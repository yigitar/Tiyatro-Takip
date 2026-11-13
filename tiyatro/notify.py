"""Turkish HTML notification messages and a minimal Telegram client."""
from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable

from tiyatro.models import ISTANBUL, Config, Event, Play

MAX_LEN = 4096
FOLD_NEW_PLAYS_ABOVE = 3
MAX_ATTEMPTS = 3

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
    "GH_LAGGING": "\U0001f422 GitHub taraması gecikiyor",
    "FETCH_FAILING": "\U0001f6ab Biletinial taraması başarısız",
}
DAY_BEFORE_TEXT = "\U0001f514 Satış açılış hatırlatması: "


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(ISTANBUL)
    except ValueError:
        return None


def _when(d: datetime) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {DAYS[d.weekday()]} {d:%H:%M}"


def _kind_line(ev: Event) -> str:
    if ev.kind == "DAY_BEFORE":
        opens = _dt(ev.payload.get("opens_at"))
        when = f"{DAYS[opens.weekday()]}, {opens:%H:%M}" if opens else "?"
        return DAY_BEFORE_TEXT + html.escape(when)
    text = KIND_TEXT.get(ev.kind, html.escape(ev.kind))
    if ev.kind == "OPENING_SOON":
        opens = _dt(ev.payload.get("opens_at"))
        if opens:
            text += f" ({opens:%H:%M})"
    return text


def _title(ev: Event, plays: dict[str, Play]) -> str:
    title = ev.payload.get("title")
    if not title and ev.play_slug in plays:
        title = plays[ev.play_slug].title
    return html.escape(str(title or ev.play_slug or ""))


def _targets(ev: Event, config: Config, ids: list[str]) -> str:
    watch = config.plays.get(ev.play_slug)
    if watch is None:
        return ""
    by_id = {t.id: t for t in watch.targets}
    return ", ".join(f"{html.escape(by_id[i].name)} ({by_id[i].people} kişi)"
                     for i in ids if i in by_id)


def _entry(ev: Event, plays: dict[str, Play], config: Config, ids: list[str]) -> str:
    lines = []
    title = _title(ev, plays)
    if title:
        lines.append(f"<b>{title}</b>")
    start = _dt(ev.payload.get("start"))
    if start and ev.session_id is not None:
        lines.append(f"\U0001f4c5 {_when(start)}")
    lines.append(_kind_line(ev))
    people = _targets(ev, config, ids)
    if people:
        lines.append(f"\U0001f465 {people}")
    url = ev.payload.get("url") or ev.payload.get("play_url")
    if url:
        lines.append(f"\U0001f517 {html.escape(str(url))}")
    detail = ev.payload.get("detail")
    if detail:
        lines.append(html.escape(str(detail)))
    return "\n".join(lines)


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split an over-long entry on line boundaries (hard-split lines that still exceed)."""
    out: list[str] = []
    cur = ""
    pieces: list[str] = []
    for line in text.split("\n"):
        while len(line) > limit:
            cut = limit
            chunk = line[:cut]
            amp = chunk.rfind("&")
            if amp > 0 and amp > chunk.rfind(";"):
                cut = amp  # never cut inside an HTML entity
            pieces.append(line[:cut])
            line = line[cut:]
        pieces.append(line)
    for line in pieces:
        cand = f"{cur}\n{line}" if cur else line
        if len(cand) > limit:
            out.append(cur)
            cur = line
        else:
            cur = cand
    out.append(cur)
    return out


def format_message(events: list[Event], plays: dict[str, Play], config: Config) -> list[str]:
    entries: list[str] = []
    groups: dict[tuple, int] = {}      # (kind, slug, sid) -> index in `entries_src`
    entries_src: list = []             # ("ev", Event, ids) | ("new", [Event])
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
                    line += f" — {html.escape(str(url))}"
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

    def send(self, text: str) -> int:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": False}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                status, body = self._transport(url, payload)
            except TelegramError as exc:
                raise TelegramError(self._scrub(str(exc))) from None
            except Exception as exc:
                raise TelegramError(
                    f"telegram transport error: {type(exc).__name__}") from None
            body = body if isinstance(body, dict) else {}
            if status == 429 and attempt < MAX_ATTEMPTS:
                wait = (body.get("parameters") or {}).get("retry_after", 1)
                self._sleep(wait if isinstance(wait, (int, float)) else 1)
                continue
            if status == 200 and body.get("ok"):
                return int(body["result"]["message_id"])
            desc = self._scrub(str(body.get("description", "")))
            raise TelegramError(f"telegram error {status}: {desc}")
        raise TelegramError("telegram error: retries exhausted")  # pragma: no cover
