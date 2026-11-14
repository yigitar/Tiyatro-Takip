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
    "NEW_PLAY": "\U0001f195 Yeni oyun",
    "REMOVED_PLAY": "❌ Listeden kalktı",
    "UNKNOWN_STATUS": "⚠️ Bilinmeyen durum — sayfayı kontrol et",
    "PC_INACTIVE": "\U0001f5a5️ PC uygulaması aktif değil",
    "PC_DIVERGED": "🖥️ PC ile GitHub uyuşmuyor",
    "GH_LAGGING": "\U0001f422 GitHub taraması gecikiyor",
    "FETCH_FAILING": "\U0001f6ab Biletinial taraması başarısız",
    "PLAY_FAILING": "\U0001f9e9 Oyun sayfası okunamıyor",
    "NEW_SESSIONS": "\U0001f4c5 Yeni seanslar/dönem",
    "DELIVERY_DROPPED": "⚠️ bildirim iletilemedi",
    "OPENED_SOLD_OUT": "⚡ Satış açıldı ve görülmeden tükendi — iade için izleniyor",
}
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


KIND_FIRST = ("ON_SALE", "OPENING_SOON")  # lock-screen preview shows the first line


def _entry(ev: Event, plays: dict[str, Play], config: Config, ids: list[str]) -> str:
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
        if ev.kind == "NEW_PLAY":
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
        elif len(item[1]) > FOLD_NEW_PLAYS_ABOVE:
            lines = [f"{KIND_TEXT['NEW_PLAY']}lar ({len(item[1])})"]
            for e in item[1]:
                url = e.payload.get("url")
                line = f"• {_title(e, plays)}"
                if url:
                    line += f" — {_esc(url, URL_MAX)}"
                lines.append(line)
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
    return parts




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
