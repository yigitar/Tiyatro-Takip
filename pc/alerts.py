"""Wave 22: "Yeni gelişmeler" -- what the (deliberately generic) alert popup was about.

The popup says only "yeni gelişme" because the user may be sharing the screen. The main
window is private, so it lists the details: one line per alert since the user last looked,
kept in the PC-local ``ui.json`` (``alerts``) so it survives a restart. The alerts are the very
``Event`` objects the popup is fed with (``Service.on_notify``), so both always agree.

Pure logic, no Tk: describing an event, the store (add / acknowledge / persist) and the
sets the list needs to mark rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from tiyatro.models import ISTANBUL
from tiyatro.notify import portal_label
from tiyatro.timeutil import parse_iso

DAY_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]   # as pc/viewmodel.py
MONTH_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]

MAX_ITEMS = 100            # stored alerts (acknowledged ones are dropped first)
KEEP_ACKED = timedelta(days=3)
MAX_LINES = 6              # lines the banner shows; the rest is "+N daha"
TITLE_MAX = 80
DETAIL_MAX = 120

# kind -> (icon, label). Wording of the generic popup is app_logic.POPUP_WORDING; these are
# the private, specific labels.
KIND_LABELS = {
    "ON_SALE": ("🎟", "Bilet açıldı"),
    "OPENED_SOLD_OUT": ("⚡", "Bilet açıldı ve tükendi"),
    "OPENING_SOON": ("⏰", "Satış açılıyor"),
    "DAY_BEFORE": ("🔔", "Yarın satış açılıyor"),
    "NEW_PLAY": ("🆕", "Yeni oyun"),
    "NEW_SESSIONS": ("📅", "Yeni seans"),
    "REMOVED_PLAY": ("❌", "Listeden kalktı"),
    "UNKNOWN_STATUS": ("⚠", "Bilinmeyen durum"),
    "SRC_NEW": ("🆕", "Yeni etkinlik"),
    "SRC_OPENS": ("🗓", "Rezervasyon saati duyuruldu"),
    "SRC_DAY_BEFORE": ("🔔", "Yarın rezervasyon açılıyor"),
    "SRC_SOON": ("⏰", "Rezervasyon açılıyor"),
    "SRC_OPENED": ("🎟", "Rezervasyon açıldı"),
    "SRC_REOPENED": ("🎟", "Rezervasyon yeniden açıldı"),
    "SRC_PROGRAM_MISSING": ("⚠", "Program haberi bulunamadı"),
    "PORTAL_OPENED": ("🏛", "Portalda etkinlik açıldı"),
    "PORTAL_CHANGED": ("🏛", "Portal güncellendi"),
    "PORTAL_FAILING": ("⚠", "Portal okunamıyor"),
    "PC_INACTIVE": ("🖥", "PC uygulaması aktif değil"),
    "PC_DIVERGED": ("🖥", "PC ile GitHub uyuşmuyor"),
    "GH_LAGGING": ("🐢", "GitHub taraması gecikiyor"),
    "FETCH_FAILING": ("⚠", "Biletinial taraması başarısız"),
    "PLAY_FAILING": ("⚠", "Oyun sayfası okunamıyor"),
    "DELIVERY_DROPPED": ("⚠", "Bildirim iletilemedi"),
    "CONFIG_INVALID": ("⚠", "Ayar dosyası geçersiz"),
}
FALLBACK_ICON = "•"
OPENS_KINDS = ("OPENING_SOON", "DAY_BEFORE", "SRC_SOON", "SRC_DAY_BEFORE", "SRC_OPENS")


def when_label(value) -> str:
    """"19 Kas Per 20:00" for an ISO string / datetime ("" when unusable)."""
    try:
        d = parse_iso(value) if isinstance(value, str) else value
    except ValueError:
        return ""
    if not isinstance(d, datetime):
        return ""
    d = d.astimezone(ISTANBUL)
    return f"{d.day} {MONTH_SHORT[d.month - 1]} {DAY_SHORT[d.weekday()]} {d:%H:%M}"


def _cut(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def describe(ev, now: datetime | None = None) -> dict:
    """The stored / shown form of one alert event::

        {"key", "kind", "icon", "label", "title", "detail", "slug", "ts", "ack": None}

    ``slug`` is the id of the list row the alert belongs to (a play's slug, or a source
    event's id) or "" when it belongs to none (health alerts)."""
    p = ev.payload if isinstance(getattr(ev, "payload", None), dict) else {}
    icon, label = KIND_LABELS.get(ev.kind, (FALLBACK_ICON, str(ev.kind)))
    slug = str(getattr(ev, "play_slug", "") or p.get("event_id") or "")
    if ev.kind.startswith("PORTAL_"):
        title = portal_label(p.get("portal_id"), p.get("title"))
        items = [i for i in (p.get("items") or []) if isinstance(i, dict)]
        parts = [f"{len(items)} etkinlik"] if items else []
        slug = ""
    else:
        title = p.get("title") or ""
        parts = []
        if p.get("venue") and ev.kind.startswith("SRC_"):
            parts.append(p["venue"])
        start = when_label(p.get("start"))
        if start and ev.kind not in OPENS_KINDS:
            parts.append(start)
        if ev.kind in OPENS_KINDS:
            opens = when_label(p.get("opens_at"))
            if opens:
                parts.append(f"açılış {opens}")
            if start:
                parts.append(f"etkinlik {start}")
        remaining = p.get("remaining")
        if ev.kind == "ON_SALE" and isinstance(remaining, int) and not isinstance(remaining, bool) \
                and remaining >= 1:
            parts.append(f"son {remaining} bilet")
        if not parts and p.get("detail"):
            parts.append(p["detail"])
    return {"key": str(ev.key or ""), "kind": ev.kind, "icon": icon, "label": label,
            "title": _cut(title, TITLE_MAX), "detail": _cut(" · ".join(map(str, parts)), DETAIL_MAX),
            "slug": slug, "ts": (now or datetime.now(ISTANBUL)).isoformat(), "ack": None}


def line_text(alert: dict) -> str:
    """"🎟 Bilet açıldı — On İkinci Gece · 19 Kas Per 20:00 · son 3 bilet" (plain text)."""
    head = f"{alert['icon']} {alert['label']}"
    body = " · ".join(x for x in (alert["title"], alert["detail"]) if x)
    return f"{head} — {body}" if body else head


def _clean(raw) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw["key"]:
        return None
    out = {k: str(raw.get(k) or "") for k in ("key", "kind", "icon", "label", "title",
                                                "detail", "slug", "ts")}
    ack = raw.get("ack")
    out["ack"] = ack if isinstance(ack, str) and ack else None
    return out


class AlertStore:
    """Alerts since the user last looked, oldest first; persisted in ``ui.json``.

    An alert is *open* until acknowledged (``ack`` = timestamp). Acknowledged ones are kept a
    few days only so that a replayed event key is not shown twice."""

    def __init__(self, items=None, clock=None):
        self.items: list[dict] = [c for c in map(_clean, items or ()) if c]
        self._clock = clock or (lambda: datetime.now(ISTANBUL))
        self._prune()

    @classmethod
    def load(cls, path, clock=None) -> "AlertStore":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(clock=clock)
        saved = data.get("alerts") if isinstance(data, dict) else None
        return cls(saved if isinstance(saved, list) else None, clock)

    def to_json(self) -> list[dict]:
        return [dict(i) for i in self.items]

    def _prune(self) -> None:
        now = self._clock()
        keep = []
        for item in self.items:
            ack = _parse(item["ack"])
            if ack is not None and now - ack > KEEP_ACKED:
                continue
            keep.append(item)
        if len(keep) > MAX_ITEMS:
            open_items = [i for i in keep if i["ack"] is None]
            acked = [i for i in keep if i["ack"] is not None]
            room = max(0, MAX_ITEMS - len(open_items))
            keep = (acked[-room:] if room else []) + open_items
            keep = keep[-MAX_ITEMS:]
        self.items = keep

    # ---- reading -------------------------------------------------------------------------
    def open(self) -> list[dict]:
        """Unacknowledged alerts, newest first."""
        return [i for i in reversed(self.items) if i["ack"] is None]

    def count(self) -> int:
        return sum(1 for i in self.items if i["ack"] is None)

    def open_slugs(self) -> set[str]:
        return {i["slug"] for i in self.items if i["ack"] is None and i["slug"]}

    # ---- changing ------------------------------------------------------------------------
    def add(self, events) -> int:
        """Record popup events; returns how many were new (by event key)."""
        known = {i["key"] for i in self.items}
        now = self._clock()
        added = 0
        for ev in events:
            alert = describe(ev, now)
            if alert["key"] and alert["key"] in known:
                continue
            if not alert["key"]:
                alert["key"] = f"#{now.isoformat()}:{len(self.items)}"
            known.add(alert["key"])
            self.items.append(alert)
            added += 1
        if added:
            self._prune()
        return added

    def ack_all(self) -> int:
        return self._ack(lambda i: True)

    def ack_key(self, key: str) -> int:
        return self._ack(lambda i: i["key"] == key)

    def ack_slug(self, slug: str) -> int:
        """The user opened this row: its alerts are read."""
        return self._ack(lambda i: bool(slug) and i["slug"] == slug)

    def _ack(self, pick) -> int:
        stamp = self._clock().isoformat()
        n = 0
        for item in self.items:
            if item["ack"] is None and pick(item):
                item["ack"] = stamp
                n += 1
        return n


def _parse(value) -> datetime | None:
    try:
        return parse_iso(value) if isinstance(value, str) else None
    except ValueError:
        return None
