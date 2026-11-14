"""Activity log of the PC app ("Etkinlik günlüğü"): what was checked, when, with what result.

One JSON object per line in ``pc/local/activity.jsonl`` (git-ignored)::

    {"ts": "2026-11-14T14:35:10+03:00", "cat": "Liste kontrolü", "level": "info",
     "title": "6 oyun, 17 seans (3 satışta, 1 yakında, 13 tükendi)", "detail": "değişiklik yok"}

``level`` is ``info`` / ``warn`` / ``error``. The log is bounded (``max_entries``, oldest
dropped; the file is rewritten atomically), every text is scrubbed of tokens before it
touches the disk, damaged lines are skipped, and a failing disk never raises into the
scan loop. No Tk, no network: pure and file code only (``pc.ui``/``pc.activity_ui`` show it).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from pc.app_logic import scrub_secrets
from pc.viewmodel import DAY_SHORT
from tiyatro.models import ISTANBUL
from tiyatro.timeutil import parse_iso

log = logging.getLogger("pc.activity")

CAT_SCAN = "Liste kontrolü"
CAT_PLAYS = "Oyun listesi"
CAT_TICKET = "Bilet açılışı"
CAT_WATCH = "Takip"
CAT_NOTIFY = "Bildirim"
CAT_ERROR = "Hata/Uyarı"
CAT_GITHUB = "GitHub"
CAT_APP = "Uygulama"  # start / stop lines (not a user-facing topic of their own)
CATEGORIES = (CAT_SCAN, CAT_PLAYS, CAT_TICKET, CAT_WATCH, CAT_NOTIFY, CAT_ERROR, CAT_GITHUB,
              CAT_APP)

LEVELS = ("info", "warn", "error")
_LEVEL_LABEL = {"info": "Bilgi", "warn": "Uyarı", "error": "Hata"}
DEFAULT_MAX = 3000

# Biletinial event kind -> category of the log line.
EVENT_CATEGORY = {
    "ON_SALE": CAT_TICKET, "OPENING_SOON": CAT_TICKET, "DAY_BEFORE": CAT_TICKET,
    "OPENED_SOLD_OUT": CAT_TICKET, "NEW_SESSIONS": CAT_TICKET,
    "NEW_PLAY": CAT_PLAYS, "REMOVED_PLAY": CAT_PLAYS,
}
WARNING_KINDS = {"FETCH_FAILING", "PLAY_FAILING", "UNKNOWN_STATUS", "DELIVERY_DROPPED",
                 "CONFIG_INVALID", "PC_INACTIVE", "PC_DIVERGED", "GH_LAGGING"}


def level_label(level: str) -> str:
    return _LEVEL_LABEL.get(level, "Bilgi")


def _text(value) -> str:
    return "" if value is None else str(value)


def _clean(raw) -> dict | None:
    """A well-formed entry from one decoded JSON value, or None."""
    if not isinstance(raw, dict) or not isinstance(raw.get("ts"), str) \
            or not isinstance(raw.get("cat"), str):
        return None
    level = raw.get("level")
    return {"ts": raw["ts"], "cat": raw["cat"], "level": level if level in LEVELS else "info",
            "title": _text(raw.get("title")), "detail": _text(raw.get("detail"))}


def _default_clock() -> datetime:
    return datetime.now(ISTANBUL)


class ActivityLog:
    def __init__(self, path: Path | str, max_entries: int = DEFAULT_MAX,
                 clock: Callable[[], datetime] = _default_clock,
                 secrets: Iterable[str] | None = None):
        self.path = Path(path)
        self.max_entries = max(1, int(max_entries))
        self._slack = max(1, self.max_entries // 10)  # rewrite in batches, not on every add
        self._clock = clock
        self._secrets = secrets if secrets is not None else []  # may grow after creation
        self._lock = threading.RLock()
        self._count: int | None = None  # lines in the file (lazy)
        self._version = 0

    @property
    def version(self) -> int:
        """Bumped by every add/clear: a cheap "anything new?" check for the UI."""
        return self._version

    # ---- file helpers ----------------------------------------------------------
    def _read_text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _entries(self) -> list[dict]:
        """All valid entries, oldest first."""
        out = []
        for line in self._read_text().splitlines():
            try:
                entry = _clean(json.loads(line))
            except ValueError:
                continue
            if entry is not None:
                out.append(entry)
        return out

    def _rewrite(self, entries: list[dict]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # ---- API -------------------------------------------------------------------
    def add(self, category: str, title: str, detail: str = "",
            level: str = "info") -> dict | None:
        """Append one entry; returns it (None if the disk refused: never raises)."""
        secrets = list(self._secrets)
        entry = {"ts": self._clock().isoformat(timespec="seconds"),
                 "cat": scrub_secrets(_text(category), secrets),
                 "level": level if level in LEVELS else "info",
                 "title": scrub_secrets(_text(title), secrets),
                 "detail": scrub_secrets(_text(detail), secrets)}
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                text = self._read_text() if self._count is None else None
                if text is not None:
                    self._count = sum(1 for ln in text.splitlines() if ln.strip())
                needs_newline = text is not None and bool(text) and not text.endswith("\n")
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(("\n" if needs_newline else "")
                             + json.dumps(entry, ensure_ascii=False) + "\n")
                self._count += 1
                if self._count > self.max_entries + self._slack:
                    kept = self._entries()[-self.max_entries:]
                    self._rewrite(kept)
                    self._count = len(kept)
            except OSError as exc:
                log.warning("activity log not written: %s", type(exc).__name__)
                self._count = None
                return None
            finally:
                self._version += 1
        return entry

    def load(self, limit: int | None = None) -> list[dict]:
        """Newest first, at most ``limit`` (and never more than ``max_entries``)."""
        with self._lock:
            entries = self._entries()[-self.max_entries:]
        entries.reverse()
        return entries[:limit] if limit is not None else entries

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("activity log not cleared: %s", type(exc).__name__)
            self._count = 0
            self._version += 1


# ---- filtering ---------------------------------------------------------------------

_FOLD = str.maketrans({"İ": "i", "I": "i", "ı": "i", "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g",
                       "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"})


def fold(text: str) -> str:
    """Case- and Turkish-letter-insensitive form for searching ("Işık" == "isik")."""
    return text.translate(_FOLD).lower()


def filter_entries(entries: list[dict], cats=None, min_level: str = "info",
                   text: str = "") -> list[dict]:
    """Keep the entries in ``cats`` (None = all) at ``min_level`` or worse that contain
    every word of ``text`` (category, title or detail); order is preserved."""
    floor = LEVELS.index(min_level) if min_level in LEVELS else 0
    words = fold(text or "").split()
    out = []
    for e in entries:
        if cats is not None and e["cat"] not in cats:
            continue
        if LEVELS.index(e["level"]) < floor:
            continue
        if words:
            hay = fold(f"{e['cat']} {e['title']} {e['detail']}")
            if not all(w in hay for w in words):
                continue
        out.append(e)
    return out


# ---- wording ----------------------------------------------------------------------

def _when(ts: str) -> datetime | None:
    try:
        when = parse_iso(ts)
    except (ValueError, TypeError):
        return None
    if when is None:
        return None
    return when.astimezone(ISTANBUL) if when.tzinfo else when


def when_text(ts: str, now: datetime | None = None) -> str:
    """"14:35" for today, "13.11 09:05" for another day, "?" when unreadable."""
    when = _when(ts)
    if when is None:
        return "?"
    if now is not None and when.date() != now.astimezone(ISTANBUL).date():
        return when.strftime("%d.%m %H:%M")
    return when.strftime("%H:%M")


def result_text(entry: dict) -> str:
    """The short "Sonuç": first line of the detail."""
    detail = entry.get("detail") or ""
    return detail.splitlines()[0].strip() if detail.strip() else ""


def sentence(entry: dict, now: datetime | None = None) -> str:
    """The entry as one plain Turkish sentence: "14:35 — Liste kontrolü: ..., değişiklik yok"."""
    line = f"{when_text(entry['ts'], now)} — {entry['cat']}: {entry['title']}"
    result = result_text(entry)
    return f"{line}, {result}" if result else line


# ---- persisted filter (ui.json) ----------------------------------------------------

def default_filters() -> dict:
    return {"cats": list(CATEGORIES), "level": "all", "text": ""}


def load_filters(path) -> dict:
    """Saved filter of the log tab; each damaged/unknown part falls back to its default."""
    out = default_filters()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    saved = data.get("activity") if isinstance(data, dict) else None
    if not isinstance(saved, dict):
        return out
    if isinstance(saved.get("cats"), list):
        out["cats"] = [c for c in CATEGORIES if c in saved["cats"]]
    if saved.get("level") in ("all", "warn"):
        out["level"] = saved["level"]
    if isinstance(saved.get("text"), str):
        out["text"] = saved["text"][:200]
    return out


# ---- config change wording ---------------------------------------------------------

def _days(days) -> str:
    if not days:
        return "her gün"
    return ", ".join(DAY_SHORT[d] for d in sorted(days) if isinstance(d, int) and 0 <= d < 7)


def _short_date(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}" if isinstance(iso, str) and len(iso) == 10 else str(iso)


def _hours(rule: dict) -> str:
    lo, hi = rule.get("from_time"), rule.get("to_time")
    if lo and hi:
        return f"{lo}–{hi}"
    if lo:
        return f"{lo} sonrası"
    if hi:
        return f"{hi}'e kadar"
    return "sınırsız"


def _target_changes(label: str, old: dict, new: dict) -> list[str]:
    out = []
    if old.get("people") != new.get("people"):
        out.append(f"{label}: kişi sayısı {old.get('people')} → {new.get('people')}")
    o_rule, n_rule = old.get("rule") or {}, new.get("rule") or {}
    if o_rule.get("days") != n_rule.get("days"):
        out.append(f"{label}: günler {_days(o_rule.get('days'))} → {_days(n_rule.get('days'))}")
    if (o_rule.get("from_time"), o_rule.get("to_time")) != \
            (n_rule.get("from_time"), n_rule.get("to_time")):
        out.append(f"{label}: saat {_hours(n_rule)}")
    if o_rule.get("overrides") != n_rule.get("overrides"):
        out.append(f"{label}: gün bazlı özel saatler değişti")
    o_ex, n_ex = set(o_rule.get("exclude_dates") or []), set(n_rule.get("exclude_dates") or [])
    if n_ex - o_ex:
        out.append(f"{label}: hariç tutulan tarih eklendi: "
                   + ", ".join(_short_date(d) for d in sorted(n_ex - o_ex)))
    if o_ex - n_ex:
        out.append(f"{label}: hariç tutulan tarih kaldırıldı: "
                   + ", ".join(_short_date(d) for d in sorted(o_ex - n_ex)))
    return out


def describe_config_diff(old, new, titles: dict | None = None) -> list[str]:
    """Readable sentences for what changed between two raw ``config.json`` dicts."""
    titles = titles or {}
    try:
        return _describe(old if isinstance(old, dict) else {},
                         new if isinstance(new, dict) else {}, titles)
    except (AttributeError, TypeError, KeyError):
        return []


def _describe(old: dict, new: dict, titles: dict) -> list[str]:
    out: list[str] = []
    if "paused" in old or "paused" in new:
        if bool(old.get("paused")) != bool(new.get("paused")):
            out.append("Tüm bildirimler duraklatıldı" if new.get("paused")
                       else "Tüm bildirimler yeniden açıldı")
    o_plays, n_plays = old.get("plays"), new.get("plays")
    o_plays = o_plays if isinstance(o_plays, dict) else {}
    n_plays = n_plays if isinstance(n_plays, dict) else {}
    for slug in sorted(set(o_plays) | set(n_plays)):
        name = titles.get(slug) or slug
        before, after = o_plays.get(slug), n_plays.get(slug)
        if after is None:
            out.append(f"{name} takipten çıkarıldı")
            continue
        if before is None:
            out.append(f"{name} takibe alındı" if after.get("active", True)
                       else f"{name} listeye eklendi (takip kapalı)")
            before = {"active": after.get("active", True), "targets": [],
                      "tag_overrides": after.get("tag_overrides")}
        elif bool(before.get("active")) != bool(after.get("active")):
            out.append(f"{name} takibi açıldı" if after.get("active")
                       else f"{name} takibi duraklatıldı")
        old_t = {t["id"]: t for t in before.get("targets") or []}
        new_t = {t["id"]: t for t in after.get("targets") or []}
        for tid, target in new_t.items():
            label = f"{name} / {target.get('name') or tid}"
            if tid not in old_t:
                out.append(f"{name}: {target.get('name') or tid} hedefi eklendi "
                           f"({target.get('people')} kişi)")
            else:
                out.extend(_target_changes(label, old_t[tid], target))
        for tid, target in old_t.items():
            if tid not in new_t:
                out.append(f"{name}: {target.get('name') or tid} hedefi silindi")
        if (before.get("tag_overrides") or {}) != (after.get("tag_overrides") or {}):
            out.append(f"{name}: etiketler değişti")
    if old.get("sources") != new.get("sources") and ("sources" in old or "sources" in new):
        out.append("Kaynak (liste sayfası) ayarı değişti")
    return out
