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


def _acilis(value) -> datetime | None:
    """``data-acilis`` (UTC ``YYYYMMDDHHMM``) as Istanbul time; None if absent/invalid."""
    if not value or not re.fullmatch(r"\d{12}", value.strip()):
        return None
    try:
        opens = datetime.strptime(value.strip(), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return opens.astimezone(ISTANBUL)


_TAG_STRIP_RE = re.compile(r"<[^>]*>")


def _closed_text(block: str, start: int) -> bool:
    """True if the button's inner text (from ``start`` to ``</button``) says it is not
    open: "Yakında" (soon) or "Tükendi" (sold out). Turkish-aware: ``İ``/``I`` lowercase
    to ``i̇``/``ı`` in Python, so both dotted and plain spellings are matched."""
    end = re.compile(r"</button", re.I).search(block, start)
    inner = block[start:end.start() if end else len(block)]
    text = htmlmod.unescape(_TAG_STRIP_RE.sub(" ", inner)).lower()
    text = text.replace("i̇", "i").replace("ı", "i")
    return "yakinda" in text or "tükend" in text or "tukend" in text


def _button_state(block: str) -> tuple[Status, datetime | None]:
    """Status from the row's ``seanceSelect`` button.

    ON_SALE = enabled ``seanceSelect`` without ``off``/``sold-out``; extra classes or a
    leftover ``data-acilis`` do not demote it (the markup of a first opening was never
    observed: a missed alert is worse than a rare false one), unless the button text
    says "Yakında"/"Tükendi" (then UNKNOWN). A parseable
    ``data-acilis`` is kept as ``opens_at`` for every status but SOLD_OUT.
    """
    at = None
    text_from = 0
    for m, cand in _tags(block, "button"):
        if "seanceselect" in (cand.get("class") or "").lower().split():
            at, text_from = cand, m.end()
            break
    if at is None:
        return Status.UNKNOWN, None
    classes = set((at.get("class") or "").lower().split())
    disabled = "disabled" in at
    opens = _acilis(at.get("data-acilis"))
    if "sold-out" in classes:
        return Status.SOLD_OUT, None
    if "off" in classes and disabled and opens is not None:
        return Status.UPCOMING, opens
    if "off" not in classes and not disabled:
        if _closed_text(block, text_from):
            return Status.UNKNOWN, opens  # "Yakında"/"Tükendi" text: never a false alert
        return Status.ON_SALE, opens
    return Status.UNKNOWN, opens


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
        **_details(html),
    )


# ---- play details: summary / cast / credits ---------------------------------------------

SUMMARY_MAX = 1500
CREDITS_MAX = 12
_ABOUT_RE = re.compile(r'<div\b[^>]*\bevent-card-v2__about\b[^>]*>(.*?)</div>', re.S | re.I)
_P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TITLE_ONLY_RE = re.compile(r"\s*<strong>[^<]*</strong>\s*", re.I)
_LEAD_STRONGS_RE = re.compile(r"\s*((?:<strong>[^<]*</strong>[ \t\xa0]*)+)(.*)", re.S | re.I)
_CAST_HEADS = {"oyuncular", "oyuncu kadrosu", "oynayanlar"}
_GROUP_RE = re.compile(r'<section\b[^>]*\bdata-cast-group="(\w+)"[^>]*>(.*?)</section>', re.S | re.I)
_PERSON_RE = re.compile(r'<span\b[^>]*\bcast-v2__person-copy\b[^>]*>(.*?)</span>', re.S | re.I)
_STRONG_RE = re.compile(r"<strong>(.*?)</strong>", re.S | re.I)
_SMALL_RE = re.compile(r"<small>(.*?)</small>", re.S | re.I)
_FOREIGN_LETTERS = set("WQXwqx")  # not Turkish: such a word's capital I is an English "i"


def _flat(fragment: str) -> str:
    """Tags dropped, entities decoded, every whitespace run folded to one space."""
    return re.sub(r"\s+", " ", htmlmod.unescape(_TAG_STRIP_RE.sub("", fragment))).strip()


def _tr_word(word: str, first_cap: bool) -> str:
    foreign = any(c in _FOREIGN_LETTERS for c in word)
    out = []
    for i, ch in enumerate(word):
        if foreign:
            low = ch.lower().replace("i\u0307", "i")
            up = low.upper()
        else:
            low = {"I": "ı", "İ": "i"}.get(ch) or ch.lower()
            up = {"i": "İ", "ı": "I"}.get(low) or low.upper()
        out.append(up if i == 0 and first_cap else low)
    return "".join(out)


def tr_title(text: str) -> str:
    """ALL-CAPS text to Turkish Title Case ("İBRAHİM AYDIN" -> "İbrahim Aydın"); text
    that already has lowercase letters is returned unchanged. Words with W/Q/X are
    treated as foreign (I -> i), e.g. "WILLIAM"."""
    if any(c.isalpha() and c != c.upper() for c in text):
        return text
    parts = re.split(r"([\s\-'’(),]+)", text)
    out, start = [], True
    for part in parts:
        if re.fullmatch(r"[\s\-'’(),]*", part):
            out.append(part)
            start = start or part.strip() == "" or part.strip() in ("(",)
            continue
        out.append(_tr_word(part, start))
        start = False
    return "".join(out)


def _cast_name(raw: str) -> str:
    """Name in Title Case; a trailing "(role)" is kept as the page wrote it."""
    m = re.fullmatch(r"(.*?)\s*(\([^()]*\))", raw.strip())
    if m and m.group(1):
        return f"{tr_title(m.group(1))} {m.group(2)}"
    return tr_title(raw.strip())


def _cap_summary(text: str) -> str:
    if len(text) <= SUMMARY_MAX:
        return text
    cut = text[:SUMMARY_MAX - 1]
    if not text[SUMMARY_MAX - 1].isspace():
        head = cut.rsplit(None, 1)
        cut = head[0] if len(head) == 2 else cut
    return cut.rstrip() + "…"


def _para_text(inner: str) -> str:
    text = _BR_RE.sub("\x00", inner)
    text = re.sub(r"\s+", " ", htmlmod.unescape(_TAG_STRIP_RE.sub("", text)))
    return re.sub(r" ?\x00 ?", "\n", text).strip()


def _credit_line(line: str) -> tuple[str, str] | None:
    m = _LEAD_STRONGS_RE.fullmatch(line)
    if not m:
        return None
    label, rest = _flat(m.group(1)), m.group(2).strip()
    if not label.endswith(":") and not rest.startswith(":"):
        return None
    label = label.rstrip(":").strip()
    value = _flat(rest.lstrip().lstrip(":"))
    return (label, tr_title(value)) if label and value else None


def _about_details(html: str) -> tuple[str | None, list[str], dict[str, str]]:
    m = _ABOUT_RE.search(html)
    if not m:
        return None, [], {}
    summary: list[str] = []
    cast: list[str] = []
    credits: dict[str, str] = {}
    for inner in _P_RE.findall(m.group(1)):
        lines = [ln for ln in _BR_RE.split(inner) if _flat(ln)]
        parsed = [_credit_line(ln) for ln in lines]
        if lines and all(parsed):
            for label, value in parsed:
                credits.setdefault(label, value)
            continue
        head = _LEAD_STRONGS_RE.fullmatch(lines[0]) if len(lines) > 1 else None
        if head and not head.group(2).strip():
            if _flat(head.group(1)).lower().replace("ı", "i") in _CAST_HEADS:
                names = re.split(r"[,;]", ",".join(_flat(ln) for ln in lines[1:]))
                cast.extend(_cast_name(n) for n in names if n.strip())
            continue  # other headed groups (e.g. chorus) are not summary text
        if _TITLE_ONLY_RE.fullmatch(inner):
            continue
        text = _para_text(inner)
        if text:
            summary.append(text)
    return ("\n\n".join(summary) or None), cast, credits


def _cast_section_details(html: str) -> tuple[list[str], dict[str, str]]:
    cast: list[str] = []
    credits: dict[str, str] = {}
    for kind, body in _GROUP_RE.findall(html):
        for person in _PERSON_RE.findall(body):
            name = _STRONG_RE.search(person)
            name = _flat(name.group(1)) if name else ""
            role = _SMALL_RE.search(person)
            role = _flat(role.group(1)) if role else ""
            if not name:
                continue
            if kind.lower() == "actor":
                cast.append(f"{name} ({role})" if role else name)
            elif role:
                credits[role] = f"{credits[role]}, {name}" if role in credits else name
    return cast, credits


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _details(html: str) -> dict:
    """``summary`` / ``cast`` / ``credits`` of a play page. The structured "Kadro"
    section is preferred (proper casing); the "Detay" paragraphs are the fallback."""
    summary, a_cast, a_credits = _about_details(html)
    s_cast, s_credits = _cast_section_details(html)
    credits = s_credits or a_credits
    return {
        "summary": _cap_summary(summary) if summary else None,
        "cast": _dedupe(s_cast or a_cast),
        "credits": dict(list(credits.items())[:CREDITS_MAX]),
    }
