"""Turkish HTML notification messages and a minimal Telegram client."""
from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable

import re

from tiyatro.models import ISTANBUL, Config, Event, Play
from tiyatro.timeutil import parse_iso

MAX_LEN = 4096
FOLD_NEW_PLAYS_ABOVE = 3
MAX_ATTEMPTS = 3
# Total 429 back-off per send stays below the 45 s claim lease, so a slow send can never
# outlive its claim (the other runner would take over and send a duplicate); beyond it
# the send fails and the claim ends `failed` (retryable).
MAX_TOTAL_WAIT = 35
TITLE_MAX = 200
NAME_MAX = 100
DETAIL_MAX = 500
URL_MAX = 1000

DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
          "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

KIND_TEXT = {
    "ON_SALE": "\U0001f39f️ Bilet alınabilir!",
    "OPENING_SOON": "⏰ 10 dk sonra satış açılıyor",
    "NEW_PLAY": "\U0001f195 Yeni oyun bulundu",
    "REMOVED_PLAY": "❌ Listeden kalktı",
    "UNKNOWN_STATUS": "⚠️ Bilinmeyen durum — sayfayı kontrol et",
    "PC_INACTIVE": "\U0001f5a5️ PC uygulaması aktif değil",
    "PC_DIVERGED": "🖥️ PC ile GitHub uyuşmuyor",
    "GH_LAGGING": "\U0001f422 GitHub taraması gecikiyor",
    "FETCH_FAILING": "\U0001f6ab Biletinial taraması başarısız",
    "PLAY_FAILING": "\U0001f9e9 Oyun sayfası okunamıyor",
    "NEW_SESSIONS": "\U0001f4c5 Yeni seanslar/dönem",
    "DELIVERY_DROPPED": "⚠️ bildirim iletilemedi",
    "CONFIG_INVALID": "⚠️ Ayar dosyası geçersiz",
    "OPENED_SOLD_OUT": "⚡ Satış açıldı ve görülmeden tükendi — iade için izleniyor",
}
PORTAL_TEXT = {
    "PORTAL_OPENED": "etkinlik açıldı",
    "PORTAL_FAILING": "okunamıyor",
    "PORTAL_NEW": "yeni etkinlik",
    "PORTAL_SEATS": "yer açıldı",
    "PORTAL_MARKUP": "sayfa düzeni değişmiş olabilir",
}
SOURCE_LABELS = {"basiskele": "Başiskele Belediyesi", "vkv": "VKV Gölcük",
                 "biletinial": "Biletinial"}
SRC_TEXT = {
    "SRC_NEW": "🆕 yeni etkinlik bulundu",
    "SRC_OPENS": "🗓️ rezervasyon açılış saati duyuruldu",
    "SRC_DAY_BEFORE": "🔔 yarın rezervasyon açılıyor",
    "SRC_SOON": "⏰ 10 dk sonra rezervasyon açılıyor",
    "SRC_OPENED": "🎟️ rezervasyon açıldı!",
    "SRC_REOPENED": "🎟️ rezervasyon yeniden açıldı!",
    "SRC_PROGRAM_MISSING": "⚠️ program haberi bulunamadı",
}
PORTAL_NOTE_ITEMS = 5
PORTAL_LABELS = {"basiskele": "Başiskele rezervasyon portalı"}
DAY_BEFORE_TEXT = "\U0001f514 Satış açılış hatırlatması: "


def _dt(value: str | None) -> datetime | None:
    try:
        d = parse_iso(value)
    except ValueError:
        return None
    return d.astimezone(ISTANBUL) if d else None


def _esc(value, limit: int) -> str:
    """Truncate (with an ellipsis) BEFORE escaping so entities are never cut."""
    text = str(value)
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return html.escape(text)


def _when(d: datetime) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {DAYS[d.weekday()]} {d:%H:%M}"


def _kind_line(ev: Event) -> str:
    if ev.kind == "DAY_BEFORE":
        opens = _dt(ev.payload.get("opens_at"))
        when = f"{DAYS[opens.weekday()]}, {opens:%H:%M}" if opens else "?"
        return DAY_BEFORE_TEXT + html.escape(when)
    if ev.kind == "DELIVERY_DROPPED":
        count = ev.payload.get("count", "?")
        return (f"⚠️ {_esc(count, 10)} bildirim iletilemedi: "
                f"{_esc(ev.payload.get('summary', ''), DETAIL_MAX)}")
    text = KIND_TEXT.get(ev.kind, html.escape(ev.kind))
    if ev.kind == "PLAY_FAILING":
        text += f": {_esc(ev.payload.get('title') or ev.play_slug, TITLE_MAX)}"
    if ev.kind == "OPENING_SOON":
        opens = _dt(ev.payload.get("opens_at"))
        if opens:
            text += f" ({opens:%H:%M})"
    return text


REMAINING_CAUTION_MAX = 10  # "Son N bilet" with N up to this also gets the caution line


def remaining_lines(value) -> list[str]:
    """Extra ON_SALE alert lines for the page's "Son N Bilet" badge (info only, never gates
    the alert). Nothing for a missing / invalid count."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 9999:
        return []
    lines = [f"🎫 Son {value} bilet"]
    if value <= REMAINING_CAUTION_MAX:
        lines.append(f"Sayfa “Son {value} bilet” diyor; koltuk haritası farklı olabilir.")
    return lines


def _title(ev: Event, plays: dict[str, Play]) -> str:
    title = ev.payload.get("title")
    if not title and ev.play_slug in plays:
        title = plays[ev.play_slug].title
    return _esc(title or ev.play_slug or "", TITLE_MAX)


def _targets(ev: Event, config: Config, ids: list[str]) -> str:
    watch = config.plays.get(ev.play_slug)
    if watch is None:
        return ""
    by_id = {t.id: t for t in watch.targets}
    return ", ".join(f"{_esc(by_id[i].name, NAME_MAX)} ({by_id[i].people} kişi)"
                     for i in ids if i in by_id)


def portal_label(portal_id, name) -> str:
    """Short display name of a reservation portal (also used by the PC app)."""
    return PORTAL_LABELS.get(portal_id) or str(name or portal_id or "Portal")


def portal_url(config, portal_id: str = "basiskele") -> str:
    """URL of an enabled reservation portal of ``config`` ("" when none / unusable). An absent
    ``config.portals`` means the default portal (as ``tiyatro.config``)."""
    from tiyatro.config import DEFAULT_PORTALS
    portals = getattr(config, "portals", None)
    if portals is None:
        portals = DEFAULT_PORTALS
    for p in portals:
        if isinstance(p, dict) and p.get("id") == portal_id and p.get("enabled", True):
            url = p.get("url")
            if isinstance(url, str) and re.match(r"^https?://\S+$", url):
                return url
    return ""


PORTAL_LINE = "🏛️ Rezervasyon portalı: "   # one line in Başiskele notices


def portal_line(payload, config) -> str | None:
    """The reservation-portal line for a notice about a Başiskele event (None for others)."""
    if not isinstance(payload, dict) or payload.get("source") != "basiskele":
        return None
    url = payload.get("portal_url") or portal_url(config)   # the event's own portal page if known
    return PORTAL_LINE + _esc(url, URL_MAX) if url else None


def portal_headline(ev: Event) -> str:
    """First line of a portal notification (also the desktop notification title)."""
    p = ev.payload if isinstance(ev.payload, dict) else {}
    label = portal_label(p.get("portal_id"), p.get("title"))
    sep = ": " if ev.kind != "PORTAL_FAILING" else " "
    return f"🏛️ {label}{sep}{PORTAL_TEXT.get(ev.kind, ev.kind)}"


def source_headline(ev: Event) -> str:
    """First line of a source-event notification (also the desktop notification title)."""
    p = ev.payload if isinstance(ev.payload, dict) else {}
    label = SOURCE_LABELS.get(p.get("source")) or str(p.get("source") or "Kaynak")
    return f"🎭 {label}: {SRC_TEXT.get(ev.kind, ev.kind)}"


NOT_FOLLOWED_NOTE = ("⚪ Takipte DEĞİL — takibe almak için menüden "
                     "(Oyunlar ve etkinlikler) aç.")
FOLLOWED_NOTE = "🟢 Takipte."


def _discovery_line(p: dict, label: str, what: str) -> str:
    """'🆕 Yeni oyun bulundu: <title> · <source> · <next date> · <link>' (one line)."""
    parts = [f"<b>{_esc(p.get('title') or '', TITLE_MAX)}</b>", html.escape(label)]
    start = _dt(p.get("start"))
    if start:
        parts.append(_when(start))
    if p.get("url"):
        parts.append(_esc(p["url"], URL_MAX))
    return f"🆕 Yeni {what} bulundu: " + " · ".join(parts)


DISCOVERY_KINDS = ("NEW_PLAY", "SRC_NEW")


def fold_discoveries(found) -> bool:
    """One digest instead of single notices: more than ``FOLD_NEW_PLAYS_ABOVE`` discoveries, or
    (wave 18) two or more new source EVENTS from one scan, e.g. events a parser improvement
    newly reads out of an article that was already known: the user wants to learn about them,
    but as ONE message."""
    found = list(found)
    return len(found) > FOLD_NEW_PLAYS_ABOVE or (
        len(found) >= 2 and all(e.kind == "SRC_NEW" for e in found))


def _source_label(p: dict) -> str:
    return SOURCE_LABELS.get(p.get("source")) or str(p.get("source") or "Kaynak")


def _discovery_note(p: dict) -> str:
    return FOLLOWED_NOTE if p.get("followed") is True else NOT_FOLLOWED_NOTE


def _new_play_entry(ev: Event) -> str:
    p = ev.payload if isinstance(ev.payload, dict) else {}
    return chr(10).join([_discovery_line(p, SOURCE_LABELS["biletinial"], "oyun"),
                         _discovery_note(p)])


def _source_entry(ev: Event, config=None) -> str:
    p = ev.payload if isinstance(ev.payload, dict) else {}
    if ev.kind == "SRC_NEW":
        label = SOURCE_LABELS.get(p.get("source")) or str(p.get("source") or "Kaynak")
        lines = [_discovery_line(p, label, "etkinlik"), _discovery_note(p)]
        lines.extend(_esc(x, TITLE_MAX) for x in p.get("contact") or [])
        extra = portal_line(p, config)
        if extra:
            lines.append(extra)
        return chr(10).join(lines)
    lines = [html.escape(source_headline(ev))]
    if p.get("title"):
        lines.append(f"<b>{_esc(p['title'], TITLE_MAX)}</b>")
    start = _dt(p.get("start"))
    if start:
        lines.append(f"📅 {_when(start)}")
    place = " · ".join(x for x in (str(p.get("kind") or ""), str(p.get("venue") or "")) if x)
    if place:
        lines.append(f"📍 {_esc(place, TITLE_MAX)}")
    opens = _dt(p.get("opens_at"))
    if opens and ev.kind != "SRC_OPENED":
        lines.append(f"📝 Rezervasyon: {_when(opens)}")
    if p.get("detail"):
        lines.append(_esc(p["detail"], DETAIL_MAX))
    for extra in p.get("contact") or []:
        lines.append(_esc(extra, TITLE_MAX))
    if p.get("url"):
        lines.append(f"🔗 {_esc(p['url'], URL_MAX)}")
    extra = portal_line(p, config)
    if extra:
        lines.append(extra)
    return chr(10).join(lines)


def _portal_event_line(e: dict) -> str:
    """"TITLE · 20.11 20:00 · venue · boş 350/590 · link" (one line, one link)."""
    parts = [f"<b>{_esc(e.get('title') or 'Etkinlik', TITLE_MAX)}</b>"]
    when = " ".join(x for x in (str(e.get("date") or "")[:5], str(e.get("time") or "")) if x)
    if when:
        parts.append(html.escape(when))
    if e.get("venue"):
        parts.append(_esc(e["venue"], NAME_MAX))
    total, free = e.get("total"), e.get("free")
    if isinstance(total, int) and isinstance(free, int) and not isinstance(total, bool):
        parts.append(f"DOLU {total}/{total}" if free <= 0 else f"boş {free}/{total}")
    if e.get("url"):
        parts.append(_esc(e["url"], URL_MAX))
    return " · ".join(parts)


def _portal_entry(ev: Event) -> str:
    p = ev.payload if isinstance(ev.payload, dict) else {}
    if ev.kind in ("PORTAL_NEW", "PORTAL_SEATS"):
        return _portal_events_entry(ev, p)
    lines = [html.escape(portal_headline(ev))]
    seen: set[str] = set()
    items = []
    for i in (p.get("items") or []):   # one bullet per distinct link
        if isinstance(i, dict) and (i.get("url") or "") not in seen:
            seen.add(i.get("url") or "")
            items.append(i)
    for i in items[:PORTAL_NOTE_ITEMS]:
        line = f"• {_esc(i.get('title') or 'Etkinlik', TITLE_MAX)}"
        if i.get("url") and i["url"] != p.get("url"):
            line += f" — {_esc(i['url'], URL_MAX)}"
        lines.append(line)
    try:
        total = max(len(items), int(p.get("item_count") or 0))
    except (TypeError, ValueError):
        total = len(items)
    if total > PORTAL_NOTE_ITEMS:
        lines.append(f"… +{total - PORTAL_NOTE_ITEMS} etkinlik daha")
    if not items:
        extra = [x for x in (p.get("lines") or []) if isinstance(x, str) and x.strip()]
        lines.extend(_esc(x, TITLE_MAX + 100) for x in extra[:PORTAL_NOTE_ITEMS])
    if p.get("detail"):
        lines.append(_esc(p["detail"], DETAIL_MAX))
    if p.get("url"):
        lines.append(f"🔗 {_esc(p['url'], URL_MAX)}")
    return chr(10).join(lines)


def _portal_events_entry(ev: Event, p: dict) -> str:
    """Wave 23 notices of the structured portal: header once, one line (one link) per event,
    the general portal link once at the bottom of a discovery message."""
    if ev.kind == "PORTAL_SEATS":
        e = p.get("event") if isinstance(p.get("event"), dict) else {}
        head = ("🎟️ Başiskele · YER AÇILDI — hemen rezervasyon yap!" if p.get("reason") != "opened"
                else "🎟️ Başiskele · rezervasyon açıldı — hemen rezervasyon yap!")
        return chr(10).join([head, _portal_event_line(e),
                             FOLLOWED_NOTE if p.get("followed") is True else NOT_FOLLOWED_NOTE])
    evs = [e for e in (p.get("events") or []) if isinstance(e, dict)]
    try:
        count = max(len(evs), int(p.get("event_count") or 0))
    except (TypeError, ValueError):
        count = len(evs)
    lines = ["🏛️ Başiskele · yeni etkinlik" if count <= 1
             else f"🏛️ Başiskele · {count} yeni etkinlik"]
    lines.extend(_portal_event_line(e) for e in evs)
    if count > len(evs):
        lines.append(f"… +{count - len(evs)} etkinlik daha")
    lines.append(FOLLOWED_NOTE if evs and all(e.get("followed") is True for e in evs)
                 else NOT_FOLLOWED_NOTE)
    if p.get("url"):
        lines.append(f"🏛️ Tüm etkinlikler: {_esc(p['url'], URL_MAX)}")
    return chr(10).join(lines)


KIND_FIRST = ("ON_SALE", "OPENING_SOON")  # lock-screen preview shows the first line


def _entry(ev: Event, plays: dict[str, Play], config: Config, ids: list[str]) -> str:
    if ev.kind in PORTAL_TEXT:
        return _portal_entry(ev)
    if ev.kind in SRC_TEXT:
        return _source_entry(ev, config)
    if ev.kind == "NEW_PLAY":
        return _new_play_entry(ev)
    lines = []
    if ev.kind in KIND_FIRST:
        lines.append(_kind_line(ev))
    title = _title(ev, plays)
    if title:
        lines.append(f"<b>{title}</b>")
    start = _dt(ev.payload.get("start"))
    if start and ev.session_id is not None:
        lines.append(f"\U0001f4c5 {_when(start)}")
    if ev.kind not in KIND_FIRST:
        lines.append(_kind_line(ev))
    if ev.kind == "ON_SALE":
        lines.extend(remaining_lines(ev.payload.get("remaining")))
    people = _targets(ev, config, ids)
    if people:
        lines.append(f"\U0001f465 {people}")
    url = ev.payload.get("url") or ev.payload.get("play_url")
    if url:
        lines.append(f"\U0001f517 {_esc(url, URL_MAX)}")
    detail = ev.payload.get("detail")
    if detail:
        lines.append(_esc(detail, DETAIL_MAX))
    extra = ev.payload.get("lines")
    if isinstance(extra, list):
        lines.extend(_esc(x, TITLE_MAX + 100) for x in extra)
    return "\n".join(lines)


_TOKEN = re.compile(r"<[^>]*>|&[#A-Za-z0-9]+;|.", re.S)
_OPEN = re.compile(r"<([A-Za-z]+)(?:\s[^>]*)?>$")
_CLOSE = re.compile(r"</([A-Za-z]+)>$")


def _split_line(line: str, limit: int) -> list[str]:
    """Split one over-long line without cutting a tag or entity.

    An element cut in two is closed at the end of the part and re-opened in the next.
    """
    out: list[str] = []
    cur = ""
    stack: list[tuple[str, str]] = []  # (name, opening tag)

    def closing() -> str:
        return "".join(f"</{n}>" for n, _ in reversed(stack))

    for tok in _TOKEN.findall(line):
        if len(cur) + len(tok) + len(closing()) > limit and cur.strip("".join(t for _, t in stack)):
            out.append(cur + closing())
            cur = "".join(t for _, t in stack)
        cur += tok
        m = _OPEN.match(tok)
        if m:
            stack.append((m.group(1), tok))
        else:
            m = _CLOSE.match(tok)
            if m and stack and stack[-1][0] == m.group(1):
                stack.pop()
    out.append(cur)
    return out


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split an over-long entry on line boundaries; tag/entity-safe for huge lines."""
    pieces: list[str] = []
    for line in text.split(chr(10)):
        pieces.extend(_split_line(line, limit) if len(line) > limit else [line])
    out: list[str] = []
    cur = ""
    for line in pieces:
        cand = cur + chr(10) + line if cur else line
        if len(cand) > limit:
            out.append(cur)
            cur = line
        else:
            cur = cand
    out.append(cur)
    return out


def format_message(events: list[Event], plays: dict[str, Play], config: Config) -> list[str]:
    entries: list[str] = []
    groups: dict[tuple, int] = {}  # (kind, slug, sid) -> position in entries_src
    entries_src: list = []  # ("ev", Event, ids) | ("new", [Event])
    new_plays: list[Event] = []
    new_index: int | None = None

    for ev in events:
        if ev.kind in DISCOVERY_KINDS:
            if new_index is None:
                new_index = len(entries_src)
                entries_src.append(("new", new_plays))
            new_plays.append(ev)
            continue
        gkey = (ev.kind, ev.play_slug, ev.session_id)
        if ev.session_id is not None and gkey in groups:
            ids = entries_src[groups[gkey]][2]
            ids.extend(i for i in ev.target_ids if i not in ids)
            continue
        if ev.session_id is not None:
            groups[gkey] = len(entries_src)
        entries_src.append(("ev", ev, list(ev.target_ids)))

    for item in entries_src:
        if item[0] == "ev":
            entries.append(_entry(item[1], plays, config, item[2]))
        elif fold_discoveries(item[1]):
            lines = [f"🆕 Yeni oyun/etkinlikler bulundu ({len(item[1])})"]
            for e in item[1]:
                url = e.payload.get("url")
                line = f"• {_title(e, plays)} · {_source_label(e.payload)}"
                start = _dt(e.payload.get("start"))
                if start:
                    line += f" · {_when(start)}"
                if url:
                    line += f" · {_esc(url, URL_MAX)}"
                lines.append(line)
            extra = next((x for x in (portal_line(e.payload, config) for e in item[1]) if x),
                         None)
            if extra:
                lines.append(extra)
            lines.append(FOLLOWED_NOTE if all(e.payload.get("followed") is True
                                              for e in item[1]) else NOT_FOLLOWED_NOTE)
            entries.append("\n".join(lines))
        else:
            entries.extend(_entry(e, plays, config, []) for e in item[1])

    parts: list[str] = []
    cur = ""
    for entry in entries:
        for chunk in (_split(entry) if len(entry) > MAX_LEN else [entry]):
            cand = f"{cur}\n\n{chunk}" if cur else chunk
            if len(cand) > MAX_LEN:
                parts.append(cur)
                cur = chunk
            else:
                cur = cand
    if cur:
        parts.append(cur)
    return [dedupe_urls(part) for part in parts]


_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_LABEL_ONLY = re.compile(r"(?:🔗|🏛️|🌐|•)?\s*(?:Rezervasyon portalı|Tüm etkinlikler)?\s*:?")


def dedupe_urls(text: str) -> str:
    """One occurrence of every URL in a message: a repeat is removed together with its
    separator, and a line that held nothing else but a label and the repeat is dropped."""
    seen: set[str] = set()
    out = []
    for line in text.split(chr(10)):
        urls = _URL_RE.findall(line)
        if not any(u in seen for u in urls):
            seen.update(urls)
            out.append(line)
            continue
        kept = line
        for u in urls:
            if u in seen:
                kept = re.sub(r"\s*[·—–-]?\s*" + re.escape(u), "", kept, count=1)
            seen.add(u)
        if not _LABEL_ONLY.fullmatch(re.sub(r"<[^>]*>", "", kept).strip()):
            out.append(kept.rstrip(" ·—–-:"))
    return chr(10).join(out)




class TelegramError(Exception):
    pass


Transport = Callable[[str, dict], "tuple[int, dict]"]


def default_transport(url: str, payload: dict) -> tuple[int, dict]:
    """POST JSON via urllib (20 s timeout). Never leaks the URL/token in errors."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return exc.code, body
    except Exception as exc:
        raise TelegramError(f"telegram transport error: {type(exc).__name__}") from None


class Telegram:
    def __init__(self, token: str, chat_id: str, transport: Transport = default_transport,
                 sleep: Callable[[float], None] = time.sleep):
        self._token = token
        self.chat_id = chat_id
        self._transport = transport
        self._sleep = sleep

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    @staticmethod
    def _retry_after(body: dict) -> float:
        raw = (body.get("parameters") or {}).get("retry_after", 1)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 1.0

    def send(self, text: str) -> int:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        waited = 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            # Errors are recorded inside `except` and raised outside it, so the token can
            # never survive in an exception's __context__.
            failure: str | None = None
            status, body = 0, {}
            try:
                status, body = self._transport(url, payload)
            except TelegramError as exc:
                failure = self._scrub(str(exc))
            except Exception as exc:
                failure = f"telegram transport error: {type(exc).__name__}"
            if failure is not None:
                raise TelegramError(failure)
            body = body if isinstance(body, dict) else {}
            if status == 429:
                wait = self._retry_after(body)
                if waited + wait > MAX_TOTAL_WAIT:
                    raise TelegramError(f"telegram rate limited: retry_after={wait:g}s too long")
                if attempt < MAX_ATTEMPTS:
                    self._sleep(int(wait) if wait == int(wait) else wait)
                    waited += wait
                    continue
            if status == 200 and body.get("ok") is True:
                result = body.get("result")
                mid = result.get("message_id") if isinstance(result, dict) else None
                # ok without an id: the message was most likely delivered; do not re-send.
                return mid if isinstance(mid, int) else 0
            desc = self._scrub(str(body.get("description", "")))
            raise TelegramError(f"telegram error {status}: {desc}")
        raise TelegramError("telegram error: retries exhausted")  # pragma: no cover
