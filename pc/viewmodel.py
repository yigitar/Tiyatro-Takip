"""Pure display model for the PC app: one ``Row`` per play, Turkish text.

No I/O and no GUI imports, so everything here is unit-testable.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime

from tiyatro.config import default_config
from tiyatro.models import ISTANBUL, Config, Event, Play, Target
from tiyatro.notify import DAY_BEFORE_TEXT, DAYS, KIND_TEXT, MONTHS, format_message
from tiyatro.tags import apply_overrides
from tiyatro.timeutil import parse_iso

DAY_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]  # as worker/src/menu.js
TAG_ORDER = ["çocuk", "gündüz"]
STATUS_WORDS = (("on_sale", "satışta"), ("upcoming", "yakında"), ("sold_out", "tükendi"),
                ("unknown", "belirsiz"))
APP_TITLE = "Tiyatro Takip"


@dataclass
class Row:
    slug: str
    title: str
    tags: list[str] = field(default_factory=list)
    genre: str = ""
    duration: str = ""         # "130 dk"
    age: str = ""              # "4+"
    price: str = ""            # "250 TL"
    session_count: int = 0     # upcoming (not past, not gone) sessions
    status_summary: str = ""   # "1 satışta · 2 yakında · 3 tükendi"
    next_opening: str = ""     # "16 Kasım Pazartesi 10:00"
    watched: bool = False      # active watch on a play that is not removed
    active: bool = False       # config watch is active
    target_lines: list[str] = field(default_factory=list)
    is_new: bool = False
    is_removed: bool = False
    url: str = ""
    nearest: datetime | None = None  # earliest upcoming session start (sorting)


def when_text(d: datetime) -> str:
    d = d.astimezone(ISTANBUL)
    return f"{d.day} {MONTHS[d.month - 1]} {DAYS[d.weekday()]} {d:%H:%M}"


def days_text(days: list[int] | None) -> str:
    if days is None:
        return "her gün"
    s = sorted(set(days))
    if not s:
        return "hiçbir gün"
    if s == [0, 1, 2, 3, 4]:
        return "hafta içi"
    if s == [5, 6]:
        return "hafta sonu"
    return ", ".join(DAY_SHORT[d] for d in s)


def target_summary(t: Target) -> str:
    """Same wording as ``targetSummary`` in worker/src/menu.js (without HTML)."""
    rule = t.rule
    parts = [f"{t.name or t.id}: {t.people} kişi", days_text(rule.days)]
    if rule.from_time:
        parts.append(f"{rule.from_time} sonrası")
    if rule.to_time:
        parts.append(f"{rule.to_time} öncesi")
    if rule.overrides:
        parts.append(f"{len(rule.overrides)} gün istisnası")
    if rule.exclude_dates:
        parts.append(f"{len(rule.exclude_dates)} hariç tarih")
    return " · ".join(parts)


def price_text(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    if float(value).is_integer():
        return f"{int(value)} TL"
    return f"{value:.2f}".replace(".", ",") + " TL"


def _dt(value) -> datetime | None:
    try:
        return parse_iso(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _tags(raw, watch) -> list[str]:
    tags = apply_overrides({str(t) for t in raw or []}, watch)
    return ([t for t in TAG_ORDER if t in tags]
            + sorted(t for t in tags if t not in TAG_ORDER))


def _row(slug: str, entry: dict, config: Config, seen: set[str], removed: set[str],
         now: datetime) -> Row:
    watch = config.plays.get(slug)
    active = bool(watch is not None and watch.active)
    is_removed = slug in removed or bool(entry.get("removed"))
    watched = active and not entry.get("removed")
    counts = {key: 0 for key, _ in STATUS_WORDS}
    nearest = opening = None
    upcoming = 0
    for rec in (entry.get("sessions") or {}).values():
        if not isinstance(rec, dict) or rec.get("gone"):
            continue
        start = _dt(rec.get("start"))
        if start is None or start < now:
            continue
        upcoming += 1
        status = rec.get("status")
        counts[status if status in counts else "unknown"] += 1
        nearest = start if nearest is None or start < nearest else nearest
        opens = _dt(rec.get("opens_at"))
        if status == "upcoming" and opens is not None and opens > now:
            opening = opens if opening is None or opens < opening else opening
    summary = " · ".join(f"{counts[k]} {word}" for k, word in STATUS_WORDS if counts[k])
    lines = [target_summary(t) for t in (watch.targets if watch is not None else [])]
    if watched and not lines:
        lines = ["hedef yok"]
    duration = entry.get("duration_min")
    age = entry.get("age_limit")
    return Row(
        slug=slug, title=str(entry.get("title") or slug),
        tags=_tags(entry.get("tags"), watch),
        genre=str(entry.get("genre") or ""),
        duration=f"{duration} dk" if isinstance(duration, int) and duration > 0 else "",
        age=f"{age}+" if isinstance(age, int) and not isinstance(age, bool) else "",
        price=price_text(entry.get("price_from")),
        session_count=upcoming, status_summary=summary or "seans yok",
        next_opening=when_text(opening) if opening else "",
        watched=watched, active=active, target_lines=lines,
        is_new=slug not in seen, is_removed=is_removed,
        url=str(entry.get("url") or ""), nearest=nearest)


def build_rows(state: dict, config: Config, seen_slugs, removed_slugs,
               now: datetime) -> list[Row]:
    """Rows for every play in ``state`` plus watched plays not scanned yet.

    ``seen_slugs``: plays the user already knew before the last refresh (anything else
    is ``is_new``); ``removed_slugs``: plays that disappeared in the last refresh.
    Archived plays (all sessions past) are left out unless watched or just removed.
    Order: watched first, then nearest upcoming session, then title.
    """
    seen, removed = set(seen_slugs or ()), set(removed_slugs or ())
    plays = (state or {}).get("plays") or {}
    rows = []
    for slug in sorted(set(plays) | set(config.plays)):
        entry = plays.get(slug) if isinstance(plays.get(slug), dict) else {}
        row = _row(slug, entry, config, seen, removed, now)
        if entry.get("archived") and not row.watched and not row.is_removed:
            continue
        rows.append(row)
    far = datetime.max.replace(tzinfo=ISTANBUL)
    rows.sort(key=lambda r: (not r.watched, r.nearest or far, r.title.casefold()))
    return rows


_TAG = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    return html.unescape(_TAG.sub("", text))


def headline(event: Event) -> str:
    if event.kind == "DAY_BEFORE":
        return DAY_BEFORE_TEXT.strip(" :")
    if event.kind == "DELIVERY_DROPPED":
        return "Bildirim iletilemedi"
    return KIND_TEXT.get(event.kind, event.kind)


def format_event_for_desktop(event: Event, plays: dict[str, Play] | None = None,
                             config: Config | None = None) -> tuple[str, str]:
    """(title, text) for a desktop notification: the Telegram text without HTML."""
    parts = format_message([event], plays or {}, config or default_config())
    return f"{APP_TITLE} — {headline(event)}", _plain("\n\n".join(parts))


def format_events_for_desktop(events: list[Event], plays: dict[str, Play] | None = None,
                              config: Config | None = None) -> tuple[str, str]:
    """One notification for several events (e.g. many new plays at once)."""
    if len(events) == 1:
        return format_event_for_desktop(events[0], plays, config)
    parts = format_message(list(events), plays or {}, config or default_config())
    kinds = {headline(e) for e in events}
    title = f"{APP_TITLE} — {kinds.pop()}" if len(kinds) == 1 else \
        f"{APP_TITLE} — {len(events)} bildirim"
    return title, _plain("\n\n".join(parts))
