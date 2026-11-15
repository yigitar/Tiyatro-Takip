"""Events of non-Biletinial sources (wave 4): Başiskele Belediyesi and VKV Ford Otosan Gölcük.

Pure parsing + state folding; the runner supplies the fetched pages.

* Başiskele: the municipality news RSS lists a monthly "Kültür-Sanat" program article;
  every dated line of that article is one event (date, time(s), title, kind, venue).
* VKV: ``/Etkinlikler`` (server rendered, current month) lists the events; every detail
  page says when the free reservation opens ("... günü saat 12:00'da açılacaktır") or
  that it is full ("REZERVASYONLARIMIZ DOLMUŞTUR").

``state["source_events"][event_id]`` keeps one record per event; notifications are
``SRC_*`` events through the normal outbox/claims. Whether an event is followed is
``config.event_follow[event_id]`` (explicit choice) else a default of ON for theatre.
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
from tiyatro.tags import audience_of, genre_label
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
    follow_default: bool = True


def genre_of(kind: str, title: str) -> str:
    """Tür: the genre word(s) of the kind ("Animasyon Filmi", "Söyleşi") or "Müzikal";
    never the audience; "" when only "Tiyatro"/"Çocuk Tiyatrosu" is known."""
    g = genre_label(kind)
    if g:
        return g
    return "Müzikal" if "müzikal" in _lower(f"{kind} {title}") else ""


def is_theatre(kind: str, title: str) -> bool:
    low = _lower(f"{kind} {title}")
    return "tiyatro" in low or "müzikal" in low


# ---- Başiskele -----------------------------------------------------------------------

@dataclass
class RssItem:
    title: str
    link: str
    published: datetime | None


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
        out.append(RssItem(title, urljoin(BASISKELE_BASE + "/", link), pub))
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


def _cap(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] if w else w for w in _norm_space(text).split(" "))


def _make_bk(start: datetime, title: str, kind: str, venue: str, url: str) -> Listing:
    eid = f"bk:{start:%Y%m%d-%H%M}-{_slug(title)}"
    return Listing(BASISKELE, eid, title, kind, start, venue, url,
                   follow_default=is_theatre(kind, title))


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

    def add(listing: Listing | None):
        if listing is not None and listing.id not in events:
            events[listing.id] = listing

    for line in lines:
        fline = fold(line)  # 1:1 character map: offsets are valid for ``line`` too
        lm = _LINE_RE.match(fline)
        if lm is not None:
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
                    add(_make_bk(start, title, kind, venue, url))
            continue
        # narrative sentence: "4 Kasım Salı günü saat 14.00’te sahnelenecek “Çöp Atlas” adlı çocuk tiyatrosu"
        hits = list(_NARRATIVE.finditer(fline))
        for n, nm in enumerate(hits):
            seg_end = hits[n + 1].start() if n + 1 < len(hits) else len(line)
            segment = line[nm.end():seg_end]
            q = _QUOTED.search(segment)
            if q is None:
                continue
            d = resolve_date(int(nm.group(1)), _MONTH_NUM[nm.group(2)],
                             _DAY_NAMES.get(nm.group(3)), years)
            start = _at(d, int(nm.group(4)), int(nm.group(5))) if d else None
            if start is None:
                continue
            kind_m = _KIND_PHRASE.search(segment[q.end():])
            kind = _cap(kind_m.group(1)) if kind_m else ""
            events_here = (start, _norm_space(q.group(1)), kind)
            hits_kind = kind
            if not hits_kind:  # coordinated titles share the kind stated after the last one
                for later in hits[n + 1:]:
                    seg2 = line[later.end():]
                    q2 = _QUOTED.search(seg2)
                    km = _KIND_PHRASE.search(seg2[q2.end():]) if q2 else None
                    if km:
                        hits_kind = _cap(km.group(1))
                        break
            add(_make_bk(events_here[0], events_here[1], hits_kind, "", url))
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
                           follow_default=kat in VKV_TRACKED)
    return list(out.values())


def vkv_tracked(listing: Listing) -> bool:
    return listing.kind in VKV_TRACKED


@dataclass
class VkvDetail:
    opens_at: datetime | None = None
    opens_pending: bool = False
    full: bool = False


def parse_vkv_detail(html_text: str, start: datetime | None) -> VkvDetail:
    """Reservation facts of a detail page. ``full``: "REZERVASYONLARIMIZ DOLMUŞTUR".
    The "… açılacaktır" sentence is kept on the page after it has opened (a full event
    still shows it), so ``opens_pending`` alone never proves the reservation is closed."""
    text = "\n".join(html_lines(html_text or ""))
    years = [start.year, start.year - 1] if start else [datetime.now(ISTANBUL).year]
    opens, pending = parse_opening(text, years)
    if opens is not None and start is not None and opens > start:
        opens, pending = None, False  # implausible: after the event itself
    return VkvDetail(opens, pending, "dolmus" in fold(text) and "rezervasyon" in fold(text))


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
         "kind": rec.get("kind") or "", "start": rec.get("start"), "venue": rec.get("venue") or "",
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


def fold_listings(state: dict, config: Config, source: str, listings: list[Listing],
                  now: datetime, *, complete: bool = True) -> list[Event]:
    """Fold one scan of ``source`` into ``state["source_events"]``; returns SRC_* events.

    The first scan of a source (no record yet) is a silent baseline: nothing is
    announced as new, but reminders and the opening alert of already-known opening
    times still work.
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
        rec.update(title=ls.title, kind=ls.kind, venue=ls.venue, url=ls.url,
                   start=_iso(ls.start), last_seen=now.isoformat(),
                   audience=audience_of(ls.kind), genre=genre_of(ls.kind, ls.title),
                   follow_default=bool(ls.follow_default))
        if not rec["audience"]:
            rec["audience"] = audience_of(ls.title) if "çocuk" in _lower(ls.title) else ""
        prev_opens = rec.get("opens_at")
        if ls.opens_at is not None:
            rec["opens_at"] = _iso(ls.opens_at)
            rec["opens_pending"] = ls.opens_pending
        elif ls.detail_ok:  # the page was read and states no opening any more
            rec["opens_at"] = None
            rec["opens_pending"] = None
        prev_full = rec.get("full")
        if ls.full is not None:
            rec["full"] = bool(ls.full)
        if ls.detail_ok:
            rec["last_detail_at"] = now.isoformat()
        start = _dt(rec.get("start"))
        if start is not None and start < now - timedelta(hours=3):
            continue  # past event: bookkeeping only
        if not effective_follow(config, ls.id, ls.follow_default):
            continue
        opens = _dt(rec.get("opens_at"))
        if is_new and not baseline:
            events.extend(filter(None, [_emit(state, "SRC_NEW", f"SRC_NEW:{ls.id}", rec, now)]))
        elif not is_new and opens is not None and _iso(opens) != prev_opens:
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
        if now < opens:
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
    """Rows for the "Diğer kaynaklar" tab / Worker screen: future events by start."""
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
        rows.append({
            "id": eid, "source": rec.get("source", ""),
            "source_name": SOURCE_LABELS.get(rec.get("source"), str(rec.get("source"))),
            "title": str(rec.get("title") or eid), "start": start,
            "when": f"{start.astimezone(ISTANBUL):%d.%m.%Y %H:%M}" if start else "",
            "audience": str(rec.get("audience") or ""), "genre": str(rec.get("genre") or ""),
            "kind": str(rec.get("kind") or ""), "venue": str(rec.get("venue") or ""),
            "url": str(rec.get("url") or ""),
            "reservation": reservation_text(rec, now), "full": bool(rec.get("full")),
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

    def content(self) -> list:
        """Normalized parsed content (for the source's change time)."""
        return sorted([l.id, l.title, _iso(l.start), _iso(l.opens_at), l.full]
                      for l in self.listings)


def scan_basiskele(fetch_fn, cache: dict | None, now: datetime) -> SourceScan:
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
    for it in program_items(items):
        known[it.link] = {"title": it.title, "pub": _iso(it.published)}
    horizon = now - ARTICLE_TTL
    for link in [k for k, r in known.items()
                 if (_dt(r.get("pub")) or now) < horizon]:
        del known[link]
    newest = sorted(known, key=lambda k: known[k].get("pub") or "", reverse=True)[:MAX_ARTICLES]
    out.articles = {k: known[k] for k in newest}
    failed = 0
    for link in newest:
        try:
            page = fetch_fn(link).html
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
    return out


def _apply_detail(ls: Listing, detail: VkvDetail) -> None:
    ls.opens_at, ls.opens_pending, ls.full, ls.detail_ok = (
        detail.opens_at, detail.opens_pending if detail.opens_at else None, detail.full, True)


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
