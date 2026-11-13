"""Biletinial page parser (stdlib only).

Session status comes from the button classes, never from JSON-LD
``offers.availability`` (it says InStock even for not-yet-open sessions).
"""
from __future__ import annotations

import html as htmlmod
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin

from tiyatro.models import ISTANBUL, Play, Session, Status

_TR_MONTHS = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}

_TAG_RE = re.compile(r"<(div|script|button|a)\b([^>]*)>", re.I | re.S)
_SCRIPT_END_RE = re.compile(r"</script\b", re.I)
_SECTION_END_RE = re.compile(r"</section\b", re.I)
_DIV_TOKEN_RE = re.compile(r"</?div\b", re.I)
_ATTR_RE = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
_PLAY_HREF_RE = re.compile(r"^(?:https?://[^/]+)?(/tr-tr/tiyatro/([^/?#]+))(?:/?)(\?[^#]*)?(?:#.*)?$", re.I)


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


def _attrs(raw: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for m in _ATTR_RE.finditer(raw):
        val = next((g for g in m.group(2, 3, 4) if g is not None), None)
        out.setdefault(m.group(1).lower(), htmlmod.unescape(val) if val is not None else None)
    return out


def _tags(html: str, name: str):
    for m in _TAG_RE.finditer(html):
        if m.group(1).lower() == name:
            yield m, _attrs(m.group(2))


def parse_venue(html: str, base: str = "https://biletinial.com") -> list[PlayRef]:
    found: dict[str, tuple[str, str, bool]] = {}  # slug -> (path, title, own)
    for _, at in _tags(html, "a"):
        hm = _PLAY_HREF_RE.match((at.get("href") or "").strip())
        if not hm:
            continue
        path, slug, query = hm.group(1), hm.group(2), hm.group(3) or ""
        own = "cinemabranchid=" in query.lower()
        title = (at.get("title") or "").strip()
        old = found.get(slug)
        found[slug] = (path, title or (old[1] if old else ""), own or (old[2] if old else False))
    own_only = {k: v for k, v in found.items() if v[2]}
    chosen = own_only or found
    if not chosen:
        raise ParseError("no play links found on venue page")
    return [
        PlayRef(slug, title or _title_from_slug(slug), urljoin(base, path))
        for slug, (path, title, _) in chosen.items()
    ]


def _json_ld_events(html: str) -> list[dict]:
    events: list[dict] = []
    for m, at in _tags(html, "script"):
        if (at.get("type") or "").strip().lower() != "application/ld+json":
            continue
        em = _SCRIPT_END_RE.search(html, m.end())
        if not em:
            continue
        try:
            data = json.loads(html[m.end():em.start()])
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
    at = None
    for _, cand in _tags(block, "button"):
        if "seanceselect" in (cand.get("class") or "").lower().split():
            at = cand
            break
    if at is None:
        return Status.UNKNOWN, None
    classes = set((at.get("class") or "").lower().split())
    disabled = "disabled" in at
    acilis = at.get("data-acilis")
    if "sold-out" in classes:
        return Status.SOLD_OUT, None
    if "off" in classes and disabled and acilis and re.fullmatch(r"\d{12}", acilis.strip()):
        try:
            opens = datetime.strptime(acilis.strip(), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return Status.UNKNOWN, None
        return Status.UPCOMING, opens.astimezone(ISTANBUL)
    # Confirmed on a real on-sale page (plain cta seanceSelect button).
    if classes == {"cta", "seanceselect"} and not disabled and "data-acilis" not in at:
        return Status.ON_SALE, None
    return Status.UNKNOWN, None


def _row_div_end(html: str, pos: int, limit: int) -> int:
    """End of the row's own <div> container (depth counting); `limit` if unbalanced."""
    depth = 1
    for m in _DIV_TOKEN_RE.finditer(html, pos, limit):
        depth += -1 if m.group(0).startswith("</") else 1
        if depth == 0:
            return m.start()
    return limit


def _row_blocks(html: str) -> list[tuple[str, str]]:
    marks = []
    for m, at in _tags(html, "div"):
        rm = re.fullmatch(r"seans-(\d+)", (at.get("id") or "").strip())
        if rm and "row" in (at.get("class") or "").lower().split():
            marks.append((m, rm.group(1)))
    blocks = []
    for i, (m, sid) in enumerate(marks):
        limit = marks[i + 1][0].start() if i + 1 < len(marks) else len(html)
        sec = _SECTION_END_RE.search(html, m.end(), limit)
        if sec:
            limit = sec.start()
        limit = _row_div_end(html, m.end(), limit)
        blocks.append((sid, html[m.end():limit]))
    return blocks


def _visible_start(sid: str, block: str, ld_start: datetime) -> datetime:
    """Start from the visible date/time; year from JSON-LD (its hour is ignored:
    some pages carry a wrong JSON-LD hour). Month/day must agree with JSON-LD."""
    d = re.search(r'''<span\b[^>]*\bclass=["'][^"']*\bd\b[^"']*["'][^>]*>\s*(\d{1,2})\s+([^\W\d_]+)''', block, re.I)
    t = re.search(r'''<span\b[^>]*\bclass=["'][^"']*\btm\b[^"']*["'][^>]*>\s*(\d{1,2}):(\d{2})''', block, re.I)
    if not d or not t:
        raise ParseError(f"seans-{sid}: date/time not found in block")
    month = _TR_MONTHS.get(htmlmod.unescape(d.group(2)).lower())
    day = int(d.group(1))
    if (month, day) != (ld_start.month, ld_start.day):
        raise ParseError(f"seans-{sid}: block date != JSON-LD start {ld_start.isoformat()}")
    try:
        return datetime(ld_start.year, month, day, int(t.group(1)), int(t.group(2)), tzinfo=ISTANBUL)
    except ValueError as e:
        raise ParseError(f"seans-{sid}: invalid visible time") from e


def _fact(html: str, label: str) -> str | None:
    m = re.search(rf"<dt>{label}</dt>\s*<dd>(.*?)</dd>", html, re.S)
    return _text(m.group(1)) if m else None


def _min_price(events: list[dict]) -> float | None:
    prices: list[float] = []
    for ev in events:
        offers = ev.get("offers")
        for off in offers if isinstance(offers, list) else [offers]:
            if not isinstance(off, dict):
                continue
            raw = off.get("price")
            if isinstance(raw, bool):
                continue
            try:
                val = float(raw.strip().replace(",", ".")) if isinstance(raw, str) else float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(val) and val >= 0:
                prices.append(val)
    return min(prices) if prices else None


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
        ld_start = _iso(ev.get("startDate"))
        if ld_start is None:
            raise ParseError(f"seans-{sid}: JSON-LD event has no startDate")
        start = _visible_start(sid, block, ld_start)
        ld_end = _iso(ev.get("endDate"))
        end = start + (ld_end - ld_start) if ld_end is not None else None
        status, opens_at = _button_state(block)
        share = re.search(r"""data-url\s*=\s*["']([^"']+)["']""", block, re.I)
        venue_m = re.search(r'''<div\b[^>]*\bclass=["'][^"']*\bvl\b[^"']*["'][^>]*>.*?<a\b[^>]*>(.*?)</a>''', block, re.S | re.I)
        venue = _text(venue_m.group(1)) if venue_m else (ev.get("location") or {}).get("name", "")
        sessions.append(Session(
            id=sid,
            start=start,
            end=end,
            status=status,
            opens_at=opens_at,
            url=htmlmod.unescape(share.group(1)) if share else f"{url}/{sid}",
            venue=venue,
        ))

    h1 = re.search(r'<h1\b[^>]*\bhero-v3__title\b[^>]*>(.*?)</h1>', html, re.S | re.I)
    title = _text(h1.group(1)) if h1 else ""
    dur = re.match(r"(\d+)", _fact(html, "Süre") or "")
    age = re.match(r"(\d+)", _fact(html, "Yaş Sınırı") or "")
    price = _min_price(events)
    return Play(
        slug=slug,
        title=title or _title_from_slug(slug),
        url=url,
        genre=_fact(html, "Etkinlik Türü"),
        duration_min=int(dur.group(1)) if dur else None,
        age_limit=int(age.group(1)) if age else None,
        price_from=price,
        sessions=sessions,
    )
