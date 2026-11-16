"""Events of non-Biletinial sources (wave 4): Başiskele Belediyesi and VKV Ford Otosan Gölcük.

Pure parsing + state folding; the runner supplies the fetched pages.

* Başiskele: the municipality news RSS lists a monthly "Kültür-Sanat" program article;
  every dated line of that article is one event (date, time(s), title, kind, venue).
* VKV: ``/Etkinlikler`` (server rendered, current month) lists the events; every detail
  page says when the free reservation opens ("... günü saat 12:00'da açılacaktır") or
  that it is full ("REZERVASYONLARIMIZ DOLMUŞTUR").

``state["source_events"][event_id]`` keeps one record per event; notifications are
``SRC_*`` events through the normal outbox/claims. Whether an event is followed is
``config.event_follow[event_id]`` (explicit choice) else the record's ``follow_default``,
which is OFF for every newly seen event (wave 6); records stored earlier keep theirs.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

from tiyatro.models import ISTANBUL, Config, Event
from tiyatro.notify import DAYS, MONTHS, SOURCE_LABELS, SRC_TEXT
from tiyatro.srcinfo import (borrow, facts_of, info_from_lines, info_hash, info_view, norm_title,
                             play_match)
from tiyatro.tags import (other_event_label, resolve_audience,
                          resolve_genre)
from tiyatro.timeutil import parse_iso

SRC_KINDS = tuple(SRC_TEXT)
BASISKELE = "basiskele"
VKV = "vkv"
BASISKELE_BASE = "https://www.basiskele.bel.tr"
BASISKELE_RSS = BASISKELE_BASE + "/rss"
VKV_BASE = "https://www.vkvfordkultur.org.tr"
VKV_LIST_URL = VKV_BASE + "/Etkinlikler"
VKV_TRACKED = ("Tiyatro", "Çocuk Tiyatrosu")

# The venue's public contact data: the site has NO online booking link (verified
# 2026-11-15: no reservation link/button on the detail or İletişim pages), so every VKV
# notification carries these.
VKV_CONTACT_PHONE = "(0262) 435 6700"
VKV_CONTACT_EMAIL = "info@vkvfordkultur.org.tr"
VKV_CONTACT_LINES = (f"📞 {VKV_CONTACT_PHONE} · ✉️ {VKV_CONTACT_EMAIL}",
                     "ℹ️ Sitede çevrimiçi rezervasyon bağlantısı yok: telefon veya e-posta ile ayırt.")

DAY_LEAD = timedelta(days=1)
DAY_CUTOFF = timedelta(hours=1)       # no "tomorrow" reminder inside the last hour
SOON_LEAD = timedelta(minutes=10)
BURST_BEFORE = timedelta(minutes=1)   # burst checks start 1 min before opens_at ...
BURST_AFTER = timedelta(minutes=30)   # ... and last 30 min
BURST_EVERY = timedelta(seconds=55)   # detail page re-check (~60 s) inside the window
DETAIL_MAX_PER_SCAN = 30
EVENT_TTL = timedelta(days=3)         # a record is dropped this long after the event
ARTICLE_TTL = timedelta(days=75)
MAX_ARTICLES = 3
MAX_EVENTS = 300

_TR_FOLD = str.maketrans({"ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
                          "I": "i", "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u",
                          "Ü": "u"})


def _lower(text: str) -> str:
    return str(text).replace("İ", "i").replace("I", "ı").lower()


def fold(text: str) -> str:
    """Lower-case ASCII fold ("Çocuk Tiyatrosu" -> "cocuk tiyatrosu")."""
    return str(text).translate(_TR_FOLD).lower()


_MONTH_NUM = {fold(m): i + 1 for i, m in enumerate(MONTHS)}
_MONTH_RE = "|".join(sorted((fold(m) for m in MONTHS), key=len, reverse=True))
_DAY_NAMES = {fold(d): i for i, d in enumerate(DAYS)}
_DAY_RE = "|".join(_DAY_NAMES)


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


# ---- text extraction -----------------------------------------------------------------

class _TextLines(HTMLParser):
    """Visible text of an HTML fragment, one entry per block element."""

    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
              "table", "ul", "ol", "article"}
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._cur: list[str] = []
        self._skip = 0

    def _flush(self):
        text = _norm_space("".join(self._cur))
        if text:
            self.lines.append(text)
        self._cur = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._cur.append(data)

    def close(self):
        super().close()
        self._flush()


def html_lines(fragment: str) -> list[str]:
    parser = _TextLines()
    try:
        parser.feed(fragment or "")
        parser.close()
    except Exception:  # malformed markup: keep what was read
        parser._flush()
    return parser.lines


# ---- shared date/time/opening parsing -------------------------------------------------

_DATE_RE = re.compile(rf"(?<!\d)(\d{{1,2}})\s+({_MONTH_RE})(?:\s+(\d{{4}}))?(?:\s+({_DAY_RE}))?",
                      re.I)
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[.:](\d{2})(?!\d)")


def resolve_date(day: int, month: int, weekday: int | None, years) -> date | None:
    """First year of ``years`` in which day/month exists (and falls on ``weekday``)."""
    for year in years:
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if weekday is None or d.weekday() == weekday:
            return d
    return None


def _at(d: date, hour: int, minute: int) -> datetime | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ISTANBUL)


_OPEN_WORD = re.compile(r"a[cç][iı]l|ba[sş]la|al[iı]nabil", re.I)


def parse_opening(text: str, years) -> tuple[datetime | None, bool]:
    """Reservation/ticket opening stated in ``text``: (opens_at, still_pending).

    A sentence that mentions "rezervasyon" or "bilet", an opening verb (açılacak /
    başlayacak / alınabilecek …) and a date WITH a clock time ("9 Kasım Pazartesi günü
    saat 12:00'da"). ``still_pending`` is True when the verb is the future "-acak" form.
    (None, False) when no such sentence exists.
    """
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9])|\n", text or ""):
        low = _lower(sentence)
        if not ("rezervasyon" in low or "bilet" in low) or not _OPEN_WORD.search(low):
            continue
        # the time must follow the date ("12 Kasım ... saat 10.00")
        m = _DATE_RE.search(fold(sentence))
        if m is None:
            continue
        tail = sentence[m.end():]
        t = _TIME_RE.search(tail)
        if t is None:
            continue
        month = _MONTH_NUM[m.group(2)]
        wd = _DAY_NAMES.get(m.group(4)) if m.group(4) else None
        d = resolve_date(int(m.group(1)), month, wd,
                         [int(m.group(3))] if m.group(3) else years)
        if d is None:
            continue
        at = _at(d, int(t.group(1)), int(t.group(2)))
        if at is None:
            continue
        return at, bool(re.search(r"a[cç][iı]lacak|ba[sş]layacak|al[iı]nabilecek", low))
    return None, False


def _slug(text: str, limit: int = 32) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")[:limit] or "x"


# ---- records -------------------------------------------------------------------------

@dataclass
class Listing:
    """One event as read from a source in this scan (None = not read / unknown)."""
    source: str
    id: str
    title: str
    kind: str = ""
    start: datetime | None = None
    venue: str = ""
    url: str = ""
    opens_at: datetime | None = None
    opens_pending: bool | None = None   # the page still says "açılacaktır"
    full: bool | None = None
    detail_ok: bool = False             # the detail page was read in this scan
    follow_default: bool = False        # wave 6: nothing is followed until the user says so
    info: dict | None = None            # wave 15: stated Özet/Oyuncular/Yapım/Süre/Yaş/Ücret ({} = read, nothing; None = not read)
    venue_derived: bool = False         # wave 17: venue resolved from "aynı merkezde" (not stated for this event)


def genre_of(kind: str, title: str) -> str:
    """Tür: the genre word(s) of the kind ("Animasyon Filmi", "Söyleşi"), else a genre
    keyword of kind/title ("Müzikal"), else "" (shown "—"; neither the audience word nor
    the generic "Tiyatro" is ever the genre). Wave 10: with no genre word, an event that is
    clearly not theatre (not is_theatre AND a non-theatre word such as atölye / söyleşi /
    film / konser / sergi / eğitim in kind or title) is "Diğer – <tür>" (Atölye, Söyleşi, Film, Konser, Sergi, Eğitim, Seminer/Panel); anything else
    stays "" (shown "—")."""
    found = resolve_genre(kind, title)
    if found:
        return found
    if not is_theatre(kind, title):
        return other_event_label(kind, title)
    return ""


def audience_for(kind: str, title: str) -> str:
    """Kitle of a source event: "Çocuk" when the category/title says so ("Çocuk
    Tiyatrosu"), otherwise "Yetişkin" (stated rule: no child marker -> Yetişkin)."""
    return resolve_audience(kind, (), None, f"{kind} {title}")


def is_theatre(kind: str, title: str) -> bool:
    low = _lower(f"{kind} {title}")
    return "tiyatro" in low or "müzikal" in low


# ---- Başiskele -----------------------------------------------------------------------

@dataclass
class RssItem:
    title: str
    link: str
    published: datetime | None
    desc: str = ""          # wave 17: plain text of the item description (hints for the fallback)


def parse_rss(xml_text: str) -> list[RssItem]:
    """Items of an RSS 2.0 feed; garbage -> []."""
    try:
        root = ET.fromstring((xml_text or "").lstrip("﻿").encode("utf-8"))
    except (ET.ParseError, ValueError):
        return []
    out = []
    for it in root.iter("item"):
        title = _norm_space(it.findtext("title") or "")
        link = _norm_space(it.findtext("link") or "")
        if not title or not link:
            continue
        pub = None
        raw = it.findtext("pubDate")
        if raw:
            try:
                from email.utils import parsedate_to_datetime
                pub = parsedate_to_datetime(raw.strip()).astimezone(ISTANBUL)
            except (TypeError, ValueError, IndexError):
                pub = None
        desc = _norm_space(re.sub(r"<[^>]+>", " ", htmllib.unescape(it.findtext("description") or "")))
        out.append(RssItem(title, urljoin(BASISKELE_BASE + "/", link), pub, desc[:400]))
    return out


def is_program_title(title: str) -> bool:
    """A monthly culture-art program article: a month name plus kültür-sanat / program /
    etkinlik in the title."""
    f = fold(title).replace("-", " ")
    f = re.sub(r"\s+", " ", f)
    return (any(re.search(rf"\b{m}", f) for m in _MONTH_NUM)
            and any(k in f for k in ("kultur sanat", "program", "etkinlik")))


def program_items(items: list[RssItem]) -> list[RssItem]:
    return [i for i in items if is_program_title(i.title)]


# Content-based fallback (wave 17): a program article whose title no longer matches the heuristic
# is found by what it says. Only recent, not yet checked items whose title/description hint at
# culture-art words are fetched (few per scan, remembered), and an article counts only when the
# event-sentence parser finds at least one dated and timed event in it.
FALLBACK_DAYS = 45
FALLBACK_MAX_FETCH = 3
CHECKED_MAX = 60
CULTURE_HINTS = ("tiyatro", "oyun", "konser", "sahne", "gosteri", "cocuk", "sinema", "atolye",
                 "soylesi", "sergi")
PROGRAM_MISSING_TEXT = "Program haberi bulunamadı (başlık değişmiş olabilir)"
PROGRAM_MISSING = "SRC_PROGRAM_MISSING"


def has_culture_hint(*texts: str) -> bool:
    low = fold(" ".join(t for t in texts if t))
    return any(h in low for h in CULTURE_HINTS)


def program_warning(now: datetime, listings: list, articles: dict) -> str | None:
    """Health of the Başiskele program discovery: the warning text when nothing usable was
    found (no events at all, nothing upcoming), or, from the 21st on, nothing dated in the
    current month. ``None`` = healthy."""
    local = now.astimezone(ISTANBUL)
    starts = [l.start for l in listings if l.start is not None]
    if not starts:
        return PROGRAM_MISSING_TEXT
    if not any(s >= now for s in starts):
        return PROGRAM_MISSING_TEXT
    if local.day >= 21 and not any(
            (s.astimezone(ISTANBUL).year, s.astimezone(ISTANBUL).month) == (local.year, local.month)
            for s in starts):
        return PROGRAM_MISSING_TEXT
    return None


_LINE_RE = re.compile(rf"^(\d{{1,2}})\s+({_MONTH_RE})\s+({_DAY_RE})\s*[–—-]\s*(.+)$", re.I)
_SEP = re.compile(r"\s+[–—-]\s+|\s*[–—]\s*")
_SESSION_TIME = re.compile(r"^(?:\d+\s*\.\s*seans\s*:\s*)?(\d{1,2})[.:](\d{2})$", re.I)
_PAREN_KIND = re.compile(r"\s*\(([^()]+)\)\s*$")
_QUOTED = re.compile(r"[“\"]([^“”\"]{2,80})[”\"]")
_NARRATIVE = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s+({_MONTH_RE})\s+({_DAY_RE})\s+g[uü]n[uü]\s+saat\s+(\d{{1,2}})[.:](\d{{2}})",
    re.I)
_KIND_PHRASE = re.compile(
    r"adl[iı]\s+((?:[a-zçğıöşü]+\s+)?(?:tiyatrosu|tiyatro|oyunu|filmi|m[uü]zikali|konseri|"
    r"g[oö]sterisi))", re.I)


class _Hit:
    """A "<day> <month> <weekday> günü saat HH.MM" mention (or "Aynı gün saat HH.MM" with the date
    of the previous sentence: ``derived_date``)."""
    __slots__ = ("start", "end", "day", "month", "weekday", "hour", "minute", "derived_date")

    def __init__(self, start, end, day, month, weekday, hour, minute, derived_date=False):
        self.start, self.end, self.day, self.month, self.weekday = start, end, day, month, weekday
        self.hour, self.minute, self.derived_date = hour, minute, derived_date


def _cap(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] if w else w for w in _norm_space(text).split(" "))


def _make_bk(start: datetime, title: str, kind: str, venue: str, url: str,
             info: dict | None = None, venue_derived: bool = False) -> Listing:
    eid = f"bk:{start:%Y%m%d-%H%M}-{_slug(title)}"
    return Listing(BASISKELE, eid, title, kind, start, venue, url, follow_default=False,
                   info=info if info is not None else {}, venue_derived=venue_derived)


# ---- venue / performer of narrative sentences (wave 17) ----------------------------------------
_CAP = "A-ZÇĞİÖŞÜ"
_WORD = rf"[{_CAP}][\wçğıöşüÇĞİÖŞÜ.]*"
# "Türkiye Yüzyılı Gençlik Merkezi’nde" / "Kitaphane’de" / "... Kafe & Restoran’da": capitalised
# words directly before a locative suffix (-de/-da/-te/-ta, -nde/-nda after a possessive)
_VENUE_RE = re.compile(rf"((?:{_WORD}\s+(?:&\s+)?){{0,7}}{_WORD})[’'](?:n?de|n?da|te|ta)(?![\wçğıöşü])")
_NOT_VENUE = {"basiskele", "kocaeli", "turkiye", "izmit", "istanbul", "ankara"}
_SAME_PLACE = re.compile(r"(?:yine\s+)?ayn[ıi]\s+(?:merkez|salon|mekan|sahne|yer|bina|tiyatro)\w*", re.I)
_COMPANY_RE = re.compile(
    rf"((?:{_WORD}\s+){{0,5}}(?:Tiyatrosu|Tiyatroları|Tiyatro|Topluluğu|Sahnesi|Kumpanyası|Ekibi))"
    rf"(?:[’']\w+)?\s+taraf[ıi]ndan")
# Wave 18: "Aynı gün saat 20.00’de ..." (the date is the one explicit date of the sentence right
# before, same line) and an unquoted series title ("... ise Kitaphane Kültür Söyleşileri kapsamında")
_SAME_DAY = re.compile(r"(?<![a-z0-9])ayni\s+gun\s+saat\s+(\d{1,2})[.:](\d{2})")
_SERIES = re.compile(
    rf"^(?:[’'][a-zçğıöşü]+)?[\s,]*(?:ise\s+)?((?:{_WORD}\s+){{1,4}}{_WORD})\s+kapsam[ıi]nda(?![\wçğıöşü])")
_SENTENCE_END = re.compile(r"(?<![\d])\.\s+(?=\S)")


def _sentence_spans(line: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(line):
        spans.append((start, m.end()))
        start = m.end()
    spans.append((start, len(line)))
    return spans


def _venues_in(text: str) -> list[str]:
    """Distinct venues a text states with a locative phrase, in order (district / city names
    alone are not venues; a weekday / month word glued in front is dropped)."""
    out: list[str] = []
    for m in _VENUE_RE.finditer(text):
        words = _norm_space(m.group(1)).split(" ")
        while len(words) > 1 and fold(words[0]) in {*_MONTH_NUM, *_DAY_NAMES, "ayni"}:
            words = words[1:]
        name = " ".join(words)
        if not name or fold(name) in _NOT_VENUE:
            continue
        if name not in out:
            out.append(name)
    return out


def _company_in(text: str) -> str:
    """The performing company a sentence names ("X Tiyatroları tarafından"); "" unless exactly one."""
    found = {_norm_space(m.group(1)) for m in _COMPANY_RE.finditer(text)}
    return found.pop() if len(found) == 1 else ""


def parse_article(html_text: str, url: str, published: datetime | None
                  ) -> tuple[list[Listing], datetime | None, bool]:
    """(events, opens_at, opens_pending) of one program article.

    Years are not written in the article: the year is the first of
    published.year, +1, -1 in which the stated weekday matches the date.
    """
    m = re.search(r'(?is)id="ContentPlaceHolder1_lbaciklama"[^>]*>(.*?)</span>', html_text or "")
    body = m.group(1) if m else html_text or ""
    lines = html_lines(body)
    base = (published or datetime.now(ISTANBUL)).year
    years = [base, base + 1, base - 1]
    events: dict[str, Listing] = {}
    prev_venues: list[str] = []      # venues stated by the last sentence of the previous line

    def add(listing: Listing | None):
        if listing is not None and listing.id not in events:
            events[listing.id] = listing

    for line in lines:
        fline = fold(line)  # 1:1 character map: offsets are valid for ``line`` too
        lm = _LINE_RE.match(fline)
        if lm is not None:
            prev_venues = []
            d = resolve_date(int(lm.group(1)), _MONTH_NUM[lm.group(2)],
                             _DAY_NAMES.get(lm.group(3)), years)
            if d is None:
                continue
            times, texts = [], []
            for tok in (t.strip() for t in _SEP.split(line[lm.start(4):]) if t.strip()):
                tm = _SESSION_TIME.match(tok)
                if tm:
                    times.append((int(tm.group(1)), int(tm.group(2))))
                else:
                    texts.append(tok)
            if not times or not texts:
                continue
            title_part, venue = texts[0], (texts[-1] if len(texts) > 1 else "")
            pk = _PAREN_KIND.search(title_part)
            kind = _cap(pk.group(1)) if pk else ""
            title = _norm_space(_PAREN_KIND.sub("", title_part))
            for hh, mm in times:
                start = _at(d, hh, mm)
                if start is not None and title:
                    add(_make_bk(start, title, kind, venue, url, facts_of(line)))
            continue
        # narrative sentence: "4 Kasım Salı günü saat 14.00’te sahnelenecek “Çöp Atlas” adlı çocuk tiyatrosu"
        spans = _sentence_spans(line)
        hits = [_Hit(m.start(), m.end(), int(m.group(1)), _MONTH_NUM[m.group(2)],
                     _DAY_NAMES.get(m.group(3)), int(m.group(4)), int(m.group(5)))
                for m in _NARRATIVE.finditer(fline)]
        explicit = list(hits)
        for sm in _SAME_DAY.finditer(fline):
            si0 = next(i for i, (a, b) in enumerate(spans) if a <= sm.start() < b)
            before = [h for h in explicit if si0 > 0 and spans[si0 - 1][0] <= h.start < spans[si0 - 1][1]]
            if len(before) == 1:        # exactly one explicit date in the immediately preceding sentence
                b0 = before[0]
                hits.append(_Hit(sm.start(), sm.end(), b0.day, b0.month, b0.weekday,
                                 int(sm.group(1)), int(sm.group(2)), derived_date=True))
        hits.sort(key=lambda h: h.start)
        sent_venues = [_venues_in(line[a:b]) for a, b in spans]
        parsed = []                      # the events of this line, with the text they were read from
        for n, nm in enumerate(hits):
            seg_end = hits[n + 1].start if n + 1 < len(hits) else len(line)
            segment = line[nm.end:seg_end]
            q = _QUOTED.search(segment)
            title_text, title_end = (q.group(1), q.end()) if q else (None, 0)
            if q is None:       # no quoted title: only "<Series Name> kapsamında" right after the time
                sm = _SERIES.match(segment)
                if sm is None:
                    continue
                title_text, title_end = sm.group(1), sm.end()
            d = resolve_date(nm.day, nm.month, nm.weekday, years)
            start = _at(d, nm.hour, nm.minute) if d else None
            if start is None:
                continue
            kind_m = _KIND_PHRASE.search(segment[title_end:])
            kind = _cap(kind_m.group(1)) if kind_m else ""
            hits_kind = kind
            if not hits_kind:  # coordinated titles share the kind stated after the last one
                for later in hits[n + 1:]:
                    seg2 = line[later.end:]
                    q2 = _QUOTED.search(seg2)
                    km = _KIND_PHRASE.search(seg2[q2.end():]) if q2 else None
                    if km:
                        hits_kind = _cap(km.group(1))
                        break
            clause = re.split(r"\.(?:\s|$)", line[nm.start:seg_end], maxsplit=1)[0]
            si = next(i for i, (a, b) in enumerate(spans) if a <= nm.start < b)
            own = _venues_in(line[nm.end:min(seg_end, spans[si][1])])
            parsed.append({"si": si, "start": start, "title": _norm_space(title_text),
                           "kind": hits_kind, "clause": clause, "own": own[0] if own else "",
                           "same": bool(_SAME_PLACE.search(line[nm.end:min(seg_end, spans[si][1])]))})
        for k, item in enumerate(parsed):
            venue, derived = item["own"], False
            if not venue:       # one venue stated after coordinated titles of one sentence is shared
                later = {p["own"] for p in parsed[k + 1:] if p["si"] == item["si"] and p["own"]}
                if len(later) == 1 and len(sent_venues[item["si"]]) == 1:
                    venue = later.pop()
            if not venue and item["same"]:      # "yine aynı merkezde": only the preceding sentence counts
                before = prev_venues if item["si"] == 0 else sent_venues[item["si"] - 1]
                if len(before) == 1:
                    venue, derived = before[0], True
            info = facts_of(item["clause"])
            company = _company_in(line[spans[item["si"]][0]:spans[item["si"]][1]])
            if company:
                info["credits"] = {"Sahneleyen": company}
            add(_make_bk(item["start"], item["title"], item["kind"], venue, url, info, derived))
        prev_venues = sent_venues[-1] if sent_venues else []
    opens_at, pending = parse_opening("\n".join(lines), years)
    return sorted(events.values(), key=lambda e: (e.start, e.id)), opens_at, pending


# ---- VKV -----------------------------------------------------------------------------

_CARD_RE = re.compile(
    r'(?is)<div class="event-cont">.*?<a href="(?P<href>/EtkinlikDetay/(?P<id>\d+)/[^"]*)".*?'
    r'<p class="kat">(?P<kat>.*?)</p>\s*<p class="mt20">(?P<title>.*?)</p>\s*'
    r'<p class="mt60">(?P<date>.*?)</p>\s*<p>\s*Saat:\s*(?P<time>[0-9:.]+)\s*</p>')


def parse_vkv_list(html_text: str) -> list[Listing]:
    out: dict[str, Listing] = {}
    for m in _CARD_RE.finditer(html_text or ""):
        kat = _norm_space(htmllib.unescape(re.sub(r"<[^>]+>", "", m.group("kat"))))
        title = _norm_space(htmllib.unescape(re.sub(r"<[^>]+>", "", m.group("title"))))
        dm = _DATE_RE.search(fold(htmllib.unescape(m.group("date"))))
        tm = _TIME_RE.search(m.group("time"))
        if not (title and dm and tm and dm.group(3)):
            continue
        d = resolve_date(int(dm.group(1)), _MONTH_NUM[dm.group(2)], None, [int(dm.group(3))])
        start = _at(d, int(tm.group(1)), int(tm.group(2))) if d else None
        if start is None:
            continue
        eid = f"vkv:{m.group('id')}"
        out[eid] = Listing(VKV, eid, title, kat, start, "VKV Ford Otosan Gölcük KSYM",
                           urljoin(VKV_BASE + "/", m.group("href")),
                           follow_default=False)
    return list(out.values())


def vkv_tracked(listing: Listing) -> bool:
    return listing.kind in VKV_TRACKED


@dataclass
class VkvDetail:
    opens_at: datetime | None = None
    opens_pending: bool = False
    full: bool = False
    info: dict | None = None   # None: the content block could not be located (keep what is stored)


_PAGE_DATE = re.compile(rf"^\d{{1,2}}\s+(?:{_MONTH_RE})\s+\d{{4}}\s+(?:{_DAY_RE})\b")


def vkv_info(html_text: str) -> dict | None:
    """Stated info of a VKV detail page: the paragraphs between the date line and the photo
    gallery (reservation / contact sentences removed), cast / crew from labelled lines.
    None when the date line is not found (page changed or garbled)."""
    lines = html_lines(html_text or "")
    head = next((n for n, ln in enumerate(lines) if _PAGE_DATE.match(fold(ln))), None)
    if head is None:
        return None
    block = []
    for ln in lines[head + 1:head + 41]:
        low = fold(ln)
        if "fotograf galeri" in low or low in ("galeri", "paylas"):
            break
        block.append(ln)
    return info_from_lines(block)


def parse_vkv_detail(html_text: str, start: datetime | None) -> VkvDetail:
    """Reservation facts of a detail page. ``full``: "REZERVASYONLARIMIZ DOLMUŞTUR".
    The "… açılacaktır" sentence is kept on the page after it has opened (a full event
    still shows it), so ``opens_pending`` alone never proves the reservation is closed."""
    text = "\n".join(html_lines(html_text or ""))
    years = [start.year, start.year - 1] if start else [datetime.now(ISTANBUL).year]
    opens, pending = parse_opening(text, years)
    if opens is not None and start is not None and opens > start:
        opens, pending = None, False  # implausible: after the event itself
    return VkvDetail(opens, pending, "dolmus" in fold(text) and "rezervasyon" in fold(text),
                     vkv_info(html_text))


# ---- following -----------------------------------------------------------------------

def effective_follow(config: Config, event_id: str, default: bool) -> bool:
    choice = getattr(config, "event_follow", {}).get(event_id)
    return default if choice is None else bool(choice)


# ---- state folding -------------------------------------------------------------------

def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _dt(value) -> datetime | None:
    try:
        return parse_iso(value) if isinstance(value, str) else None
    except ValueError:
        return None


def reservation_text(rec: dict, now: datetime) -> str:
    """"Rezervasyon durumu" column text."""
    if rec.get("full"):
        return "Doldu"
    opens = _dt(rec.get("opens_at"))
    if opens is None:
        return "Duyuru bekleniyor"
    if now < opens:
        d = opens.astimezone(ISTANBUL)
        return f"{d.day} {MONTHS[d.month - 1]} {d:%H:%M}'da açılacak"
    if rec.get("opens_pending") is False:
        return "Açık"
    return "Açılmış olabilir"


def _payload(rec: dict, extra: dict | None = None) -> dict:
    p = {"source": rec["source"], "event_id": rec["id"], "title": rec["title"],
         "kind": rec.get("kind") or "", "start": rec.get("start"),
         "venue": "" if rec.get("venue_derived") is True else rec.get("venue") or "",
         "url": rec.get("url") or "", "opens_at": rec.get("opens_at")}
    if rec["source"] == VKV:
        p["contact"] = list(VKV_CONTACT_LINES)
    if extra:
        p.update(extra)
    return p


def _emit(state: dict, kind: str, key: str, rec: dict, now: datetime,
          extra: dict | None = None) -> Event | None:
    if key in state["emitted"]:
        return None
    state["emitted"][key] = now.isoformat()
    return Event(kind, key, "", None, [], _payload(rec, extra))


def reservation_summary(rec: dict, now: datetime) -> str:
    """"Durum özeti" of a source event in the unified list (lower-case sentence)."""
    if rec.get("full"):
        return "doldu"
    opens = _dt(rec.get("opens_at"))
    if opens is None:
        return "duyuru bekleniyor"
    if now < opens:
        d = opens.astimezone(ISTANBUL)
        return f"rezervasyon {d.day} {MONTHS[d.month - 1][:3]} {d:%H:%M}'da açılacak"
    if rec.get("opens_pending") is False:
        return "rezervasyon açık"
    return "rezervasyon açılmış olabilir"


def fold_listings(state: dict, config: Config, source: str, listings: list[Listing],
                  now: datetime, *, complete: bool = True) -> list[Event]:
    """Fold one scan of ``source`` into ``state["source_events"]``; returns SRC_* events.

    The first scan of a source (no record yet) is a silent baseline: nothing is
    announced as new, but reminders and the opening alert of already-known opening
    times still work. Afterwards every newly seen upcoming event produces one ``SRC_NEW``
    discovery notice, whether it is followed or not (reminders/opening alerts stay
    follow-gated).
    """
    store = state.get("source_events")
    if not isinstance(store, dict):
        store = state["source_events"] = {}
    baseline = not any(isinstance(r, dict) and r.get("source") == source for r in store.values())
    events: list[Event] = []
    for ls in listings:
        rec = store.get(ls.id)
        is_new = not isinstance(rec, dict)
        if is_new:
            rec = store[ls.id] = {"id": ls.id, "source": source, "first_seen": now.isoformat(),
                                  "full": None, "opens_at": None, "opens_pending": None}
        rec.update(title=ls.title, kind=ls.kind, venue=ls.venue, venue_derived=bool(ls.venue_derived), url=ls.url,
                   start=_iso(ls.start), last_seen=now.isoformat(),
                   audience=audience_for(ls.kind, ls.title),
                   genre=genre_of(ls.kind, ls.title))
        if is_new:
            rec["follow_default"] = bool(ls.follow_default)
        elif "follow_default" not in rec:  # stored before the field existed: it was ON then
            rec["follow_default"] = True   # (an existing record keeps its default: wave 6)
        prev_opens = rec.get("opens_at")
        if ls.opens_at is not None:
            rec["opens_at"] = _iso(ls.opens_at)
            rec["opens_pending"] = ls.opens_pending
        elif ls.detail_ok and rec.get("opens_at"):
            # The page was read and no longer announces the opening: the time is kept (the
            # alert key depends on it) and the event counts as opened.
            rec["opens_pending"] = False
        prev_full = rec.get("full")
        if ls.full is not None:
            rec["full"] = bool(ls.full)
        if ls.detail_ok:
            rec["last_detail_at"] = now.isoformat()
        if ls.info is not None and ls.detail_ok:
            digest = info_hash(ls.info)       # rewrite only when the page content changed
            if rec.get("info_hash") != digest:
                rec["info"], rec["info_hash"] = ls.info, digest
        start = _dt(rec.get("start"))
        if start is not None and start < now - timedelta(hours=3):
            continue  # past event: bookkeeping only
        followed = effective_follow(config, ls.id, bool(rec.get("follow_default")))
        if is_new and not baseline:
            # Discovery notice (wave 9): sent for every new event, followed or not, once
            # per event id (emitted key). A record already in the state is never "new", so
            # nothing present at deploy time can fire it.
            events.extend(filter(None, [_emit(state, "SRC_NEW", f"SRC_NEW:{ls.id}", rec, now,
                                              {"followed": followed})]))
        if not followed:
            continue
        opens = _dt(rec.get("opens_at"))
        if not is_new and opens is not None and _iso(opens) != prev_opens:
            events.extend(filter(None, [_emit(
                state, "SRC_OPENS", f"SRC_OPENS:{ls.id}:{_iso(opens)}", rec, now)]))
        events.extend(_due_events(state, rec, opens, prev_full, now))
    _prune(store, now)
    return events


def _due_events(state: dict, rec: dict, opens: datetime | None, prev_full, now: datetime
                ) -> list[Event]:
    out: list[Event | None] = []
    eid = rec["id"]
    if opens is not None:
        tag = _iso(opens)
        if now < opens and rec.get("opens_pending") is False:
            out.append(_emit(state, "SRC_OPENED", f"SRC_OPENED:{eid}:{tag}", rec, now,
                             {"detail": "Sayfadaki açılış duyurusu kalktı: rezervasyon "
                                        "açılmış olabilir."}))
        elif now < opens:
            if opens - DAY_LEAD <= now < opens - DAY_CUTOFF:
                out.append(_emit(state, "SRC_DAY_BEFORE", f"SRC_DAY:{eid}:{tag}", rec, now))
            if opens - SOON_LEAD <= now < opens:
                out.append(_emit(state, "SRC_SOON", f"SRC_SOON:{eid}:{tag}", rec, now))
        else:
            # opening time reached (or the page stopped announcing it): alert once, even
            # if the page still shows the old sentence (a full event still does).
            note = ("" if rec.get("opens_pending") is False
                    else "Sayfa henüz güncellenmemiş olabilir; açılış saati geldi.")
            out.append(_emit(state, "SRC_OPENED", f"SRC_OPENED:{eid}:{tag}", rec, now,
                             {"detail": note} if note else None))
    if prev_full is True and rec.get("full") is False:
        n = int(rec.get("reopen_count") or 0) + 1
        rec["reopen_count"] = n
        out.append(_emit(state, "SRC_REOPENED", f"SRC_REOPENED:{eid}:{n}", rec, now))
    return [e for e in out if e is not None]


def _prune(store: dict, now: datetime) -> None:
    for eid in [k for k, r in store.items()
                if not isinstance(r, dict) or (_dt(r.get("start")) is not None
                                               and _dt(r["start"]) < now - EVENT_TTL)]:
        del store[eid]
    if len(store) > MAX_EVENTS:  # hard bound: keep the soonest
        keep = sorted(store, key=lambda k: store[k].get("start") or "")[-MAX_EVENTS:]
        for eid in [k for k in store if k not in keep]:
            del store[eid]


def burst_ids(state: dict, config: Config, now: datetime) -> list[str]:
    """Followed events whose reservation opening is within [-1 min, +30 min] of now and
    which are not full: they are polled every ~60 s."""
    out = []
    for eid, rec in (state.get("source_events") or {}).items():
        if not isinstance(rec, dict) or rec.get("source") != VKV or rec.get("full"):
            continue
        opens = _dt(rec.get("opens_at"))
        if opens is None or not opens - BURST_BEFORE <= now <= opens + BURST_AFTER:
            continue
        if effective_follow(config, eid, bool(rec.get("follow_default", True))):
            out.append(eid)
    return sorted(out)


def source_burst_end(state: dict, eid: str) -> datetime:
    rec = (state.get("source_events") or {}).get(eid) or {}
    return (_dt(rec.get("opens_at")) or datetime.now(ISTANBUL)) + BURST_AFTER


def due_for_burst_check(rec: dict, now: datetime) -> bool:
    last = _dt(rec.get("last_detail_at"))
    return last is None or now - last >= BURST_EVERY


def event_views(state: dict | None, config: Config | None, now: datetime) -> list[dict]:
    """Rows for the unified list (PC) / Worker list: future events by start."""
    cfg_follow = getattr(config, "event_follow", {}) if config is not None else {}
    rows = []
    for eid, rec in ((state or {}).get("source_events") or {}).items():
        if not isinstance(rec, dict):
            continue
        start = _dt(rec.get("start"))
        if start is not None and start < now - timedelta(hours=3):
            continue
        default = bool(rec.get("follow_default", True))
        choice = cfg_follow.get(eid)
        kind, title = str(rec.get("kind") or ""), str(rec.get("title") or "")
        merged = borrow(info_view(rec), genre_of(kind, title), state, title)
        rows.append({
            "id": eid, "source": rec.get("source", ""),
            "source_name": SOURCE_LABELS.get(rec.get("source"), str(rec.get("source"))),
            "title": str(rec.get("title") or eid), "start": start,
            "when": f"{start.astimezone(ISTANBUL):%d.%m.%Y %H:%M}" if start else "",
            "audience": audience_for(str(rec.get("kind") or ""), str(rec.get("title") or "")),
            "genre": merged["genre"], "genre_borrowed": "genre" in merged["borrowed"],
            "kind": str(rec.get("kind") or ""), "venue": str(rec.get("venue") or ""),
            "venue_derived": rec.get("venue_derived") is True,
            "url": str(rec.get("url") or ""), "opens_at": _dt(rec.get("opens_at")),
            "opens_pending": rec.get("opens_pending"),
            "info": merged["info"], "borrowed": merged["borrowed"],
            "borrowed_credit_labels": merged["borrowed_credit_labels"],
            "borrowed_from": merged["borrowed_from"],
            "reservation": reservation_text(rec, now),
            "summary": reservation_summary(rec, now), "full": bool(rec.get("full")),
            "followed": default if choice is None else bool(choice)})
    far = datetime.max.replace(tzinfo=ISTANBUL)
    rows.sort(key=lambda r: (r["start"] or far, r["id"]))
    return rows


# ---- scanning (network goes through the injected ``fetch_fn``) ---------------------------

@dataclass
class SourceScan:
    """Result of reading one source: listings plus bookkeeping for ``sources.record_check``."""
    source: str
    listings: list[Listing] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    articles: dict = field(default_factory=dict)   # Başiskele: remembered program articles
    complete: bool = True                          # False: partial read (burst / detail only)
    checked: dict = field(default_factory=dict)    # Başiskele: other RSS items already read (link -> {pub, ok})
    warning: str | None = None                     # Başiskele: program discovery health (see program_warning)
    extra_fetches: int = 0                         # Başiskele: content-fallback article fetches this scan

    def content(self) -> list:
        """Normalized parsed content (for the source's change time)."""
        return sorted([l.id, l.title, _iso(l.start), _iso(l.opens_at), l.full]
                      for l in self.listings)


def scan_basiskele(fetch_fn, cache: dict | None, now: datetime,
                   checked: dict | None = None) -> SourceScan:
    """Read the RSS feed, then every known monthly program article (newest
    ``MAX_ARTICLES``). ``cache`` = state["src_cache"]["basiskele"]["articles"]."""
    out = SourceScan(BASISKELE)
    try:
        items = parse_rss(fetch_fn(BASISKELE_RSS).html)
        if not items:
            raise ValueError("EmptyFeed")
    except Exception as exc:
        out.ok, out.error = False, type(exc).__name__
        return out
    known: dict[str, dict] = {}
    for link, rec in (cache or {}).items():
        if isinstance(rec, dict) and isinstance(link, str):
            known[link] = {"title": str(rec.get("title") or ""), "pub": rec.get("pub")}
            if rec.get("via"):
                known[link]["via"] = str(rec["via"])
    for it in program_items(items):
        known[it.link] = {"title": it.title, "pub": _iso(it.published)}
    seen_other = {k: dict(v) for k, v in (checked or {}).items()
                  if isinstance(k, str) and isinstance(v, dict)}
    fb_horizon = now - timedelta(days=FALLBACK_DAYS)
    pages: dict[str, str] = {}      # pages read by the fallback: not fetched a second time below
    for it in sorted(items, key=lambda i: i.published or fb_horizon, reverse=True):
        if out.extra_fetches >= FALLBACK_MAX_FETCH:
            break
        if (it.link in known or it.link in seen_other or it.published is None
                or it.published < fb_horizon or is_program_title(it.title)
                or not has_culture_hint(it.title, it.desc)):
            continue
        out.extra_fetches += 1
        try:
            page = fetch_fn(it.link).html
            found, _o, _p = parse_article(page, it.link, it.published)
        except Exception:
            continue          # not remembered: tried again next scan (still capped)
        seen_other[it.link] = {"pub": _iso(it.published), "ok": bool(found)}
        if found:
            pages[it.link] = page
            known[it.link] = {"title": it.title, "pub": _iso(it.published), "via": "content"}
    keep = sorted(seen_other, key=lambda k: seen_other[k].get("pub") or "", reverse=True)[:CHECKED_MAX]
    out.checked = {k: seen_other[k] for k in keep
                   if (_dt(seen_other[k].get("pub")) or now) >= fb_horizon}
    horizon = now - ARTICLE_TTL
    for link in [k for k, r in known.items()
                 if (_dt(r.get("pub")) or now) < horizon]:
        del known[link]
    newest = sorted(known, key=lambda k: known[k].get("pub") or "", reverse=True)[:MAX_ARTICLES]
    out.articles = {k: known[k] for k in newest}
    failed = 0
    for link in newest:
        try:
            page = pages.get(link) or fetch_fn(link).html
            events, opens_at, pending = parse_article(page, link, _dt(known[link].get("pub")))
        except Exception:
            failed += 1
            continue
        for ev in events:
            ev.opens_at, ev.opens_pending, ev.detail_ok = opens_at, (pending if opens_at else None), True
            out.listings.append(ev)
    if newest and failed == len(newest):
        out.ok, out.error = False, "ArticleFetch"
    elif failed:
        out.error = f"ArticleFetch({failed})"
    seen: dict[str, Listing] = {}
    for ls in out.listings:  # one event may be in two articles: keep the first
        seen.setdefault(ls.id, ls)
    out.listings = list(seen.values())
    if out.ok:
        out.warning = program_warning(now, out.listings, out.articles)
    return out


def _apply_detail(ls: Listing, detail: VkvDetail) -> None:
    ls.opens_at, ls.opens_pending, ls.full, ls.detail_ok = (
        detail.opens_at, detail.opens_pending if detail.opens_at else None, detail.full, True)
    ls.info = detail.info


def scan_vkv(fetch_fn, now: datetime, sleep=lambda _s: None) -> SourceScan:
    """Event list + detail pages of the tracked categories (Tiyatro, Çocuk Tiyatrosu)."""
    out = SourceScan(VKV)
    try:
        page = fetch_fn(VKV_LIST_URL).html
        listings = parse_vkv_list(page)
        if not listings and "etkinlik takvimi" not in fold(page):
            raise ValueError("GarbledPage")
    except Exception as exc:
        out.ok, out.error = False, type(exc).__name__
        return out
    tracked = [l for l in listings if vkv_tracked(l)
               and (l.start is None or l.start >= now - timedelta(hours=3))]
    attempted = failed = 0
    for ls in tracked[:DETAIL_MAX_PER_SCAN]:
        if attempted:
            sleep(1)
        attempted += 1
        try:
            _apply_detail(ls, parse_vkv_detail(fetch_fn(ls.url).html, ls.start))
        except Exception:
            failed += 1
    out.listings = tracked
    if attempted and failed == attempted:
        out.ok, out.error = False, "DetailFetch"
    elif failed:
        out.error = f"DetailFetch({failed})"
    return out


def scan_vkv_burst(fetch_fn, state: dict, ids: list[str], now: datetime) -> SourceScan:
    """Re-read the detail pages of the events in their opening window."""
    out = SourceScan(VKV, complete=False)
    store = state.get("source_events") or {}
    attempted = failed = 0
    for eid in ids:
        rec = store.get(eid)
        if not isinstance(rec, dict) or not rec.get("url"):
            continue
        ls = Listing(VKV, eid, rec.get("title") or eid, rec.get("kind") or "",
                     _dt(rec.get("start")), rec.get("venue") or "", rec["url"],
                     follow_default=bool(rec.get("follow_default", True)))
        attempted += 1
        try:
            _apply_detail(ls, parse_vkv_detail(fetch_fn(ls.url).html, ls.start))
        except Exception:
            failed += 1
            continue
        out.listings.append(ls)
    if attempted and failed == attempted:
        out.ok, out.error = False, "DetailFetch"
    return out
