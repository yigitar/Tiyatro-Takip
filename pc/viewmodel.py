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
from tiyatro.notify import (DAY_BEFORE_TEXT, DAYS, KIND_TEXT, MONTHS, SRC_TEXT, format_message,
                            portal_headline, portal_label, source_headline)
from tiyatro import srcevents
from tiyatro.tags import (apply_overrides, audience_is_stated, derived_tags, genre_is_stated,
                          resolve_audience, resolve_genre, time_of_day)
from tiyatro.timeutil import parse_iso

DAY_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]  # as worker/src/menu.js
TAG_ORDER = ["çocuk", "gündüz"]
STATUS_WORDS = (("on_sale", "satışta"), ("upcoming", "yakında"), ("sold_out", "tükendi"),
                ("unknown", "belirsiz"))
APP_TITLE = "Tiyatro Takip"
NO_TARGET_TEXT = "ℹ️ hedef yok → tüm seanslar izleniyor"  # as worker/src/menu.js
NO_TARGET_SHORT = "yok → tüm seanslar"  # "Hedef" column of a followed-or-not play without targets


@dataclass
class Row:
    slug: str
    title: str
    tags: list[str] = field(default_factory=list)
    audience: str = ""         # Kitle: "Yetişkin" / "Çocuk" / "" (unknown)
    genre: str = ""            # Tür: "Komedi", "Dram"… (never the audience)
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
    summary: str = ""          # plot description ("" = unknown)
    cast: list[str] = field(default_factory=list)  # actors in the page's own order
    credits: list[tuple[str, str]] = field(default_factory=list)  # ("Yönetmen", "Ad Soyad")
    source: str = "Biletinial"  # Kaynak: Biletinial / Başiskele / VKV
    is_event: bool = False     # a Başiskele / VKV event (slug = event id), not a Biletinial play
    seans: str = ""            # event rows: date/time of the event (the "Seans" column)
    venue: str = ""
    reservation_opens: str = ""  # event rows: "19 Kasım Perşembe 12:00" or ""
    contact: list[str] = field(default_factory=list)  # VKV: phone / e-mail lines
    event_full: bool = False
    # ---- wave 6 -----------------------------------------------------------------------
    zaman: str = ""            # "gündüz" / "gece" / "gündüz+gece" (computed from session times)
    target_count: int = 0      # explicit targets of the play (events: always 0)
    target_text: str = ""      # "var (2)" / "yok → tüm seanslar" / "—" (events)
    session_starts: list[datetime] = field(default_factory=list)  # upcoming session starts, sorted
    sessions_text: str = ""    # "3 seans: 28 Kas 15:00, 28 Kas 20:00, +1"
    rez_text: str = ""         # "Rez. açılış": "16 Kas Pzt 10:00 (yarın)" / "açık" / "tükendi" / "—"
    rez_key: tuple | None = None  # sort key: (0, 0) açık first, then (1, opening timestamp)
    derived: frozenset = frozenset()  # list columns whose value is derived, not read (italic)


def short_when(d: datetime) -> str:
    """"28 Kas 15:00" (compact session label of the Seans column)."""
    d = d.astimezone(ISTANBUL)
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d:%H:%M}"


def relative_day(d: datetime, now: datetime) -> str:
    """"bugün" / "yarın" / "3 gün sonra" / "geçti" by calendar day in Istanbul time."""
    diff = (d.astimezone(ISTANBUL).date() - now.astimezone(ISTANBUL).date()).days
    if diff < 0:
        return "geçti"
    return {0: "bugün", 1: "yarın"}.get(diff, f"{diff} gün sonra")


def rez_when(d: datetime, now: datetime) -> str:
    """"16 Kas Pzt 10:00 (yarın)"."""
    local = d.astimezone(ISTANBUL)
    return (f"{local.day} {MONTHS[local.month - 1][:3]} {DAY_SHORT[local.weekday()]} "
            f"{local:%H:%M} ({relative_day(d, now)})")


def sessions_summary(starts: list[datetime], shown: int = 2) -> str:
    """"3 seans: 28 Kas 15:00, 28 Kas 20:00, +1" (the first ``shown`` sessions in time order,
    then "+N"); "" without sessions."""
    ordered = sorted(starts)
    if not ordered:
        return ""
    names = [short_when(d) for d in ordered[:shown]]
    if len(ordered) > shown:
        names.append(f"+{len(ordered) - shown}")
    return f"{len(ordered)} seans: " + ", ".join(names)


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


# Turkish dative suffix of a clock time as read aloud ("20:00" -> "yirmi" -> 'ye);
# minute 0 reads the hour. Same table as untilText in worker/src/menu.js.
_UNIT_SUFFIX = {1: "e", 2: "ye", 3: "e", 4: "e", 5: "e", 6: "ya", 7: "ye", 8: "e", 9: "a"}
_TENS_SUFFIX = {0: "a", 1: "a", 2: "ye", 3: "a", 4: "a", 5: "ye"}


def until_text(hhmm: str) -> str:
    """'20:00' -> "20:00'ye kadar" (inclusive upper bound)."""
    try:
        hh, mm = (int(x) for x in hhmm.split(":"))
        n = mm or hh
        suffix = _UNIT_SUFFIX[n % 10] if n % 10 else _TENS_SUFFIX[n // 10]
    except (ValueError, KeyError, AttributeError):
        suffix = "e"
    return f"{hhmm}'{suffix} kadar"


def target_summary(t: Target) -> str:
    """Same wording as ``targetSummary`` in worker/src/menu.js (without HTML)."""
    rule = t.rule
    parts = [f"{t.name or t.id}: {t.people} kişi", days_text(rule.days)]
    if rule.from_time:
        parts.append(f"{rule.from_time} sonrası")
    if rule.to_time:
        parts.append(until_text(rule.to_time))
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


def _details(entry: dict) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Summary / cast / credits of a stored play; wrong types (hand-edited state) -> empty."""
    summary = entry.get("summary")
    cast = entry.get("cast")
    credits = entry.get("credits")
    return (summary.strip() if isinstance(summary, str) else "",
            [c.strip() for c in cast if isinstance(c, str) and c.strip()]
            if isinstance(cast, list) else [],
            [(k, v.strip()) for k, v in credits.items()
             if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()]
            if isinstance(credits, dict) else [])


def credit_lines(credits: list[tuple[str, str]], per_line: int = 2) -> list[str]:
    """Compact crew lines: "Yazan: A  ·  Yönetmen: B" (``per_line`` entries per line)."""
    items = [f"{k}: {v}" for k, v in credits]
    return ["  ·  ".join(items[i:i + per_line]) for i in range(0, len(items), per_line)]


def _row(slug: str, entry: dict, config: Config, seen: set[str], removed: set[str],
         now: datetime) -> Row:
    watch = config.plays.get(slug)
    active = bool(watch is not None and watch.active)
    is_removed = slug in removed or bool(entry.get("removed"))
    watched = active and not entry.get("removed")
    counts = {key: 0 for key, _ in STATUS_WORDS}
    nearest = opening = None
    upcoming = 0
    starts: list[datetime] = []
    for rec in (entry.get("sessions") or {}).values():
        if not isinstance(rec, dict) or rec.get("gone"):
            continue
        start = _dt(rec.get("start"))
        if start is None or start < now:
            continue
        upcoming += 1
        starts.append(start)
        status = rec.get("status")
        counts[status if status in counts else "unknown"] += 1
        nearest = start if nearest is None or start < nearest else nearest
        opens = _dt(rec.get("opens_at"))
        if status == "upcoming" and opens is not None and opens > now:
            opening = opens if opening is None or opens < opening else opening
    summary = " · ".join(f"{counts[k]} {word}" for k, word in STATUS_WORDS if counts[k])
    lines = [target_summary(t) for t in (watch.targets if watch is not None else [])]
    if active and not lines:
        lines = [NO_TARGET_TEXT]
    duration = entry.get("duration_min")
    age = entry.get("age_limit")
    summary_text, cast, credits = _details(entry)
    tags = _tags(entry.get("tags"), watch)
    audience = resolve_audience(entry.get("genre"), entry.get("tags") or (),
                                age if isinstance(age, int) and not isinstance(age, bool)
                                else None)
    starts.sort()
    explicit = len(watch.targets) if watch is not None else 0
    rez_text, rez_key, rez_derived = _play_rez(counts, opening, upcoming, now)
    derived = {"zaman"} if starts else set()
    if not audience_is_stated(entry.get("genre")):
        derived.add("audience")
    if not genre_is_stated(entry.get("genre")):
        derived.add("genre")
    if not explicit:
        derived.add("target")
    if rez_derived:
        derived.add("rez")
    return Row(
        slug=slug, title=str(entry.get("title") or slug),
        tags=derived_tags(tags, audience) if not tags else tags,
        audience=audience,
        genre=resolve_genre(entry.get("genre"), entry.get("title"), summary_text),
        duration=f"{duration} dk" if isinstance(duration, int) and duration > 0 else "",
        age=f"{age}+" if isinstance(age, int) and not isinstance(age, bool) else "",
        price=price_text(entry.get("price_from")),
        session_count=upcoming, status_summary=summary or "seans yok",
        next_opening=when_text(opening) if opening else "",
        watched=watched, active=active, target_lines=lines,
        is_new=slug not in seen, is_removed=is_removed,
        url=str(entry.get("url") or ""), nearest=nearest,
        summary=summary_text, cast=cast, credits=credits,
        zaman=time_of_day([d.astimezone(ISTANBUL).hour for d in starts]),
        target_count=explicit, target_text=f"var ({explicit})" if explicit else NO_TARGET_SHORT,
        session_starts=starts, sessions_text=sessions_summary(starts),
        rez_text=rez_text, rez_key=rez_key, derived=frozenset(derived))


def _play_rez(counts: dict, opening: datetime | None, upcoming: int, now: datetime):
    """("Rez. açılış" text, sort key, derived?) of a play. Ladder: earliest opening of an
    UPCOMING ("yakında") session; else "açık" when a session is ON_SALE; else "tükendi"
    when every upcoming session is sold out; else "—" (derived: nothing to read)."""
    if opening is not None:
        return rez_when(opening, now), (1, opening.timestamp()), False
    if counts.get("on_sale"):
        return "açık", (0, 0.0), False
    if upcoming and counts.get("sold_out") == upcoming:
        return "tükendi", None, False
    return "—", None, True


def _event_rez(view: dict, now: datetime):
    """("Rez. açılış" text, sort key, derived?) of a source event: "doldu" when the page
    says it is full, the announced opening time, "açık" / "açılmış olabilir" (our reading
    of a passed opening time, derived) or "duyuru bekleniyor" (nothing announced)."""
    opens = view.get("opens_at")
    if view.get("full"):
        return "doldu", None, False
    if opens is None:
        return "duyuru bekleniyor", None, True
    if now < opens:
        return rez_when(opens, now), (1, opens.timestamp()), False
    if view.get("opens_pending") is False:
        return "açık", (0, 0.0), True
    return "açılmış olabilir", (0, 0.0), True


def event_row(view: dict, now: datetime | None = None) -> Row:
    """A Başiskele / VKV event as a list row (``srcevents.event_views`` item)."""
    now = now or datetime.now(ISTANBUL)
    start = view.get("start")
    local = start.astimezone(ISTANBUL) if start else None
    opens = view.get("opens_at")
    audience = view["audience"] or resolve_audience(view["kind"], (), None, view["title"])
    tags = derived_tags([], audience, local.hour if local else None)
    contact = list(srcevents.VKV_CONTACT_LINES) if view["source"] == srcevents.VKV else []
    rez_text, rez_key, rez_derived = _event_rez(view, now)
    derived: set[str] = {"target"}   # "—": targets do not exist for source events
    if local:
        derived.add("zaman")
    if not audience_is_stated(view["kind"]):
        derived.add("audience")
    if not genre_is_stated(view["kind"]):
        derived.add("genre")
    if rez_derived:
        derived.update({"rez", "summary"})
    return Row(
        slug=view["id"], title=view["title"], tags=tags, audience=audience,
        genre=view["genre"],
        session_count=1, status_summary=view["summary"],
        watched=view["followed"], active=view["followed"],
        url=view["url"], nearest=start, source=view["source_name"], is_event=True,
        seans=when_text(start) if start else "", venue=view["venue"],
        reservation_opens=when_text(opens) if opens else "", contact=contact,
        event_full=bool(view["full"]),
        zaman=time_of_day([local.hour]) if local else "",
        target_count=0, target_text="—",
        session_starts=[start] if start else [], sessions_text=sessions_summary(
            [start] if start else []),
        rez_text=rez_text, rez_key=rez_key, derived=frozenset(derived))


def with_events(rows: list[Row], views: list[dict], now: datetime | None = None) -> list[Row]:
    """Plays + source events in one list: nearest upcoming start, then title (never by follow state: toggling Takip must not move a row)."""
    merged = list(rows) + [event_row(v, now) for v in views]
    far = datetime.max.replace(tzinfo=ISTANBUL)
    merged.sort(key=lambda r: (r.nearest or far, r.title.casefold(), r.slug))
    return merged


def build_rows(state: dict, config: Config, seen_slugs, removed_slugs,
               now: datetime) -> list[Row]:
    """Rows for every play in ``state`` plus watched plays not scanned yet.

    ``seen_slugs``: plays the user already knew before the last refresh (anything else
    is ``is_new``); ``removed_slugs``: plays that disappeared in the last refresh.
    Archived plays (all sessions past) are left out unless watched or just removed.
    Order: nearest upcoming session, then title (independent of follow state).
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
    rows.sort(key=lambda r: (r.nearest or far, r.title.casefold(), r.slug))
    return rows


@dataclass
class PortalView:
    id: str
    text: str     # "🏛️ Başiskele rezervasyon portalı: boş · son kontrol 14:35"
    url: str
    state: str    # "unknown" | "empty" | "open" | "failing"


def _checked_text(value, now: datetime) -> str:
    d = _dt(value)
    if d is None:
        return ""
    d, today = d.astimezone(ISTANBUL), now.astimezone(ISTANBUL).date()
    return f"{d:%H:%M}" if d.date() == today else f"{d:%d.%m %H:%M}"


def portal_views(config: Config, state: dict | None, now: datetime) -> list[PortalView]:
    """One strip entry per enabled portal, from ``config.portals`` + ``state["portals"]``."""
    recs = (state or {}).get("portals")
    recs = recs if isinstance(recs, dict) else {}
    out = []
    for p in config.portals:
        if not p.get("enabled", True):
            continue
        raw = recs.get(p["id"])
        raw = raw if isinstance(raw, dict) else {}
        try:
            streak = int(raw.get("fail_streak") or 0)
        except (TypeError, ValueError):
            streak = 0
        empty = raw.get("empty")
        checked = _checked_text(raw.get("last_check"), now)
        if empty is True:
            state_name, status = "empty", "boş"
        elif empty is False:
            n = len(raw["items"]) if isinstance(raw.get("items"), list) else 0
            state_name, status = "open", f"{n} etkinlik açık" if n else "açık"
        else:
            state_name, status = "unknown", "henüz kontrol edilmedi"
        parts = [status]
        if empty is not None and checked:
            parts.append(f"son kontrol {checked}")
        if streak:
            parts.append(f"okunamadı ({streak}. kez)")
            state_name = "failing"
        out.append(PortalView(p["id"], f"🏛️ {portal_label(p['id'], p['name'])}: "
                              + " · ".join(parts), p["url"], state_name))
    return out


_TAG = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    return html.unescape(_TAG.sub("", text))


def headline(event: Event) -> str:
    if event.kind == "DAY_BEFORE":
        return DAY_BEFORE_TEXT.strip(" :")
    if event.kind == "DELIVERY_DROPPED":
        return "Bildirim iletilemedi"
    if event.kind.startswith("PORTAL_"):
        return portal_headline(event)
    if event.kind in SRC_TEXT:
        return source_headline(event)
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
