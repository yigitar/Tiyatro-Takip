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
from pc.viewmodel import DAY_SHORT, target_summary, when_text
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
                                         when_text(opens) if show else "")))
    found.sort(key=lambda x: x[0])
    return [line for _s, line in found]


def row_values(row) -> tuple[str, ...]:
    title = row.title
    if row.is_removed:
        title = f"KALKTI · {title}"
    elif row.is_new:
        title = f"YENİ · {title}"
    if row.watched:
        follow = "Takipte"
    elif row.active:
        follow = "—"
    else:
        follow = "Pasif" if row.target_lines else "—"
    return (title, ", ".join(row.tags), row.genre, row.duration, row.age, row.price,
            str(row.session_count), row.status_summary, follow)


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


def scrub_secrets(text: str, secrets=()) -> str:
    """Replace known secret values and token-shaped strings with ``***``."""
    for secret in sorted((s for s in secrets if s and len(s) >= 6), key=len, reverse=True):
        text = text.replace(secret, "***")
    for pattern in _SECRET_SHAPES:
        text = pattern.sub("***", text)
    return _BEARER.sub(r"\1***", text)


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
    ui = {"geometry": None, "beep": True}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ui
    if isinstance(data, dict):
        if isinstance(data.get("geometry"), str) and _GEOM.match(data["geometry"]):
            ui["geometry"] = data["geometry"]
        if isinstance(data.get("beep"), bool):
            ui["beep"] = data["beep"]
    return ui


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

class SingleInstance:
    """Holds a loopback TCP port for the life of the process; the OS frees it on exit
    (also after a crash), so there is no stale lock file to clean up."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self._sock: socket.socket | None = None

    def acquire(self) -> bool:
        if self._sock is not None:
            return True
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
        if self._sock is not None:
            self._sock.close()
            self._sock = None
