"""Biletinial page parser (stdlib only).

Session status comes from the button classes, never from JSON-LD
``offers.availability`` (it says InStock even for not-yet-open sessions).
"""
from __future__ import annotations

import html as htmlmod
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin

from tiyatro.models import ISTANBUL, Play, Session, Status

_TR_MONTHS = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}

_ROW_RE = re.compile(r'<div class="row" id="seans-(\d+)">')
_LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_BUTTON_RE = re.compile(r"<button\b[^>]*\bseanceSelect\b[^>]*>", re.S)
_ATTR_RE = re.compile(r'([\w-]+)(?:="([^"]*)")?')
_VENUE_LINK_RE = re.compile(
    r'<a class="hpv2-event-card__visual" href="(/tr-tr/tiyatro/([^"?/]+))[^"]*"([^>]*)>'
)


class ParseError(Exception):
    pass


@dataclass
class PlayRef:
    slug: str
    title: str
    url: str


def _text(fragment: str) -> str:
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


def parse_venue(html: str, base: str = "https://biletinial.com") -> list[PlayRef]:
    refs: list[PlayRef] = []
    seen: set[str] = set()
    for m in _VENUE_LINK_RE.finditer(html):
        slug = m.group(2)
        if slug in seen:
            continue
        seen.add(slug)
        tm = re.search(r'\btitle="([^"]*)"', m.group(3))
        title = htmlmod.unescape(tm.group(1)).strip() if tm else ""
        refs.append(PlayRef(slug, title or _title_from_slug(slug), urljoin(base, m.group(1))))
    if not refs:
        raise ParseError("no play links found on venue page")
    return refs


def _json_ld_events(html: str) -> list[dict]:
    events: list[dict] = []
    for m in _LD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Event":
                events.append(item)
    return events


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise ParseError(f"bad ISO datetime {value!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ISTANBUL)
    return dt.astimezone(ISTANBUL)


def _button_state(block: str) -> tuple[Status, datetime | None]:
    m = _BUTTON_RE.search(block)
    if not m:
        return Status.UNKNOWN, None
    tag = m.group(0)[len("<button"):-1]
    attrs: dict[str, str | None] = {}
    for am in _ATTR_RE.finditer(tag):
        attrs[am.group(1)] = am.group(2)
    classes = set((attrs.get("class") or "").split())
    disabled = "disabled" in attrs
    if "sold-out" in classes:
        return Status.SOLD_OUT, None
    if "off" in classes and disabled:
        acilis = attrs.get("data-acilis")
        if acilis and re.fullmatch(r"\d{12}", acilis):
            opens = datetime.strptime(acilis, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            return Status.UPCOMING, opens.astimezone(ISTANBUL)
        return Status.UNKNOWN, None
    # PROVISIONAL: never observed on a real page yet.
    if classes == {"cta", "seanceSelect"} and not disabled:
        return Status.ON_SALE, None
    return Status.UNKNOWN, None


def _row_blocks(html: str) -> list[tuple[str, str]]:
    marks = list(_ROW_RE.finditer(html))
    blocks = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else html.find("</section>", m.end())
        blocks.append((m.group(1), html[m.end(): end if end != -1 else len(html)]))
    return blocks


def _check_block_matches(sid: str, block: str, start: datetime) -> None:
    d = re.search(r'<span class="d">\s*(\d{1,2})\s+([^\W\d_]+)', block)
    t = re.search(r'<span class="tm">\s*(\d{1,2}):(\d{2})', block)
    if not d or not t:
        raise ParseError(f"seans-{sid}: date/time not found in block")
    month = _TR_MONTHS.get(htmlmod.unescape(d.group(2)).lower())
    # Only month/day are enforced: the real Kusursuz page shows 14:00 while its
    # JSON-LD says 17:00 (see task report); the JSON-LD time is used as specified.
    if (month, int(d.group(1))) != (start.month, start.day):
        raise ParseError(f"seans-{sid}: block date != JSON-LD start {start.isoformat()}")


def _fact(html: str, label: str) -> str | None:
    m = re.search(rf"<dt>{label}</dt>\s*<dd>(.*?)</dd>", html, re.S)
    return _text(m.group(1)) if m else None


def parse_play(html: str, url: str) -> Play:
    url = url.split("?")[0].rstrip("/")
    slug = url.rsplit("/", 1)[-1]
    blocks = _row_blocks(html)
    events = _json_ld_events(html)
    if not blocks or not events:
        raise ParseError("no session rows or no JSON-LD events")
    if len(blocks) != len(events):
        raise ParseError(f"{len(blocks)} session rows vs {len(events)} JSON-LD events")

    sessions: list[Session] = []
    for (sid, block), ev in zip(blocks, events):
        start = _iso(ev.get("startDate"))
        if start is None:
            raise ParseError(f"seans-{sid}: JSON-LD event has no startDate")
        _check_block_matches(sid, block, start)
        status, opens_at = _button_state(block)
        share = re.search(r'data-url="([^"]+)"', block)
        venue_m = re.search(r'<div class="vl">.*?<a [^>]*>(.*?)</a>', block, re.S)
        venue = _text(venue_m.group(1)) if venue_m else (ev.get("location") or {}).get("name", "")
        sessions.append(Session(
            id=sid,
            start=start,
            end=_iso(ev.get("endDate")),
            status=status,
            opens_at=opens_at,
            url=htmlmod.unescape(share.group(1)) if share else f"{url}/{sid}",
            venue=htmlmod.unescape(venue),
        ))

    h1 = re.search(r'<h1 class="hero-v3__title">(.*?)</h1>', html, re.S)
    title = _text(h1.group(1)) if h1 else ""
    dur = re.match(r"(\d+)", _fact(html, "Süre") or "")
    age = re.match(r"(\d+)", _fact(html, "Yaş Sınırı") or "")
    price = (events[0].get("offers") or {}).get("price")
    return Play(
        slug=slug,
        title=title or _title_from_slug(slug),
        url=url,
        genre=_fact(html, "Etkinlik Türü"),
        duration_min=int(dur.group(1)) if dur else None,
        age_limit=int(age.group(1)) if age else None,
        price_from=float(price) if price is not None else None,
        sessions=sessions,
    )
