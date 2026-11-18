"""Generic reader for a municipality reservation portal page ("rezervasyon portalı").

The Başiskele portal (ASP.NET WebForms) only lists events whose free reservation is
currently open; most of the time the list is empty. The markup of a populated list has
never been observed, so nothing here depends on one: the page is reduced to its visible
text lines minus known site chrome, plus "candidate items" (links/buttons whose text or
target suggests an event or a reservation). ``empty`` is a conservative verdict (no items
and fewer than two substantial content lines left), so a redesign that only adds chrome
text cannot raise a false "opened"; a redesign that adds real text can, which is the
safer side for an alert whose purpose is to catch a rare opening.

Pure and stdlib-only; never raises on bad input.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from tiyatro.models import ISTANBUL

DEFAULT_BASE_URL = "https://rezervasyon.basiskele.bel.tr/etkinlikler"
MAX_HTML = 500_000
MAX_LINES = 200
MAX_LINE = 300
MAX_ITEMS = 50
MAX_DEPTH = 300
MAX_TAGS = 20_000   # parsing stops after this many start tags (a real portal has hundreds)
CONTENT_LINE_MIN = 20   # a "substantial" line
CONTENT_LINES_NEEDED = 2

_TR = str.maketrans({"ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i", "I": "i",
                     "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"})

# Lines (folded) that belong to the site frame, not to an event: the visible text of the
# real empty page plus the usual header/nav/footer vocabulary.
CHROME_LINES = frozenset({
    "basiskele belediyesi rezervasyon", "rezervasyon", "etkinlikler", "etkinlik", "ana sayfa",
    "anasayfa", "giris", "giris yap", "cikis", "cikis yap", "iletisim", "hakkimizda", "menu",
    "geri", "yukari", "yukari cik", "gezinti baglantilarini atla", "turkce", "english",
    "tr", "en", "arama", "ara", "kvkk", "gizlilik", "cerez politikasi",
})
_CHROME_PATTERNS = (
    re.compile(r"\S+@\S+\.\S+"),                              # e-mail
    re.compile(r"(?<!\d)\d{3}[\s.-]\d{2}[\s.-]\d{2}(?!\d)"),   # 444 41 10
    re.compile(r"yazilim\s*k7|tum haklari saklidir|copyright|©"),
)
_CHROME_CLASS_TOKENS = frozenset({"nav", "navbar", "menu", "breadcrumb", "breadcrumbs",
                                  "topbar", "navigation", "sitemap"})

# Text that names an action rather than the event: when a link says only this, the title
# is taken from the surrounding card.
GENERIC_ACTIONS = frozenset({
    "rezervasyon yap", "rezervasyon yapin", "rezervasyon", "rezerve et", "rezerve", "detay",
    "detaylar", "detayli bilgi", "devami", "devamini oku", "incele", "kayit ol", "katil",
    "basvur", "bilgi al", "yer ayirt", "ayirt", "tikla", "tiklayin", "sec", "git",
    "daha fazla", "daha fazlasi", "tumunu gor", "kabul et",
})
_KEYWORDS = ("rezerv", "etkinlik", "bilet", "katil", "basvur", "ayirt",
             "tiyatro", "konser", "gosteri", "sinema", "festival")
_WEAK_KEYWORDS = ("detay", "kayit")        # count only together with an event path
_EVENT_PATH = ("etkinlik", "rezerv")
# Pager / consent / navigation controls: never an event, whatever their href says.
_PAGER_TEXT = frozenset({     # excluded whatever the href says
    "sonraki", "sonraki sayfa", "onceki", "onceki sayfa", "ilk", "son", "ilk sayfa",
    "son sayfa", "geri", "ileri",
})
_CONSENT_TEXT = frozenset({   # excluded unless the href is under an event/reservation path
    "kabul et", "kabul ediyorum", "tamam", "reddet", "kapat", "detayli bilgi", "daha fazla",
    "anladim", "ok", "yukle", "yenile", "ara", "giris",
})
_NOT_ITEM_TEXT = _PAGER_TEXT | _CONSENT_TEXT
_NOT_ITEM_WORDS = ("cerez", "cookie", "kvkk", "gizlilik")
# Text that says "nothing here" / "something is wrong": a page made only of such lines is empty.
_NEGATIVE = ("bulunamad", "bulunmamakta", "bulunmuyor", "hata olustu", "hata olu", "bakim",
             "erisim", "izin", "cerez", "cookie", "kvkk", "gizlilik", "session", "oturum",
             "captcha", "dogrulama", "unauthorized", "forbidden", "server error",
             "sunucu hatasi", "error", "exception", "hatasi", "tekrar deneyin",
             "tekrar deneyiniz", "access denied", "blocked", "firewall")
_NOT_FOUND = ("bulunamad", "bulunmamakta", "bulunmuyor")
_VOLATILE_WORDS = re.compile(
    r"son guncelleme|guncelleme|guncellenme|ziyaretci|ziyaret|sayac|sayisi|toplam|online|"
    r"cevrimici|bugun|zaman|sayi")
_VOLATILE_PARAMS = frozenset({"ts", "t", "_", "time", "timestamp", "rnd", "rand", "random",
                              "nocache", "cache", "cb", "v", "sid", "sessionid", "session",
                              "token", "nonce", "r"})
_SESSION_SEGMENT = re.compile(r"/\([A-Za-z]\([^)]*\)\)")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DATE_WORDS = re.compile(
    r"ocak|subat|mart|nisan|mayis|haziran|temmuz|agustos|eylul|ekim|kasim|aralik|"
    r"pazartesi|sali|carsamba|persembe|cuma|cumartesi|pazar|saat|tarih|gun")
_SELF_PATHS = {"", "/", "/default", "/default.aspx", "/index", "/index.aspx", "/home"}

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                   "source", "track", "wbr", "param"})
_SKIP = frozenset({"script", "style", "noscript", "template", "head", "title", "svg",
                   "iframe", "select", "textarea", "object", "canvas"})
_BLOCK = frozenset({"address", "article", "aside", "blockquote", "br", "caption", "center",
                    "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
                    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
                    "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
                    "thead", "tr", "ul", "button", "option"})
_CLOSES = {"li": ("li", {"ul", "ol"}), "td": ("td", {"tr"}), "th": ("th", {"tr"}),
           "tr": ("tr", {"table"}), "p": ("p", {"div", "td", "li", "section", "article"}),
           "option": ("option", {"select"})}


@dataclass
class PortalSnapshot:
    lines: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    empty: bool = True
    signature: str = ""
    valid: bool = True   # False: the page is garbled/binary, to be treated as a failed fetch
    # Wave 23: structured event cards (see ``parse_events``); ``structured`` is True when at
    # least one card was read, and then ``items``/``lines`` are derived from them.
    events: list[dict] = field(default_factory=list)
    structured: bool = False


def fold(text: str) -> str:
    """Lower-case ASCII-ish form for comparisons (Turkish letters folded, spaces collapsed)."""
    return re.sub(r"\s+", " ", str(text).translate(_TR).lower()).strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "hidden", "chrome", "depth")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent
        self.depth = parent.depth + 1 if parent is not None else 0
        self.hidden = bool(parent and parent.hidden)
        self.chrome = bool(parent and parent.chrome)


def _is_hidden(tag: str, a: dict) -> bool:
    style = re.sub(r"\s+", "", (a.get("style") or "").lower())
    return (tag in _SKIP or "hidden" in a or a.get("aria-hidden") == "true"
            or "display:none" in style or "visibility:hidden" in style
            or (tag == "input" and (a.get("type") or "").lower() == "hidden"))


def _is_chrome(node: _Node) -> bool:
    a, tag = node.attrs, node.tag
    if tag in ("nav", "footer"):
        return True
    if tag == "header":  # a <header> inside a card is its title, not the site header
        p = node.parent
        while p is not None:
            if p.tag in ("article", "li", "tr", "section"):
                return False
            p = p.parent
        return True
    if _CHROME_CLASS_TOKENS & set((a.get("class") or "").lower().split()):
        return True
    return "sitemap" in (a.get("id") or "").lower()


class _TooBig(Exception):
    pass


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root
        self.tags = 0

    def _open(self, tag, attrs, selfclose=False):
        self.tags += 1
        if self.tags > MAX_TAGS:
            raise _TooBig()
        a = {k.lower(): (v or "") for k, v in attrs}
        rule = _CLOSES.get(tag)
        if rule:
            name, bounds = rule
            n = self.cur
            while n is not self.root and n.tag not in bounds:
                if n.tag == name:
                    self.cur = n.parent
                    break
                n = n.parent
        parent = self.cur
        if parent.depth >= MAX_DEPTH:  # absurd nesting: keep it flat instead of recursing
            parent = self.root
        node = _Node(tag, a, parent)
        node.hidden = node.hidden or _is_hidden(tag, a)
        node.chrome = node.chrome or _is_chrome(node)
        parent.children.append(node)
        if tag not in _VOID and not selfclose:
            self.cur = node

    def handle_starttag(self, tag, attrs):
        self._open(tag.lower(), attrs)

    def handle_startendtag(self, tag, attrs):
        self._open(tag.lower(), attrs, True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        n = self.cur
        while n is not self.root:
            if n.tag == tag:
                self.cur = n.parent
                return
            n = n.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def _build(html: str) -> _Node:
    b = _Builder()
    try:
        b.feed(html)
        b.close()
    except Exception:  # malformed markup: keep what was built
        pass
    return b.root


def _tokens(node: _Node, include_chrome: bool = False):
    """Visible text tokens of a subtree, iteratively: str, or None for a line break."""
    stack: list = [(node, False)]
    while stack:
        n, closing = stack.pop()
        if isinstance(n, str):
            yield n
            continue
        if closing:
            if n.tag in _BLOCK:
                yield None
            continue
        if n.hidden or (n.chrome and not include_chrome):
            continue
        if n.tag in _BLOCK:
            yield None
        stack.append((n, True))
        for child in reversed(n.children):
            stack.append((child, False))


def _lines_of(node: _Node) -> list[str]:
    out, cur = [], []
    for tok in _tokens(node):
        if tok is None:
            if cur:
                out.append(_norm("".join(cur)))
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(_norm("".join(cur)))
    return [l for l in out if l]


def _is_chrome_line(line: str) -> bool:
    f = fold(line[:MAX_LINE])  # bounded: the e-mail pattern backtracks on very long lines
    return f in CHROME_LINES or any(p.search(f) for p in _CHROME_PATTERNS)


def _date_only(line: str) -> bool:
    rest = _DATE_WORDS.sub("", fold(line))
    return not re.sub(r"[\d\s.:/,\-–]", "", rest)


def _volatile_line(line: str) -> bool:
    """A clock, counter or date on its own line: changes without the content changing."""
    f = fold(line)
    for phrase in sorted(_NOT_ITEM_TEXT, key=len, reverse=True):  # consent/pager link text
        f = f.replace(phrase, "")
    rest = _DATE_WORDS.sub("", _VOLATILE_WORDS.sub("", f))
    return not re.sub(r"[\d\s.:/,\-–|]", "", rest)


def _negative(line: str) -> bool:
    f = fold(line)
    return any(k in f for k in _NEGATIVE)


def _clean_url(url: str) -> str:
    """URL without fragment, ASP.NET cookieless-session segments and volatile parameters."""
    try:
        p = urlsplit(_SESSION_SEGMENT.sub("", url))
        q = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if k.lower() not in _VOLATILE_PARAMS and not k.startswith("__"))
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))
    except Exception:
        return url


_DATE_TIME = re.compile(
    r"\d{1,2}[.:/]\d{2}|\d{1,2}\s*(?:ocak|subat|mart|nisan|mayis|haziran|temmuz|agustos|"
    r"eylul|ekim|kasim|aralik)")


def has_date(line: str) -> bool:
    """A date or a time in the line: such a line is content, never an error message."""
    return bool(_DATE_TIME.search(fold(line[:MAX_LINE])))


_COUNTER_TITLE = re.compile(r"koltuk|kontenjan|kalan|sayisi|\bbos\b|\bdolu\b|rezerve")


def stable_title(title: str) -> str:
    """Folded title; digits are dropped from a seat-counter line ("Rezerve Edilmiş Koltuk
    Sayısı: 173"): the number moves with every reservation, the item does not."""
    f = fold(title)
    return re.sub(r"\d+", "#", f) if _COUNTER_TITLE.search(f) else f


def item_key(item: dict) -> str:
    """Short stable identity of an item (folded title + cleaned URL)."""
    raw = stable_title(item.get("title", "")) + "\t" + _clean_url(item.get("url", ""))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:10]


def _garbled(html: str) -> bool:
    return bool(_CTRL.search(html)) or html.count("\ufffd") > max(20, len(html) // 50)


def _titleish(line: str) -> bool:
    f = fold(line)
    return (len(f) >= 3 and f not in GENERIC_ACTIONS and not _date_only(line)
            and not _is_chrome_line(line))


def _own_text(node: _Node) -> str:
    return _norm(" ".join(_lines_of(node)))


def _walk(node: _Node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(c for c in reversed(n.children) if not isinstance(c, str))


def _candidate(n: _Node, base: str):
    """(text, url) if ``n`` looks like an event/reservation control, else None."""
    if n.hidden or n.chrome:
        return None
    a = n.attrs
    if n.tag == "a":
        text, href = _own_text(n), (a.get("href") or "").strip()
        if not href:
            return None
        low = href.lower()
        if low.startswith("javascript:"):
            if "dopostback" not in low:
                return None
            url = base
        elif low.startswith(("#", "tel:", "mailto:", "sms:", "data:")):
            return None
        else:
            url = urljoin(base, href)
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https"):
                return None
            if parts.path.rstrip("/").lower() in _SELF_PATHS and not parts.query:
                return None
            if url.split("#")[0] == base.split("#")[0]:
                return None
    elif n.tag == "input" and (a.get("type") or "").lower() in ("submit", "button", "image"):
        text, url = _norm(a.get("value") or a.get("alt") or ""), base
    elif n.tag == "button":
        text, url = _own_text(n), base
    else:
        return None
    f = fold(text)
    if f in CHROME_LINES and f not in GENERIC_ACTIONS:
        return None
    if (f in _PAGER_TEXT or re.fullmatch(r"[\d\s.<>«»…-]*", f)
            or "page$" in (a.get("href") or "").lower()):
        return None
    parts = urlsplit(url)
    path = fold(parts.path + "?" + parts.query) if url != base else ""  # never the host
    # a link under an event/reservation path is an event whatever its words say
    if not any(k in path for k in _EVENT_PATH) and (
            f in _CONSENT_TEXT or any(w in f for w in _NOT_ITEM_WORDS)
            or _is_chrome_line(text)):
        return None
    hay = f + " " + path
    strong = any(k in hay for k in _KEYWORDS)
    weak = any(k in hay for k in _WEAK_KEYWORDS) and any(k in path for k in _EVENT_PATH)
    # a content link of the portal itself whose text reads like a title (title-only rows)
    plain = (n.tag == "a" and _titleish(text) and len(f) >= 4
             and (url == base or urlsplit(url).netloc == urlsplit(base).netloc))
    if not (strong or weak or plain):
        return None
    return text, url


def _item(n: _Node, text: str, url: str, count: dict, root: _Node) -> dict:
    if _titleish(text):
        return {"title": text[:120], "url": url, "info": ""}
    cur = n
    for _ in range(6):
        cur = cur.parent
        if cur is None or cur is root or cur.tag in ("body", "html", "form"):
            break
        if count.get(id(cur), 0) > 1:
            break
        lines = [l for l in _lines_of(cur)
                 if fold(l) not in GENERIC_ACTIONS and not _is_chrome_line(l)]
        title = next((l for l in lines if _titleish(l)), None)
        if title is not None:
            rest = [l for l in lines if l is not title]
            return {"title": title[:120], "url": url, "info": " · ".join(rest)[:160]}
    return {"title": "Etkinlik", "url": url, "info": ""}


def _candidate_counts(root: _Node, cands: dict) -> dict:
    """id(ancestor) -> number of distinct candidate controls (text, url) inside it."""
    found: dict = {}
    for n, key in cands.items():
        p = n.parent
        while p is not None:
            found.setdefault(id(p), set()).add(key)
            p = p.parent
    return {k: len(v) for k, v in found.items()}


def _signature(lines: list[str], items: list[dict]) -> str:
    """With items: the SORTED SET of (title, cleaned url), so clocks, counters, order and
    volatile URL parameters do not matter; without: the (non-volatile) content lines."""
    if items:
        raw = "\n".join(sorted({stable_title(i["title"]) + "\t" + _clean_url(i["url"])
                                for i in items}))
    else:
        raw = "\n".join(lines)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


# ---- structured event cards (wave 23) ---------------------------------------------------
#
# Real markup (observed 2026-11-18): one card per event with the title, "20.11.2026 - 20:00",
# the venue, "Mevcut Koltuk Sayısı: 590" (total seats), "Rezerve Edilmiş Koltuk Sayısı: 240"
# (reserved seats) and the buttons Rezervasyon Yap / Doğrula / İptal; every text line is also
# a link to the event page /etkinlik-d/0/<id>/<slug>.aspx. A card is found by its seat-total
# line (not by ids or classes), so a redesign that keeps the wording keeps working.

MAX_EVENTS = 60
_TOTAL_RE = re.compile(r"mevcut koltuk sayisi\s*:?\s*(\d{1,6})")
_RESERVED_RE = re.compile(r"rezerve edilmis koltuk sayisi\s*:?\s*(\d{1,6})")
_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
_CLOCK_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_EVENT_URL_ID = re.compile(r"/etkinlik-d/\d+/(\d+)(?:/|$)")
_ANY_URL_ID = re.compile(r"/(?:etkinlik-d|bilet-al|bilet-dogrula|bilet-iptal)/\d+/(\d+)(?:/|$)")
_BUTTON_WORDS = ("rezervasyon", "dogrula", "iptal", "bilet")


def _count_after(rx, line: str):
    m = rx.search(fold(line))
    return int(m.group(1)) if m else None


def _card_of(anchor: _Node, holders: dict) -> _Node:
    """Highest ancestor of ``anchor`` that holds no other card's seat-total line."""
    card, cur = anchor, anchor.parent
    while cur is not None and cur.tag not in ("body", "html", "#root", "form"):
        if holders.get(id(cur), 0) > 1:
            break
        card, cur = cur, cur.parent
    return card


def _card_event(card: _Node, base: str) -> dict | None:
    total = reserved = None
    rest = []
    for line in _lines_of(card):
        f = fold(line)
        if total is None and (n := _count_after(_TOTAL_RE, line)) is not None:
            total = n
        elif reserved is None and (n := _count_after(_RESERVED_RE, line)) is not None:
            reserved = n
        elif "koltuk" in f and re.search(r"\d", f):
            continue
        else:
            rest.append(line)
    date_line = next((l for l in rest if _DATE_RE.search(l)), None)
    start = time_ = None
    day = None
    if date_line:
        d = _DATE_RE.search(date_line)
        c = _CLOCK_RE.search(date_line[d.end():])
        try:
            day = (int(d.group(3)), int(d.group(2)), int(d.group(1)))
            hh, mm = (int(c.group(1)), int(c.group(2))) if c else (0, 0)
            start = datetime(*day, hh, mm, tzinfo=ISTANBUL).isoformat()
            time_ = f"{hh:02d}:{mm:02d}" if c else ""
        except ValueError:
            start = time_ = day = None
    content = [l for l in rest if l is not date_line
               and not any(fold(l).startswith(w) for w in _BUTTON_WORDS)
               and fold(l) not in GENERIC_ACTIONS and not _is_chrome_line(l)]
    title = next((l for l in content if _titleish(l)), "")
    venue = ""
    if title:
        after = content[content.index(title) + 1:]
        venue = after[0] if after else ""
    url = reserve_url = ""
    bookable = None
    for n in _walk(card):
        if n.tag != "a" or n.chrome:
            continue
        href = (n.attrs.get("href") or "").strip()
        if not href or href.lower().startswith(("javascript:", "#", "tel:", "mailto:")):
            continue
        absolute = urljoin(base, href)
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        path = urlsplit(absolute).path.lower()
        if "bilet-al" in path or fold(_own_text(n)).startswith("rezervasyon yap"):
            reserve_url = reserve_url or absolute
            bookable = (not n.hidden) if bookable is None else (bookable and not n.hidden)
        elif not url and "/etkinlik" in path:
            url = absolute
    if not title or (total is None and reserved is None):
        return None
    m = _EVENT_URL_ID.search(url) or _ANY_URL_ID.search(reserve_url)
    eid = m.group(1) if m else hashlib.sha1(
        (fold(title) + (start or "")).encode("utf-8")).hexdigest()[:8]
    ev = {"id": eid, "title": title[:120], "start": start,
          "date": f"{day[2]:02d}.{day[1]:02d}.{day[0]}" if day else "", "time": time_ or "",
          "venue": venue[:120], "total": total, "reserved": reserved,
          "url": url or reserve_url, "bookable": bookable}
    ev["free"] = (max(total - reserved, 0) if total is not None and reserved is not None
                  else None)
    ev["status"] = seat_status(ev)
    return ev


def seat_status(ev: dict) -> str:
    """"full" (no free seat), "closed" (the reservation button is hidden), "open" (free seats),
    "unknown" (the counters were not read). The counters decide: the button stays visible on
    a full event (observed 2026-11-18)."""
    free = ev.get("free")
    if free is not None and ev.get("total") is not None:
        if free <= 0:
            return "full"
        return "closed" if ev.get("bookable") is False else "open"
    return "closed" if ev.get("bookable") is False else "unknown"


def parse_events(root: _Node, base: str) -> list[dict]:
    """Structured event cards of a portal page ([] when none is recognisable)."""
    anchors = []
    for n in _walk(root):
        if n.hidden or n.chrome or n.tag in ("script", "style"):
            continue
        # cheap pre-check (the text itself must name the seats): the full text of a node is
        # only built for the few nodes that do, never for every node of a huge page
        if not any(isinstance(c, str) and "koltuk" in fold(c) for c in n.children):
            continue
        if _TOTAL_RE.search(fold(_own_text(n))):
            anchors.append(n)
            if len(anchors) > MAX_EVENTS * 3:
                break
    holders: dict = {}
    for a in anchors:
        p = a.parent
        while p is not None:
            holders[id(p)] = holders.get(id(p), 0) + 1
            p = p.parent
    events, seen = [], set()
    for a in anchors:
        ev = _card_event(_card_of(a, holders), base)
        if ev and ev["id"] not in seen:
            seen.add(ev["id"])
            events.append(ev)
            if len(events) >= MAX_EVENTS:
                break
    return events


def seats_text(ev: dict) -> str:
    """"boş 350/590", "DOLU 590/590" or "" (counters unknown)."""
    total, free = ev.get("total"), ev.get("free")
    if total is None or free is None:
        return ""
    return f"DOLU {total}/{total}" if free <= 0 else f"boş {free}/{total}"


def event_line(ev: dict) -> str:
    """One short line per event: "TITLE · 20.11 20:00 · venue · boş 350/590"."""
    parts = [ev["title"]]
    if ev.get("date"):
        parts.append(ev["date"][:5] + (f" {ev['time']}" if ev.get("time") else ""))
    if ev.get("venue"):
        parts.append(ev["venue"])
    if seats_text(ev):
        parts.append(seats_text(ev))
    return " · ".join(parts)


def _structured_snapshot(events: list[dict]) -> PortalSnapshot:
    items = [{"title": e["title"], "url": e["url"], "info": event_line(e)} for e in events]
    # identity only (id, title, start): seat counters / status never change the signature
    raw = "\n".join(sorted(f"{e['id']}\t{fold(e['title'])}\t{e['start'] or ''}" for e in events))
    return PortalSnapshot([event_line(e) for e in events], items, False,
                          hashlib.sha1(raw.encode("utf-8")).hexdigest(), True,
                          [dict(e) for e in events], True)


def norm_title(text) -> str:
    """Folded title for matching an article event with a portal event."""
    f = re.sub(r"[^a-z0-9 ]+", " ", fold(str(text or "")))
    f = re.sub(r"\b(programi|etkinligi|gosterisi)\b", " ", f)
    return re.sub(r"\s+", " ", f).strip()


def match_portal_event(state, title, start) -> tuple[str, dict] | None:
    """(portal id, stored event) with the same normalised title and Istanbul date as ``title``
    / ``start`` (ISO string); the same clock time wins when several share the day. None when
    nothing, or nothing unambiguous, matches."""
    portals = state.get("portals") if isinstance(state, dict) else None
    if not isinstance(portals, dict) or not isinstance(start, str) or not start:
        return None
    want, day, clock = norm_title(title), start[:10], start[11:16]
    if not want:
        return None
    found = []
    for pid, rec in portals.items():
        evs = rec.get("events") if isinstance(rec, dict) else None
        for ev in (evs.values() if isinstance(evs, dict) else ()):
            if (isinstance(ev, dict) and norm_title(ev.get("title")) == want
                    and str(ev.get("start") or "")[:10] == day):
                found.append((pid, ev))
    if len(found) > 1:
        same = [f for f in found if str(f[1].get("start") or "")[11:16] == clock]
        found = same if len(same) == 1 else []
    return found[0] if len(found) == 1 else None


def _fallback(html: str, base: str) -> PortalSnapshot:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    lines = [_norm(l) for l in re.sub(r"<[^>]*>", "\n", text).split("\n")]
    lines = [l[:MAX_LINE] for l in lines if l and not _is_chrome_line(l)][:MAX_LINES]
    big = [l for l in lines if len(l) >= CONTENT_LINE_MIN]
    return PortalSnapshot(lines, [], len(big) < CONTENT_LINES_NEEDED, _signature(lines, []))


def parse_portal(html, base_url: str = DEFAULT_BASE_URL) -> PortalSnapshot:
    if isinstance(html, (bytes, bytearray)):
        html = bytes(html).decode("utf-8", "replace")
    if not isinstance(html, str):
        html = ""
    html = html[:MAX_HTML]
    if _garbled(html):
        return PortalSnapshot([], [], True, _signature([], []), False)
    try:
        root = _build(html)
        seen, lines = set(), []
        for line in _lines_of(root):
            if _is_chrome_line(line) or _volatile_line(line):
                continue
            line = line[:MAX_LINE]
            if line not in seen or len(line) < CONTENT_LINE_MIN:
                lines.append(line)
            seen.add(line)
            if len(lines) >= MAX_LINES:
                break
        cands: dict = {}
        for n in _walk(root):
            if (c := _candidate(n, base_url)):
                cands[n] = c
                if len(cands) >= MAX_ITEMS * 3:
                    break
        count = _candidate_counts(root, cands)
        items, keys = [], set()
        for n, (text, url) in cands.items():
            item = _item(n, text, url, count, root)
            key = (item["title"], item["url"])
            if key in keys:
                continue
            keys.add(key)
            items.append(item)
            if len(items) >= MAX_ITEMS:
                break
        events = parse_events(root, base_url)
        if events:   # structured cards: the generic reading is only the fallback
            return _structured_snapshot(events)
        substantial = [l for l in lines if len(l) >= CONTENT_LINE_MIN]
        empty = False
        if not items:
            undated = [l for l in lines if not has_date(l)]
            empty = (len(substantial) < CONTENT_LINES_NEEDED
                     or any(k in fold(l) for l in undated for k in _NOT_FOUND)
                     or all(_negative(l) and not has_date(l) for l in substantial))
        return PortalSnapshot(lines, items, empty, _signature(lines, items))
    except Exception:  # a parser bug must not turn into a crash of the scan
        return _fallback(html, base_url)
