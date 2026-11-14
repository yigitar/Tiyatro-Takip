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
from urllib.parse import urljoin, urlsplit

DEFAULT_BASE_URL = "https://rezervasyon.basiskele.bel.tr/etkinlikler"
MAX_HTML = 2_000_000
MAX_LINES = 200
MAX_LINE = 300
MAX_ITEMS = 50
MAX_DEPTH = 300
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
})
_KEYWORDS = ("rezerv", "etkinlik", "bilet", "kayit", "katil", "basvur", "detay", "ayirt",
             "tiyatro", "konser", "gosteri", "sinema", "festival")
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


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root

    def _open(self, tag, attrs, selfclose=False):
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
    f = fold(line)
    return f in CHROME_LINES or any(p.search(f) for p in _CHROME_PATTERNS)


def _date_only(line: str) -> bool:
    rest = _DATE_WORDS.sub("", fold(line))
    return not re.sub(r"[\d\s.:/,\-–]", "", rest)


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
    hay = f + " " + fold(url if url != base else "")
    if not any(k in hay for k in _KEYWORDS):
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
    raw = "\n".join(lines) + "\n--\n" + "\n".join(i["url"] for i in items)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


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
    try:
        root = _build(html)
        seen, lines = set(), []
        for line in _lines_of(root):
            if _is_chrome_line(line):
                continue
            line = line[:MAX_LINE]
            if line not in seen or len(line) < CONTENT_LINE_MIN:
                lines.append(line)
            seen.add(line)
            if len(lines) >= MAX_LINES:
                break
        cands = {n: c for n in _walk(root) if (c := _candidate(n, base_url))}
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
        substantial = [l for l in lines if len(l) >= CONTENT_LINE_MIN]
        empty = not items and len(substantial) < CONTENT_LINES_NEEDED
        return PortalSnapshot(lines, items, empty, _signature(lines, items))
    except Exception:  # a parser bug must not turn into a crash of the scan
        return _fallback(html, base_url)
