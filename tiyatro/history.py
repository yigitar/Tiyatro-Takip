"""Notification history (wave 23): ``state["history"]``, a bounded list of precise one-line
facts written by the scanner itself, so it does not depend on the PC app having been open
when something happened. Everything in it is public listing data (titles, dates, seat
counters), small strings only.

Entry: ``{"ts", "key", "kind", "source", "title", "start", "text", "row"}``. ``kind`` is the
event kind (or one of the quiet pseudo kinds ``SOLD_OUT`` / ``FULL``: full and sold-out are
recorded here and shown in the row status, but never sent), ``row`` the id of the list row.
"""
from __future__ import annotations

from datetime import datetime

from tiyatro.models import ISTANBUL
from tiyatro.timeutil import parse_iso

MAX_HISTORY = 200
QUIET_KINDS = ("SOLD_OUT", "FULL")          # recorded, never notified
SILENT_EVENT_KINDS = ("OPENED_SOLD_OUT",)   # real events that are only recorded
MONTH_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]

# filter groups of the PC / Telegram view
GROUP_OF = {
    "NEW_PLAY": "yeni", "SRC_NEW": "yeni", "PORTAL_NEW": "yeni", "NEW_SESSIONS": "yeni",
    "ON_SALE": "açıldı", "SRC_OPENED": "açıldı", "OPENING_SOON": "yakında", "DAY_BEFORE": "yakında",
    "SRC_OPENS": "yakında", "SRC_DAY_BEFORE": "yakında", "SRC_SOON": "yakında",
    "SRC_REOPENED": "yer açıldı", "PORTAL_SEATS": "yer açıldı",
    "SOLD_OUT": "doldu", "FULL": "doldu", "OPENED_SOLD_OUT": "doldu",
    "REMOVED_PLAY": "kalktı", "UNKNOWN_STATUS": "diğer", "PORTAL_OPENED": "diğer",
}
GROUPS = ("yeni", "açıldı", "yer açıldı", "yakında", "doldu", "kalktı", "diğer")


def when(value) -> str:
    """"20 Kas 20:00" for an ISO string ("" when unusable)."""
    try:
        d = parse_iso(value) if isinstance(value, str) else None
    except ValueError:
        d = None
    if d is None:
        return ""
    d = d.astimezone(ISTANBUL)
    return f"{d.day} {MONTH_SHORT[d.month - 1]} {d:%H:%M}"


def _subject(title, start) -> str:
    w = when(start)
    return f"{title} ({w})" if w else str(title)


def _seats(ev: dict) -> str:
    free, total = ev.get("free"), ev.get("total")
    if isinstance(free, int) and isinstance(total, int) and not isinstance(free, bool):
        return f"{free} boş" if free > 0 else f"DOLU {total}/{total}"
    return ""


def add(state: dict, *, kind: str, source: str, title, start, text: str, row: str,
        key: str, now: datetime) -> None:
    """Append one entry (a key already present is not added twice); the list stays bounded."""
    hist = state.get("history")
    if not isinstance(hist, list):
        hist = state["history"] = []
    if any(isinstance(h, dict) and h.get("key") == key for h in hist[-MAX_HISTORY:]):
        return
    hist.append({"ts": now.isoformat(), "key": key, "kind": kind, "source": source,
                 "title": str(title or "")[:120], "start": start if isinstance(start, str) else None,
                 "text": text[:240], "row": str(row or "")})
    del hist[:-MAX_HISTORY]


def entries_for(ev, plays_title=lambda slug: "") -> list[dict]:
    """The history facts of one ``Event`` (a list: a discovery digest holds several). Health
    kinds (FETCH_FAILING, PORTAL_FAILING, PORTAL_MARKUP, ...) are not changes: none."""
    p = ev.payload if isinstance(ev.payload, dict) else {}
    kind = ev.kind
    source = str(p.get("source") or "biletinial")
    title = p.get("title") or plays_title(ev.play_slug) or ev.play_slug or ""
    start = p.get("start")
    row = ev.play_slug or str(p.get("event_id") or "")
    subj = _subject(title, start)
    mk = lambda text, **kw: {"kind": kind, "source": source, "title": title, "start": start,  # noqa: E731
                             "text": text, "row": row, "key": ev.key, **kw}
    if kind == "ON_SALE":
        n = p.get("remaining")
        extra = f" (son {n} bilet)" if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else ""
        return [mk(f"{subj} — bilet satışa çıktı{extra}")]
    if kind == "OPENING_SOON":
        return [mk(f"{subj} — satış 10 dk sonra açılıyor")]
    if kind == "DAY_BEFORE":
        return [mk(f"{subj} — yarın satış açılıyor")]
    if kind == "NEW_PLAY":
        return [mk(f"Yeni oyun: {subj}")]
    if kind == "NEW_SESSIONS":
        return [mk(f"{title} — yeni seans/dönem" + (f": {p['detail']}" if p.get("detail") else ""))]
    if kind == "REMOVED_PLAY":
        return [mk(f"{title} — listeden kalktı")]
    if kind == "UNKNOWN_STATUS":
        return [mk(f"{subj} — durum okunamadı")]
    if kind == "OPENED_SOLD_OUT":
        return [mk(f"{subj} — satış açıldı ve görülmeden tükendi")]
    if kind == "SRC_NEW":
        return [mk(f"Yeni etkinlik: {subj}")]
    if kind == "SRC_OPENS":
        return [mk(f"{subj} — rezervasyon açılış saati: {when(p.get('opens_at')) or '?'}")]
    if kind == "SRC_DAY_BEFORE":
        return [mk(f"{subj} — yarın rezervasyon açılıyor")]
    if kind == "SRC_SOON":
        return [mk(f"{subj} — 10 dk sonra rezervasyon açılıyor")]
    if kind == "SRC_OPENED":
        return [mk(f"{subj} — rezervasyon açıldı")]
    if kind == "SRC_REOPENED":
        return [mk(f"{subj} — yer açıldı")]
    if kind == "PORTAL_NEW":
        out = []
        for e in p.get("events") or []:
            if isinstance(e, dict):
                seats = _seats(e)
                out.append({"kind": kind, "source": "basiskele", "title": e.get("title") or "",
                            "start": e.get("start"), "row": str(e.get("rec_id") or e.get("id") or ""),
                            "text": f"Yeni etkinlik: {_subject(e.get('title') or '', e.get('start'))}"
                                    + (f" · {seats}" if seats else ""),
                            "key": f"{ev.key}:{e.get('id')}"})
        return out
    if kind == "PORTAL_SEATS":
        e = p.get("event") if isinstance(p.get("event"), dict) else {}
        what = "rezervasyon açıldı" if p.get("reason") == "opened" else "yer açıldı"
        seats = _seats(e)
        return [{"kind": kind, "source": "basiskele", "title": e.get("title") or "",
                 "start": e.get("start"), "row": str(p.get("event_id") or e.get("id") or ""),
                 "text": f"{_subject(e.get('title') or '', e.get('start'))} — {what}"
                         + (f": {seats}" if seats else ""), "key": ev.key}]
    if kind == "PORTAL_OPENED":
        return [{"kind": kind, "source": "basiskele", "title": p.get("title") or "", "start": None,
                 "text": "Rezervasyon portalında etkinlik görüldü (sayfa düzeni tanınmadı)",
                 "row": "", "key": ev.key}]
    return []


def record(state: dict, events, now: datetime, plays_title=lambda slug: "",
           first_scan: bool = False) -> None:
    """Write the history of ``events`` (the first scan's NEW_PLAY baseline is not news)."""
    for ev in events:
        if first_scan and ev.kind == "NEW_PLAY":
            continue
        for e in entries_for(ev, plays_title):
            add(state, now=now, **e)


TELEGRAM_LINES = 10


def telegram_lines(hist) -> list[str]:
    """The lines of the Telegram "📜 Geçmiş" screen: the last ``TELEGRAM_LINES`` entries, newest
    first, "18.11 12:05 · text" (parity: worker/src/menu.js historyScreen)."""
    items = [h for h in (hist if isinstance(hist, list) else [])
             if isinstance(h, dict) and isinstance(h.get("text"), str)]
    out = []
    for h in items[-TELEGRAM_LINES:][::-1]:
        try:
            d = parse_iso(h.get("ts"))
        except (TypeError, ValueError):
            d = None
        stamp = f"{d.astimezone(ISTANBUL):%d.%m %H:%M}" if d else "?"
        out.append(f"{stamp} · {h['text'][:200]}")
    return out
