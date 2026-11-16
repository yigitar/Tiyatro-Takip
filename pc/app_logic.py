"""Pure helpers for the Tk window (no tkinter import): text, validation, edit plans.

Everything the UI shows or decides lives here so it can be unit-tested without a display:
target/session/status text, day sets, time validation, the batch plan applied when the
target editor is saved, popup/beep policy, secret scrubbing, ``ui.json`` and the
single-instance socket guard.
"""
from __future__ import annotations

import copy
import json
import os
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pc import configedit
from pc.viewmodel import DAY_SHORT, NO_TARGET_TEXT, target_summary, when_text  # noqa: F401
from tiyatro.notify import DAYS, MONTHS
from tiyatro.config import MAX_PEOPLE, MIN_PEOPLE, config_from_dict
from tiyatro.models import ISTANBUL, Target
from tiyatro.timeutil import parse_iso

GREEN, AMBER, RED, GRAY = "#1a7f37", "#b36b00", "#c62828", "#6b7280"
POPUP_MAX_CHARS = 1500
BEEP_INTERVAL_S = 20
BEEP_MAX = 5
DEFAULT_PORT = 47615
MIN_WINDOW = (400, 300)
SCREEN_FRACTION = 0.92        # default window = this share of the screen width and height
DEFAULT_MIN_WINDOW = (1000, 650)  # the default never goes below this (unless the screen is smaller)
GEOMETRY_VERSION = 2          # bump to discard every previously saved window size once
ROW_PX, HEADING_PX = 22, 26   # Treeview row / heading height (default theme)


def default_geometry(screen_w: int, screen_h: int) -> str:
    """"WxH+X+Y": ~92 % of the screen (never below ``DEFAULT_MIN_WINDOW``, never above the
    screen), centred a little above the middle (taskbar)."""
    w = min(screen_w, max(DEFAULT_MIN_WINDOW[0], int(screen_w * SCREEN_FRACTION)))
    h = min(screen_h, max(DEFAULT_MIN_WINDOW[1], int(screen_h * SCREEN_FRACTION)))
    x = max(0, (screen_w - w) // 2)
    y = max(0, (screen_h - h) // 2 - 20)
    return f"{w}x{h}+{x}+{y}"


# Wave 12: the list gets at least this share of the main area by default (the detail pane
# below it gets the rest, or less when it needs less); with no row selected the detail
# placeholder is the remaining share, so selecting a row does not make the divider jump.
LIST_SHARE = 0.55


def list_pane_px(row_count: int, pane_px: int, detail_need: int | None = None, *,
                 row_px: int = ROW_PX, head_px: int = HEADING_PX) -> int:
    """Divider position (= list height). The list wants its rows (``head + rows``); it gets
    at least ``LIST_SHARE`` of the pane, more when the detail needs less than the rest
    (``detail_need`` px; unknown = the rest). Never fewer than 3 rows; the detail pane
    scrolls when it does not fit in what is left."""
    wanted = head_px + max(row_count, 3) * row_px + 6
    floor = head_px + 3 * row_px + 6
    share = int(pane_px * LIST_SHARE)
    ceiling = pane_px - detail_need if detail_need else share
    return int(max(floor, min(wanted, max(ceiling, share))))


def source_views_with_portals(views: list[dict], portals) -> list[dict]:
    """Merge the reservation-portal info into the Başiskele source row (wave 5): the
    source dict gets ``portal_text`` ("rezervasyon portalı: boş") and ``portal_url``;
    a portal without a source row gets a minimal one."""
    out = [dict(v) for v in views]
    for pv in portals or ():
        word = {"empty": "boş", "unknown": "henüz kontrol edilmedi"}.get(pv.state)
        if word is None:
            first = pv.text.split(": ", 1)[-1].split(" · ")[0]
            word = first + (" (okunamadı)" if pv.state == "failing" else "")
        view = next((v for v in out if v["id"] == pv.id), None)
        if view is None:
            view = {"id": pv.id, "name": pv.text.split(": ", 1)[0].lstrip("🏛️ ").strip(),
                    "status": "unknown", "icon": "⚪", "last_check": "—",
                    "last_change": "—", "items_count": None, "error": None}
            out.append(view)
        view["portal_text"] = f"rezervasyon portalı: {word}"
        view["portal_url"] = pv.url
    return out


def source_line(view: dict) -> str:
    from tiyatro import sources as S
    text = S.line(view)
    return f"{text} · {view['portal_text']}" if view.get("portal_text") else text


def _ago(then: datetime, now: datetime) -> str:
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 1:
        return "az önce"
    if minutes < 60:
        return f"{minutes} dk önce"
    if minutes < 24 * 60:
        return f"{minutes // 60} sa önce"
    return f"{minutes // (24 * 60)} gün önce"


def _day_time(then: datetime, now: datetime) -> str:
    """"bugün 12:10" / "dün 12:10" / "13.11 12:10" (Istanbul time)."""
    then, today = then.astimezone(ISTANBUL), now.astimezone(ISTANBUL).date()
    delta = (today - then.date()).days
    day = "bugün" if delta == 0 else "dün" if delta == 1 else f"{then:%d.%m}"
    return f"{day} {then:%H:%M}"


@dataclass(frozen=True)
class SourceCard:
    id: str
    title: str
    status: str            # ok | stale | failed | unknown
    icon: str
    color: str
    lines: tuple[str, ...]  # Son kontrol / Son güncelleme / N etkinlik [/ portal]
    error: str = ""


CARD_COLORS = {"ok": GREEN, "stale": AMBER, "failed": RED, "unknown": GRAY}


def source_card(view: dict, now: datetime) -> SourceCard:
    """Text of one source card: "Son kontrol: 14:35 (3 dk önce)", "Son güncelleme: bugün
    12:10", "6 etkinlik", Başiskele also "Rezervasyon portalı: boş". Unknown values are "—"."""
    def stamp(key: str, fallback: str, with_ago: bool) -> str:
        iso = view.get(key)
        try:
            then = parse_iso(iso) if isinstance(iso, str) else None
        except ValueError:
            then = None
        if then is None:
            return fallback or BLANK
        hhmm = f"{then.astimezone(ISTANBUL):%H:%M}"
        if with_ago:
            clock = hhmm if then.astimezone(ISTANBUL).date() == now.astimezone(ISTANBUL).date()                 else _day_time(then, now)
            return f"{clock} ({_ago(then, now)})"
        return _day_time(then, now)

    count = view.get("items_count")
    lines = [f"Son kontrol: {stamp('last_check_at', view.get('last_check'), True)}",
             f"Son güncelleme: {stamp('last_change_at', view.get('last_change'), False)}",
             f"{count} etkinlik" if isinstance(count, int) else f"{BLANK} etkinlik"]
    if view.get("warning"):
        lines.append(f"⚠️ {view['warning']}")
    if view.get("portal_text"):
        text = view["portal_text"]
        lines.append(text[:1].upper() + text[1:])
    status = view.get("status") if view.get("status") in CARD_COLORS else "unknown"
    error = view.get("error") if status == "failed" and view.get("error") else ""
    return SourceCard(view["id"], str(view.get("name") or view["id"]), status,
                      str(view.get("icon") or "⚪"), CARD_COLORS[status], tuple(lines), error)


def source_links(config, state) -> dict[str, str]:
    """Page the "Kaynağı aç" button of each source opens (wave 6): Biletinial = the venue
    page of the config (first source), Başiskele = the newest known monthly program article
    (else the municipality home page), VKV = the Etkinlikler list."""
    from tiyatro import srcevents as E
    links: dict[str, str] = {}
    sources = list(getattr(config, "sources", None) or [])
    if sources and _http(sources[0]):
        links["biletinial"] = sources[0]
    articles = (((state or {}).get("src_cache") or {}).get("basiskele") or {}).get("articles")
    newest = ""
    if isinstance(articles, dict):
        ranked = sorted((str((r or {}).get("pub") or ""), link) for link, r in articles.items()
                        if isinstance(r, dict) and _http(link))
        newest = ranked[-1][1] if ranked else ""
    links["basiskele"] = newest or E.BASISKELE_BASE
    links["vkv"] = E.VKV_LIST_URL
    return links


QUICK_DAYS = {"Hafta içi": [0, 1, 2, 3, 4], "Hafta sonu": [5, 6],
              "Hepsi": [0, 1, 2, 3, 4, 5, 6]}
_TIME = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?$")


# ---- time / days -----------------------------------------------------------------------

def parse_time(text, label: str = "saat") -> str | None:
    """Empty -> None (no limit); "18", "9:05", "18.30" -> "HH:MM"; else ValueError."""
    if text is None or not str(text).strip():
        return None
    m = _TIME.match(str(text).strip())
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if hour <= 23 and minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    raise ValueError(f"{label}: geçersiz saat {str(text).strip()!r} (SS:DD yazın, boş = sınırsız)")


def days_to_flags(days) -> list[bool]:
    chosen = set(range(7) if days is None else days)
    return [d in chosen for d in range(7)]


def flags_to_days(flags) -> list[int] | None:
    days = [d for d, on in enumerate(flags) if on]
    return None if len(days) == 7 else days


def target_summary_text(target: Target) -> str:
    return target_summary(target)


# ---- target editor: plan -> dry run -> apply ---------------------------------------------

def _hours(lo_text, hi_text, label: str) -> tuple[str | None, str | None]:
    lo = parse_time(lo_text, f"{label} başlangıç")
    hi = parse_time(hi_text, f"{label} bitiş")
    if lo and hi and lo > hi:
        raise ValueError(f"{label}: başlangıç saati bitişten sonra olamaz ({lo} > {hi})")
    return lo, hi


def build_edit_plan(slug: str, tid: str, *, people, days, from_text, to_text, overrides,
                    excluded, dates) -> list[tuple[str, tuple]]:
    """Ordered ``(Service method, args)`` list for one editor save; ValueError on bad input.

    ``overrides``: weekday -> (from_text, to_text); every weekday is written (blank row
    removes its override). ``dates``: the checklist dates; those in ``excluded`` are
    excluded, the rest included again.
    """
    if isinstance(people, bool) or not isinstance(people, int) \
            or not MIN_PEOPLE <= people <= MAX_PEOPLE:
        raise ValueError(f"kişi sayısı {MIN_PEOPLE}-{MAX_PEOPLE} arasında olmalı")
    if days is not None:
        days = sorted(set(days))
        if not days:
            raise ValueError("en az bir gün seçin")
        if len(days) == 7:
            days = None
    lo, hi = _hours(from_text, to_text, "Genel saat")
    plan: list[tuple[str, tuple]] = [
        ("set_people", (slug, tid, people)),
        ("set_days", (slug, tid, days)),
        ("set_hours", (slug, tid, lo, hi)),
    ]
    for wd in range(7):
        a, b = (overrides or {}).get(wd, ("", ""))
        olo, ohi = _hours(a, b, f"{DAY_SHORT[wd]} özel saat")
        plan.append(("set_day_override", (slug, tid, wd, olo, ohi)))
    for iso in sorted(set(dates)):
        plan.append(("set_excluded_date", (slug, tid, iso, iso in excluded)))
    return plan


def dry_run(plan, raw_cfg: dict) -> dict:
    """Apply ``plan`` to a copy of ``raw_cfg`` and validate the result (nothing written)."""
    cfg = copy.deepcopy(raw_cfg)
    for name, args in plan:
        getattr(configedit, name)(cfg, *args)
    try:
        config_from_dict(cfg)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f"geçersiz ayar: {exc}") from None
    return cfg


def apply_plan(service, plan) -> None:
    for name, args in plan:
        getattr(service, name)(*args)


def _istanbul(value):
    try:
        d = parse_iso(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return d.astimezone(ISTANBUL) if d else None


def date_label(iso: str) -> str:
    """"2026-11-20" -> "20 Kasım Cuma" (checklist label in the target editor)."""
    d = datetime.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {DAYS[d.weekday()]}"


def editor_dates(entry, existing_excludes, now: datetime) -> list[str]:
    """Session dates of the play (upcoming, not gone) plus already excluded valid dates."""
    out: set[str] = set()
    for rec in ((entry or {}).get("sessions") or {}).values():
        if not isinstance(rec, dict) or rec.get("gone"):
            continue
        start = _istanbul(rec.get("start"))
        if start is not None and start >= now:
            out.add(start.date().isoformat())
    for d in existing_excludes or []:
        try:
            out.add(configedit.check_date(d))
        except ValueError:
            pass
    return sorted(out)


# ---- sessions / rows ----------------------------------------------------------------------

_STATUS = {"on_sale": ("satışta", "●"), "upcoming": ("yakında", "◷"),
           "sold_out": ("tükendi", "✕"), "unknown": ("belirsiz", "?")}


@dataclass
class SessionLine:
    when: str
    status: str
    icon: str
    opens: str = ""
    url: str = ""        # the session's own page (Biletinial seance link); "" = none stored


def _http(value) -> str:
    return value.strip() if isinstance(value, str) and value.strip().lower().startswith(
        ("http://", "https://")) else ""


def session_link(line: SessionLine, play_url: str) -> tuple[str, str, str]:
    """(button text, url, note) of one session row: its own page when the state has one,
    otherwise the play page (note says so); ("", "", "") when there is no page at all."""
    if line.url:
        return "Seans ↗", line.url, ""
    if play_url:
        return "Oyun ↗", play_url, "seans bağlantısı yok: oyun sayfası açılır"
    return "", "", ""


def session_lines(entry, now: datetime) -> list[SessionLine]:
    found = []
    for rec in ((entry or {}).get("sessions") or {}).values():
        if not isinstance(rec, dict) or rec.get("gone"):
            continue
        start = _istanbul(rec.get("start"))
        if start is None or start < now:
            continue
        text, icon = _STATUS.get(rec.get("status"), _STATUS["unknown"])
        opens = _istanbul(rec.get("opens_at"))
        show = rec.get("status") == "upcoming" and opens is not None and opens > now
        found.append((start, SessionLine(when_text(start), text, icon,
                                         when_text(opens) if show else "",
                                         _http(rec.get("url")))))
    found.sort(key=lambda x: x[0])
    return [line for _s, line in found]


@dataclass(frozen=True)
class Cell:
    """One list cell: its text, whether the value is derived (our rule / fallback / computed
    rather than read from the source page) and how it behaves ("text", "check", "link")."""
    text: str
    derived: bool = False
    kind: str = "text"


# (key, heading, width in px); the list is one table for plays and source events (wave 6)
LIST_COLUMNS = (("follow", "Takip", 50), ("title", "Oyun", 220), ("link", "Sayfa", 60),
                ("source", "Kaynak", 105), ("audience", "Kitle", 66), ("genre", "Tür", 85),
                ("zaman", "Zaman", 95), ("duration", "Süre", 50), ("age", "Yaş", 40),
                ("price", "Fiyat", 60), ("sessions", "Seans", 270),
                ("rez", "Rez. açılış", 215), ("target", "Hedef", 125),
                ("summary", "Durum özeti", 170))
CHECKED, UNCHECKED = "☑", "☐"
LEGEND = "normal = kaynaktan alınan, italik = türetilmiş/varsayım"
# "Açıklama" box (wave 8): list styles and source states, each item shown with a real sample
LEGEND_LIST = (("Normal yazı", "= kaynaktan alınan", "normal"),
               ("İtalik", "= türetilmiş / varsayım", "derived"),
               ("Mor kalın", "= yeni, henüz açılmadı", "new"))
LEGEND_STATES = (("✅", "güncel", GREEN), ("⚠️", "30 dk'dan eski (kontrol gecikti)", AMBER),
                 ("❌", "son kontrol başarısız", RED), ("⚪", "henüz kontrol edilmedi", GRAY))
LEGEND_TIMES = ("Son kontrol = kaynağa en son bakılma · Son güncelleme = kaynağın kendi "
                "içeriğinin en son değişmesi")
BLANK = "—"


def row_title(row) -> str:
    return f"KALKTI · {row.title}" if row.is_removed else row.title


def row_cells(row) -> list[Cell]:
    """Cells in ``LIST_COLUMNS`` order. ``row.derived`` (set by the viewmodel, never guessed
    here) says which values are derived; an empty value is shown as "—" and counts as derived."""
    der = row.derived

    def text(key: str, value: str) -> Cell:
        return Cell(value or BLANK, key in der or not value)

    return [
        Cell(CHECKED if row.watched else UNCHECKED, False, "check"),
        Cell(row_title(row)),
        Cell("Oyun ↗" if row.url else BLANK, False, "link" if row.url else "text"),
        Cell(row.source),
        text("audience", row.audience), text("genre", row.genre), text("zaman", row.zaman),
        Cell(row.duration or BLANK, "duration" in getattr(row, "borrowed", ())),
        Cell(row.age or BLANK, "age" in getattr(row, "borrowed", ())), Cell(row.price or BLANK),
        Cell(row.sessions_text or BLANK, not row.sessions_text),
        text("rez", row.rez_text), text("target", row.target_text),
        Cell(row.status_summary, "summary" in der),
    ]


def row_values(row) -> tuple[str, ...]:
    return tuple(c.text for c in row_cells(row))


def meta_parts(row) -> list[Cell]:
    """Detail header segments, the same for plays and source events: Kaynak, Kitle, Tür (derived
    ones flagged), then Süre / Yaş / Ücret when known (italic when taken from the same-named
    Biletinial play). Each segment is rendered with its own style."""
    der = row.derived
    borrowed = getattr(row, "borrowed", frozenset())
    parts = [Cell(f"Kaynak: {row.source}"),
             Cell(f"Kitle: {row.audience or 'Bilgi yok'}", "audience" in der or not row.audience),
             Cell(f"Tür: {row.genre or 'Bilgi yok'}", "genre" in der or not row.genre)]
    if row.duration:
        parts.append(Cell(f"Süre: {row.duration}", "duration" in borrowed))
    if row.age:
        parts.append(Cell(f"Yaş: {row.age}", "age" in borrowed))
    if row.price:
        parts.append(Cell(f"Ücret: {row.price}"))
    return parts


def meta_line(row) -> str:
    """Detail header: "Kaynak: Biletinial  ·  Kitle: Çocuk  ·  Tür: Komedi  ·  Süre: 90 dk  ·  Yaş: 7+"."""
    return "  ·  ".join(c.text for c in meta_parts(row))


def row_tags(row) -> tuple[str, ...]:
    tags = []
    if row.is_removed:
        tags.append("removed")
    elif row.watched:
        tags.append("watched")
    if row.is_new and not row.is_removed:
        tags.append("new")
    return tuple(tags)


# ---- status ------------------------------------------------------------------------------

def ago_text(seconds) -> str:
    s = int(seconds)
    if s < 60:
        return "az önce"
    if s < 3600:
        return f"{s // 60} dk önce"
    if s < 86400:
        return f"{s // 3600} sa önce"
    return f"{s // 86400} gün önce"


@dataclass
class StatusView:
    last_scan: str = "bilinmiyor"
    mode: str = "Normal"
    next_opening: str = "yok"
    github: str = "bilinmiyor"
    github_color: str = GRAY
    heartbeat: str = "yok"
    heartbeat_color: str = GRAY
    paused: bool = False
    pending: str = "0"
    errors: list[str] = field(default_factory=list)
    warning: str = ""


def status_view(status: dict, now: datetime, refresh_failures=()) -> StatusView:
    v = StatusView()
    scan = None
    try:
        scan = parse_iso(status.get("last_scan"))
    except (ValueError, TypeError):
        pass
    if scan is not None:
        v.last_scan = scan.astimezone(ISTANBUL).strftime("%d.%m %H:%M:%S")
    v.mode = "Hızlı" if status.get("mode") == "burst" else "Normal"
    nxt = status.get("next_opening")
    if isinstance(nxt, dict) and nxt.get("text"):
        v.next_opening = f"{nxt['title']} · {nxt['text']}" if nxt.get("title") else nxt["text"]

    gh = status.get("github")
    if not isinstance(gh, dict):
        v.github = "bilinmiyor (henüz okunmadı)"
    else:
        label = "sağlıklı" if gh.get("healthy") else "sorunlu"
        v.github_color = GREEN if gh.get("healthy") else RED
        if gh.get("last_run"):
            v.github = f"{label} · son çalışma {ago_text(gh.get('gap_s') or 0)}"
            if (gh.get("lag_s") or 0) >= 60:
                v.github += f" · gecikme {int(gh['lag_s']) // 60} dk"
        else:
            v.github = f"{label} · çalışma kaydı yok"

    age = status.get("heartbeat_age_s")
    if isinstance(age, (int, float)):
        v.heartbeat = ago_text(age)
        v.heartbeat_color = GREEN if age <= 360 else AMBER
    v.paused = bool(status.get("paused"))
    v.pending = str(int(status.get("outbox_pending") or 0))
    v.errors = [str(e) for e in status.get("errors") or []]
    if refresh_failures:
        v.warning = (f"Uyarı: {len(refresh_failures)} sayfa okunamadı, "
                     "liste eksik olabilir")
    return v


# ---- popup / beeps -------------------------------------------------------------------------

def popup_text(title: str, text: str) -> tuple[str, str]:
    if len(text) > POPUP_MAX_CHARS:
        text = text[:POPUP_MAX_CHARS - 1].rstrip() + "…"
    return title, text


def should_beep(count: int, enabled: bool, dismissed: bool) -> bool:
    return bool(enabled) and not dismissed and count < BEEP_MAX


def event_url(events, row_urls: dict) -> str:
    for ev in events:
        payload = ev.payload or {}
        url = payload.get("url") or payload.get("play_url")
        if url:
            return str(url)
    for ev in events:
        if row_urls.get(ev.play_slug):
            return str(row_urls[ev.play_slug])
    return ""


# ---- secret scrubbing ----------------------------------------------------------------------

_SECRET_SHAPES = (
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\d{5,}:[A-Za-z0-9_-]{8,}"),
)
_BEARER = re.compile(r"(Bearer\s+)\S+", re.I)
_BASIC = re.compile(r"(Basic\s+)[A-Za-z0-9+/=_-]{8,}")


def scrub_secrets(text: str, secrets=()) -> str:
    """Replace known secret values and token-shaped strings with ``***``."""
    for secret in sorted((s for s in secrets if s and len(s) >= 6), key=len, reverse=True):
        text = text.replace(secret, "***")
    for pattern in _SECRET_SHAPES:
        text = pattern.sub("***", text)
    return _BASIC.sub(r"\1***", _BEARER.sub(r"\1***", text))


# ---- list sorting ------------------------------------------------------------------------

_TR_ALPHABET = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
_TR_RANK = {ch: i for i, ch in enumerate(_TR_ALPHABET)}
_TR_LOWER = {"I": "ı", "İ": "i"}
SORT_COLUMNS = tuple(key for key, _label, _w in LIST_COLUMNS)
# First click sorts "most first" (descending key) on these; every other column ascending.
_FIRST_DESC = {"summary", "follow", "target", "link"}


def tr_sort_key(text) -> tuple:
    """Collation key in Turkish alphabet order (a b c ç d … g ğ h ı i j … o ö p … s ş t u ü …).
    ``I`` is dotless (``ı``) and ``İ`` dotted (``i``); digits and punctuation sort before
    letters; case is ignored."""
    key = []
    for ch in str(text or ""):
        low = _TR_LOWER.get(ch) or ch.lower()
        for c in low.replace("i̇", "i"):
            rank = _TR_RANK.get(c)
            key.append((1, rank) if rank is not None else (0, ord(c)))
    return tuple(key)


_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)")


def _number(text) -> float | None:
    m = _NUMBER.search(str(text or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def _count(text: str, word: str) -> int:
    m = re.search(rf"(\d+) {word}", text or "")
    return int(m.group(1)) if m else 0


def _sort_value(row, column: str):
    """Comparable value of ``row`` for ``column`` or None when the row has no value."""
    if column == "title":
        return tr_sort_key(row.title) if row.title.strip() else None
    if column == "zaman":
        return tr_sort_key(row.zaman) if row.zaman else None
    if column == "link":
        return 1 if row.url else None
    if column == "rez":
        return row.rez_key
    if column == "target":
        return None if row.is_event else row.target_count
    if column == "source":
        return tr_sort_key(row.source)
    if column == "audience":
        return tr_sort_key(row.audience) if row.audience.strip() else None
    if column == "genre":
        return tr_sort_key(row.genre) if row.genre.strip() else None
    if column in ("duration", "age", "price"):
        return _number(getattr(row, column))
    if column == "sessions":   # the earliest upcoming session (date and time), not the count
        return row.nearest.timestamp() if row.nearest is not None else None
    if column == "summary":
        if not row.session_count:
            return None
        return (_count(row.status_summary, "satışta"), -_count(row.status_summary, "tükendi"))
    if column == "follow":
        return 2 if row.watched else 1 if row.target_lines else 0
    return None


def sort_rows(rows, sort) -> list:
    """Rows ordered by ``sort`` = ``(column, "asc"|"desc")``; None/unknown keeps the given
    (default) order. Stable; rows without a value always come last, in the given order."""
    rows = list(rows)
    if not sort or sort[0] not in SORT_COLUMNS or sort[1] not in ("asc", "desc"):
        return rows
    column, direction = sort
    valued, missing = [], []
    for row in rows:
        value = _sort_value(row, column)
        (missing if value is None else valued).append((value, row))
    valued.sort(key=lambda pair: pair[0], reverse=direction == "desc")
    return [r for _v, r in valued] + [r for _v, r in missing]


def next_sort(current, column: str):
    """Header click: first click (ascending, or "most first" for Seans/Durum/Takip), second
    reverses, third returns to the default order (None)."""
    first = "desc" if column in _FIRST_DESC else "asc"
    reverse = "asc" if first == "desc" else "desc"
    if not current or current[0] != column:
        return (column, first)
    return (column, reverse) if current[1] == first else None


def heading_text(label: str, column: str, sort) -> str:
    if sort and sort[0] == column:
        return f"{label} {'▲' if sort[1] == 'asc' else '▼'}"
    return label


# ---- "new" items (wave 6) ------------------------------------------------------------------

SEEN_MAX = 3000


class SeenStore:
    """Ids of list items the user has already seen, kept per machine in ``ui.json``.

    An item is *new* when its id is not in the set; it stops being new when the user selects
    its row. Rule for the first run after the upgrade (no ``seen`` key in ``ui.json``): the
    first non-empty list is taken as the baseline and every item in it counts as seen (no
    colour flood); only items that appear later are new. Before the baseline nothing is new.
    """

    def __init__(self, ids=None):
        self.initialised = ids is not None
        self.ids: dict[str, None] = dict.fromkeys(
            i for i in (ids or ()) if isinstance(i, str) and i)

    @classmethod
    def load(cls, path) -> "SeenStore":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        saved = data.get("seen") if isinstance(data, dict) else None
        return cls(saved if isinstance(saved, list) else None)

    def baseline(self, current_ids) -> bool:
        """Mark everything currently listed as seen, once; True when it happened."""
        current = [i for i in current_ids if isinstance(i, str) and i]
        if self.initialised or not current:
            return False
        self.ids.update(dict.fromkeys(current))
        self.initialised = True
        return True

    def is_new(self, item_id: str) -> bool:
        return self.initialised and item_id not in self.ids

    def mark(self, item_id: str) -> bool:
        """The user selected this row; True when that changed anything."""
        if not self.initialised or not item_id or item_id in self.ids:
            return False
        self.ids[item_id] = None
        return True

    def to_json(self) -> list[str] | None:
        if not self.initialised:
            return None
        return list(self.ids)[-SEEN_MAX:]


# ---- ui.json ---------------------------------------------------------------------------------

_GEOM = re.compile(r"^(\d{1,5})x(\d{1,5})(?:([+-]-?\d{1,5})([+-]-?\d{1,5}))?$")


def _coord(sign_and_value: str) -> int:
    return int(sign_and_value[1:]) * (1 if sign_and_value[0] == "+" else -1)


def sanitize_geometry(geometry, screen_w: int, screen_h: int) -> str | None:
    """Clamp a saved ``WxH+X+Y`` to the screen; drop a position that is off-screen."""
    m = _GEOM.match(geometry) if isinstance(geometry, str) else None
    if not m:
        return None
    w = min(max(int(m.group(1)), MIN_WINDOW[0]), screen_w)
    h = min(max(int(m.group(2)), MIN_WINDOW[1]), screen_h)
    if m.group(3) is None:
        return f"{w}x{h}"
    x, y = _coord(m.group(3)), _coord(m.group(4))
    if x + w < 100 or x + 100 > screen_w or y < -10 or y + 100 > screen_h:
        return f"{w}x{h}"
    return f"{w}x{h}+{x}+{y}"


def load_ui(path) -> dict:
    ui = {"geometry": None, "beep": True, "keep_awake": False}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ui
    if isinstance(data, dict):
        if (isinstance(data.get("geometry"), str) and _GEOM.match(data["geometry"])
                and data.get("geometry_v") == GEOMETRY_VERSION):  # older sizes: dropped once
            ui["geometry"] = data["geometry"]
        if isinstance(data.get("beep"), bool):
            ui["beep"] = data["beep"]
        if isinstance(data.get("keep_awake"), bool):
            ui["keep_awake"] = data["keep_awake"]
    return ui


def load_sort(path):
    """Saved ``(column, direction)`` of the list sort; None (default order) when the file
    is missing/corrupt or holds anything unknown."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    saved = data.get("sort") if isinstance(data, dict) else None
    if (isinstance(saved, list) and len(saved) == 2 and saved[0] in SORT_COLUMNS
            and saved[1] in ("asc", "desc")):
        return (saved[0], saved[1])
    return None


def load_columns(path) -> dict:
    """Saved list-column layout ``{"order": [key], "hidden": [key], "widths": {key: px}}``
    (all keys strings, widths positive numbers); anything else is dropped. Unknown column
    keys are filtered later against the real columns (``pc.table.reconcile_layout``)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    saved = data.get("columns") if isinstance(data, dict) else None
    if not isinstance(saved, dict):
        return {}
    out = {}
    for name in ("order", "hidden"):
        if isinstance(saved.get(name), list):
            out[name] = [k for k in saved[name] if isinstance(k, str)][:200]
    if isinstance(saved.get("widths"), dict):
        out["widths"] = {k: w for k, w in saved["widths"].items()
                         if isinstance(k, str) and isinstance(w, (int, float))
                         and not isinstance(w, bool) and w > 0}
    return out


def save_ui(path, ui: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(ui, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ---- single instance -----------------------------------------------------------------------

MUTEX_NAME = r"Local\TiyatroTakipPC"   # per Windows session; the OS frees it on exit/crash
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Only one PC app per Windows session. Windows: a named mutex (``CreateMutexW``) held
    for the life of the process; the OS releases it when the process ends, also after a
    crash or kill, so there is no stale pid/lock file. Other systems (tests on CI): a
    loopback TCP port with the same property."""

    def __init__(self, name: str = MUTEX_NAME, port: int = DEFAULT_PORT):
        self.name, self.port = name, port
        self._handle = None
        self._sock: socket.socket | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None or self._sock is not None

    def acquire(self) -> bool:
        if self.held:
            return True
        if os.name == "nt":
            return self._acquire_mutex()
        return self._acquire_port()

    def _acquire_mutex(self) -> bool:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not handle:
            return False   # cannot tell: do not start a second copy blindly
        if err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle, self._close = handle, kernel32.CloseHandle
        return True

    def _acquire_port(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", self.port))
            sock.listen(1)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        return True

    def release(self) -> None:
        if self._handle is not None:
            self._close(self._handle)
            self._handle = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
