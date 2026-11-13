"""Scan-to-scan state diff, notification events and periodic cleanup.

Notes on alerting semantics:
- Every event goes through ``state["emitted"]`` (key -> ISO time). A key is never
  emitted twice while it is remembered; ``cleanup`` forgets keys after 60 days. So a
  moved ``opens_at`` after an OPENING_SOON/DAY_BEFORE alert does not re-alert, and a
  play removed, re-added and removed again within 60 days is not re-announced.
- ON_SALE: a session entering ON_SALE (or first seen ON_SALE, or reappearing ON_SALE
  after having dropped off the page) becomes "pending". The pending alert fires when an
  active watch has a matching target and the last ON_SALE alert for that session is at
  least 30 min old. A pending alert survives the session vanishing for a scan. If a
  matching active watch/target appears while the session is already ON_SALE, exactly
  one ON_SALE alert fires ("ticket available right now").
- UNKNOWN_STATUS is keyed per session id (``UNKNOWN_STATUS:<sid>``): once per session.
- DAY_BEFORE fires from 10:00 the day before opening until 60 min before opening, so it
  never arrives as a near-duplicate of OPENING_SOON (last 10 min before opening).
"""
from __future__ import annotations

import copy
from datetime import datetime, time, timedelta
from typing import Iterable

from tiyatro.models import ISTANBUL, Config, Event, Play, PlayWatch, Session, Status
from tiyatro.rules import matching_targets
from tiyatro.tags import apply_overrides, derive_tags
from tiyatro.timeutil import parse_iso

ON_SALE_GAP = timedelta(minutes=30)
OPENING_SOON_LEAD = timedelta(minutes=10)
DAY_BEFORE_HOUR = time(10, 0)
DAY_BEFORE_CUTOFF = timedelta(minutes=60)
REMOVE_AFTER_MISSES = 2
EMITTED_TTL = timedelta(days=60)
NEVER_OPENED_TTL = timedelta(days=180)


def empty_state() -> dict:
    return {
        "plays": {},
        "meta": {"last_scan": None, "fetch_fail_streak": 0},
        "emitted": {},
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


_dt = parse_iso


def _normalize(old: dict) -> dict:
    state = copy.deepcopy(old) if old else empty_state()
    state.setdefault("plays", {})
    state.setdefault("emitted", {})
    meta = state.setdefault("meta", {})
    meta.setdefault("last_scan", None)
    meta.setdefault("fetch_fail_streak", 0)
    return state


def _stored_session(sid: str, entry: dict) -> Session:
    return Session(
        id=sid,
        start=_dt(entry["start"]),
        end=None,
        status=Status(entry["status"]),
        opens_at=_dt(entry.get("opens_at")),
        url=entry.get("url", ""),
        venue="",
    )


def _watch(config: Config, slug: str) -> PlayWatch | None:
    watch = config.plays.get(slug)
    return watch if watch is not None and watch.active else None


def _day_before_start(opens_at: datetime) -> datetime:
    day = opens_at.astimezone(ISTANBUL).date() - timedelta(days=1)
    return datetime.combine(day, DAY_BEFORE_HOUR, tzinfo=ISTANBUL)


def _merge_play(state: dict, play: Play, config: Config, now_iso: str) -> bool:
    """Fold a scanned play into state. Returns True if the slug is new."""
    tags = sorted(apply_overrides(derive_tags(play), config.plays.get(play.slug)))
    entry = state["plays"].get(play.slug)
    is_new = entry is None
    if is_new:
        entry = {
            "title": play.title, "first_seen": now_iso, "last_seen": now_iso,
            "missing_count": 0, "ever_opened": False, "archived": False,
            "removed": False, "tags": [], "sessions": {},
        }
        state["plays"][play.slug] = entry
    entry.update(title=play.title, url=play.url, last_seen=now_iso, missing_count=0,
                 removed=False, tags=tags)

    sessions = entry.setdefault("sessions", {})
    seen = set()
    for s in play.sessions:
        seen.add(s.id)
        prev = sessions.get(s.id)
        # A gone record is a non-ON_SALE previous state: reappearing ON_SALE is a transition.
        prev_status = None if prev is None or prev.get("gone") else prev["status"]
        rec = prev if prev is not None else {
            "last_on_sale_notified": None, "on_sale_count": 0, "on_sale_pending": False,
            "first_seen": now_iso,
        }
        if prev is None or prev_status != s.status.value or "status_since" not in rec:
            rec["status_since"] = now_iso
        rec.update(status=s.status.value, opens_at=_iso(s.opens_at), start=_iso(s.start),
                   url=s.url, gone=False)
        if s.status in (Status.ON_SALE, Status.SOLD_OUT):
            entry["ever_opened"] = True
        if s.status is Status.ON_SALE:
            if prev_status != Status.ON_SALE.value:
                rec["on_sale_pending"] = True
        else:
            rec["on_sale_pending"] = False
        sessions[s.id] = rec
    for sid, rec in sessions.items():
        if sid not in seen:
            rec["gone"] = True
    return is_new


def _session_payload(entry: dict, session: Session) -> dict:
    return {
        "title": entry["title"],
        "play_url": entry.get("url"),
        "url": session.url,
        "start": _iso(session.start),
        "opens_at": _iso(session.opens_at),
        "status": session.status.value,
    }


def diff(old: dict, plays: list[Play], config: Config, now: datetime, *,
         complete: bool = True,
         unscanned: Iterable[str] = ()) -> tuple[dict, list[Event]]:
    """Fold a scan into state and return (new_state, events).

    ``complete=False``: ``plays`` is only a subset (e.g. a burst scan of a few plays),
    so stored plays absent from it are not counted as missing.
    ``unscanned``: slugs that are listed on the venue page but whose own page could not
    be read this scan; their stored record is left untouched and never counted missing.
    """
    state = _normalize(old)
    skip_missing = set(unscanned)
    now_iso = now.isoformat()
    same_scan = state["meta"]["last_scan"] == now_iso
    emitted = state["emitted"]
    events: list[Event] = []

    def emit(kind: str, key: str, slug: str, sid: str | None,
             target_ids: list[str], payload: dict) -> None:
        if key in emitted:
            return
        emitted[key] = now_iso
        events.append(Event(kind, key, slug, sid, target_ids, payload))

    seen = set()
    for play in plays:
        seen.add(play.slug)
        if _merge_play(state, play, config, now_iso):
            emit("NEW_PLAY", f"NEW_PLAY:{play.slug}", play.slug, None, [],
                 {"title": play.title, "url": play.url})

    for slug, entry in state["plays"].items():
        if slug in seen or same_scan or not complete or slug in skip_missing:
            continue
        entry["missing_count"] = entry.get("missing_count", 0) + 1
        if entry["missing_count"] >= REMOVE_AFTER_MISSES and not entry.get("removed"):
            entry["removed"] = True
            emit("REMOVED_PLAY", f"REMOVED_PLAY:{slug}", slug, None, [],
                 {"title": entry["title"], "url": entry.get("url")})

    for slug, entry in state["plays"].items():
        watch = _watch(config, slug)
        if watch is None or entry.get("removed"):
            continue
        for sid, rec in entry["sessions"].items():
            if rec.get("gone"):
                continue
            session = _stored_session(sid, rec)
            payload = _session_payload(entry, session)
            if session.status is Status.UNKNOWN:
                emit("UNKNOWN_STATUS", f"UNKNOWN_STATUS:{sid}", slug, sid, [], payload)
                continue
            targets = matching_targets(watch, session)
            if not targets:
                continue
            ids = [t.id for t in targets]
            if session.status is Status.ON_SALE and rec.get("on_sale_pending"):
                last = _dt(rec.get("last_on_sale_notified"))
                if last is None or now - last >= ON_SALE_GAP:
                    n = rec.get("on_sale_count", 0) + 1
                    rec.update(on_sale_count=n, last_on_sale_notified=now_iso,
                               on_sale_pending=False)
                    emit("ON_SALE", f"ON_SALE:{sid}:{n}", slug, sid, ids, payload)
            elif session.status is Status.UPCOMING and session.opens_at is not None:
                soon_from = session.opens_at - OPENING_SOON_LEAD
                if soon_from <= now < session.opens_at:
                    emit("OPENING_SOON", f"OPENING_SOON:{sid}", slug, sid, ids, payload)
                elif (_day_before_start(session.opens_at) <= now
                      < session.opens_at - DAY_BEFORE_CUTOFF):
                    emit("DAY_BEFORE", f"DAY_BEFORE:{sid}", slug, sid, ids, payload)

    state["meta"]["last_scan"] = now_iso
    return state, events


def cleanup(state: dict, config: Config, now: datetime) -> dict:
    out = _normalize(state)
    now_iso = now.isoformat()
    out["emitted"] = {k: v for k, v in out["emitted"].items()
                      if now - _dt(v) <= EMITTED_TTL}
    for slug in list(out["plays"]):
        entry = out["plays"][slug]
        starts = [_dt(rec["start"]) for rec in entry.get("sessions", {}).values()]
        watched = _watch(config, slug) is not None
        too_old = now - _dt(entry["first_seen"]) > NEVER_OPENED_TTL
        if not entry.get("ever_opened") and too_old and not watched:
            del out["plays"][slug]
            # A still-listed play would otherwise be re-announced as NEW_PLAY next scan.
            out["emitted"][f"NEW_PLAY:{slug}"] = now_iso
            continue
        entry["archived"] = bool(starts) and all(s < now for s in starts)
    return out


def next_opening(state: dict, config: Config, now: datetime) -> datetime | None:
    best: datetime | None = None
    for slug, entry in state.get("plays", {}).items():
        watch = _watch(config, slug)
        if watch is None or entry.get("removed"):
            continue
        for sid, rec in entry.get("sessions", {}).items():
            if rec.get("gone") or rec["status"] != Status.UPCOMING.value:
                continue
            session = _stored_session(sid, rec)
            if session.opens_at is None or session.opens_at <= now:
                continue
            if not matching_targets(watch, session):
                continue
            if best is None or session.opens_at < best:
                best = session.opens_at
    return best
