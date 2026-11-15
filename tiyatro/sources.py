"""Per-source health bookkeeping (wave 4 B): ``state["sources"][id]``.

Record: ``{name, last_check_at, last_ok_at, ok, error, last_change_at, items_count}``.

* every check sets ``last_check_at`` and ``ok``; a successful one also ``last_ok_at``;
* ``last_change_at`` moves only when the hash of the normalized parsed items changed
  (the source's own content, not the scan time);
* ``error`` is a short, secret-free reason (an exception type name or a short word).

Ids: ``biletinial``, ``basiskele``, ``vkv``. ``last_check_at``/``last_ok_at`` are left out
of the runner's state fingerprint (they move every scan, like portal ``last_check``).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from tiyatro.models import ISTANBUL
from tiyatro.timeutil import parse_iso

SOURCE_IDS = ("biletinial", "basiskele", "vkv")
SOURCE_NAMES = {"biletinial": "Biletinial", "basiskele": "Başiskele Belediyesi",
                "vkv": "VKV Ford Otosan Gölcük"}
STALE_AFTER = timedelta(minutes=30)
ERROR_MAX = 80
TIME_KEYS = ("last_check_at", "last_ok_at")

ICON_OK, ICON_STALE, ICON_FAIL, ICON_NONE = "✅", "⚠️", "❌", "⚪"
LEGEND = ("✅ güncel · ⚠️ 30 dakikadan eski (kontrol gecikti) · ❌ son kontrol başarısız · "
          "Son kontrol = kaynağa en son bakılma zamanı, Son güncelleme = kaynağın kendi "
          "içeriğinin en son değişme zamanı")


def source_name(source_id: str) -> str:
    return SOURCE_NAMES.get(source_id, source_id)


def items_hash(items) -> str:
    """sha1 of the normalized (JSON, sorted keys) parsed items."""
    norm = json.dumps(items, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _short(error) -> str | None:
    if not error:
        return None
    return str(error).replace("\n", " ")[:ERROR_MAX]


def record_check(state: dict, source_id: str, now: datetime, ok: bool, *,
                 error=None, items=None, items_count: int | None = None,
                 content_hash: str | None = None, name: str | None = None) -> dict:
    """Fold one check into ``state["sources"][source_id]`` and return the record.

    Pass ``items`` (a JSON-able list/dict of the parsed content) or a ready
    ``content_hash``. A failed check never touches the content hash/``last_change_at``.
    """
    sources = state.get("sources")
    if not isinstance(sources, dict):
        sources = state["sources"] = {}
    rec = sources.get(source_id)
    if not isinstance(rec, dict):
        rec = sources[source_id] = {}
    rec["name"] = name or rec.get("name") or source_name(source_id)
    iso = now.isoformat()
    rec["last_check_at"] = iso
    rec["ok"] = bool(ok)
    if ok:
        rec["last_ok_at"] = iso
        rec["error"] = None
        if items is not None:
            content_hash = items_hash(items)
            if items_count is None and hasattr(items, "__len__"):
                items_count = len(items)
        if items_count is not None:
            rec["items_count"] = int(items_count)
        if content_hash is not None:
            if rec.get("content_hash") != content_hash or not rec.get("last_change_at"):
                rec["last_change_at"] = iso
            rec["content_hash"] = content_hash
        rec.setdefault("last_change_at", None)
    else:
        rec["error"] = _short(error) or "hata"
        rec.setdefault("last_ok_at", None)
        rec.setdefault("last_change_at", None)
        rec.setdefault("items_count", None)
    return rec


def _dt(value) -> datetime | None:
    try:
        return parse_iso(value) if isinstance(value, str) else None
    except ValueError:
        return None


def status_of(rec, now: datetime) -> str:
    """"ok" | "stale" | "failed" | "unknown"."""
    if not isinstance(rec, dict):
        return "unknown"
    checked = _dt(rec.get("last_check_at"))
    if checked is None:
        return "unknown"
    if rec.get("ok") is False:
        return "failed"
    if now - checked > STALE_AFTER:
        return "stale"
    return "ok"


ICONS = {"ok": ICON_OK, "stale": ICON_STALE, "failed": ICON_FAIL, "unknown": ICON_NONE}


def time_text(value, now: datetime) -> str:
    d = _dt(value)
    if d is None:
        return "—"
    d, today = d.astimezone(ISTANBUL), now.astimezone(ISTANBUL).date()
    return f"{d:%H:%M}" if d.date() == today else f"{d:%d.%m %H:%M}"


def describe(state: dict | None, now: datetime) -> list[dict]:
    """One dict per source that has a record (known ids first, in a fixed order)."""
    recs = (state or {}).get("sources")
    recs = recs if isinstance(recs, dict) else {}
    ids = [i for i in SOURCE_IDS if isinstance(recs.get(i), dict)]
    ids += sorted(i for i in recs if i not in SOURCE_IDS and isinstance(recs[i], dict))
    out = []
    for sid in ids:
        rec = recs[sid]
        st = status_of(rec, now)
        count = rec.get("items_count")
        out.append({"id": sid, "name": str(rec.get("name") or source_name(sid)),
                    "status": st, "icon": ICONS[st],
                    "last_check_at": rec.get("last_check_at")
                    if _dt(rec.get("last_check_at")) else None,
                    "last_change_at": rec.get("last_change_at")
                    if _dt(rec.get("last_change_at")) else None,
                    "last_check": time_text(rec.get("last_check_at"), now),
                    "last_change": time_text(rec.get("last_change_at"), now),
                    "items_count": count if isinstance(count, int) else None,
                    "error": rec.get("error") if isinstance(rec.get("error"), str) else None})
    return out


def line(view: dict) -> str:
    """"✅ Biletinial · Son kontrol 14:35 · Son güncelleme 12:10 · 6 öğe"."""
    parts = [f"{view['icon']} {view['name']}", f"Son kontrol {view['last_check']}",
             f"Son güncelleme {view['last_change']}"]
    if view["items_count"] is not None:
        parts.append(f"{view['items_count']} öğe")
    if view["status"] == "failed" and view.get("error"):
        parts.append(f"hata: {view['error']}")
    return " · ".join(parts)
