"""Scan orchestration: fetch -> parse -> diff -> outbox -> Telegram (claim protected).

Delivery guarantees (a missed alert is worse than a duplicate):
- ``state.diff`` records every event key in ``state["emitted"]`` *before* anything is
  sent, so each event is first put into the persistent ``state["outbox"]`` and retried
  every run until the claim for its key reads ``sent`` (written by us or by the other
  runner). An entry is dropped after 6 h or 20 send attempts; the drop is logged in
  ``state["meta"]["outbox_dropped"]``, counted as a failure, and announced once by a
  ``DELIVERY_DROPPED`` notice (key ``DROPPED:<first dropped key>``) that goes through
  the same outbox. After a failed attempt the entry waits 10 s, 20 s, 40 s ...
  (max 10 min) before the next try.
- One claim per event: claim key = event key (``claims.claim_key``). Exceptions: ON_SALE
  events share one claim per session (``ON_SALE:<sid>``; the per-runner episode counter
  in the event key drifts between runners). A ``sent`` ON_SALE claim covers an ON_SALE
  entry created at most ``ON_SALE_GAP`` (3 min) after its ``sent_at``; an older one is
  re-claimed (a new re-open). The first scan (old state had no plays) folds all
  NEW_PLAY events into a single ``SETUP:<sha1(sorted slugs)[:12]>`` entry; the key is
  stable, so a retry after a failed state save is not re-sent.
- If the Telegram send succeeded but saving ``state.json`` failed, the next run derives
  the same events again; their claims already read ``sent`` so they are not re-sent
  (ON_SALE: only within the 3-min gap; a re-derive later than that may duplicate, which
  is accepted: a duplicate is better than a suppressed re-open).
- The PC runner (``who="pc"``) queues with ``not_before = now + 15 s`` for ON_SALE (a
  sell-out takes ~30 s) and ``now + 60 s`` for every other kind, so GitHub gets the
  first chance; afterwards the PC only sends if the claim is not ``sent``/held.

Scan health: a venue failure, or a venue that loads while no play page (or at least
max(2, 50 %) of them) parses, is a scan FAILURE: ``meta.fetch_fail_streak`` grows and
one ``FETCH_FAILING`` fires at 3. A single watched play whose page fails 3 normal scans
in a row fires one ``PLAY_FAILING`` (``plays[slug]["fail_streak"]``).

Burst mode: the announced ``opens_at`` is only a plan; the site flips to ON_SALE when
the organiser enables sales, which can be minutes late (observed live). So a watched
UPCOMING session is polled from ``opens_at - 10 min`` up to ``opens_at + 4 h``
(every 15 s +-3 s for the whole window: a live opening was still "Yakında" 55+ min after
its announced time and real tickets sell out within ~30 s of the flip), and for 10 min
after a flip to ON_SALE. GitHub API cost of a burst hour (GITHUB_TOKEN limit ~1000/h):
one ``config.json`` GET per tick (needed so a pause is honoured within one tick) = at
most 300 (ticks every 12-18 s), one heartbeat GET per minute (``pc_watch``) = 60, plus
state saves on change and ~10 requests per run start (4 runs/h) = ~40: about 400/h
(a test keeps a simulated hour under 600).
One run's burst ends before ``max_seconds`` (960 s); the next scheduled run
(every 5 min) re-enters burst mode by itself, which is cheap and idempotent (state,
``emitted`` and claims carry over). A run that starts inside a burst window goes
straight into the burst loop and does the full normal scan only afterwards, so the
blind gap between two runs' bursts is only the next run's start-up (checkout, Python
setup, ~20-40 s) instead of start-up plus a full venue scan; with the 16-min burst
and the 5-min schedule the next run usually starts before the previous burst ends, but
runs never overlap, so a gap of roughly one start-up remains. Two runs never overlap:
the scan workflow runs in one Actions ``concurrency`` group (set up in the workflow
file).

Pausing: with ``config.paused`` the scan and state update run normally but the events
(and anything still in the outbox) are discarded: they stay recorded in ``emitted`` and
are never sent, also not after unpausing. The count is kept in
``meta["paused_discarded"]``.

Outbox entry: ``{key, event | events, created, attempts, not_before}``; ``event`` is an
``Event`` as dict, a SETUP entry carries ``events`` (a list) instead.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Callable, Iterable

from tiyatro.claims import ClaimStore, claim_key, claim_path, send_with_claim
from tiyatro.config import config_from_dict, default_config
from tiyatro.fetcher import fetch
from tiyatro.heartbeat import github_run_status, pc_status, read_heartbeat
from tiyatro.models import ISTANBUL, Config, Event, Play, Session, Status
from tiyatro.notify import MONTHS, Telegram, default_transport, format_message
from tiyatro.parser import ParseError, parse_play, parse_venue
from tiyatro.rules import matching_targets
from tiyatro.state import (ON_SALE_GAP, clean_emitted, cleanup, diff, empty_state,
                           next_opening, valid_session_record)
from tiyatro.store import Store
from tiyatro.timeutil import parse_iso

STATE_PATH = "state.json"
CONFIG_PATH = "config.json"
REQUEST_GAP_S = 2
BURST_GAP_S = 0.5
FETCH_FAIL_ALERT_AT = 3
PLAY_FAIL_ALERT_AT = 3
OUTBOX_MAX_AGE = timedelta(hours=6)
OUTBOX_MAX_ATTEMPTS = 20
PC_DELAY = timedelta(seconds=60)
PC_ON_SALE_DELAY = timedelta(seconds=15)
RETRY_BASE_S = 10
RETRY_MAX_S = 600
DROPPED_LOG_MAX = 50
BURST_LEAD = timedelta(minutes=10)
BURST_OVERDUE = timedelta(hours=4)
BURST_FLIP_TAIL = timedelta(minutes=10)
BURST_SOLD_OUT_TAIL = timedelta(minutes=30)  # cart-expiry re-opens come 10-15 min later
# One cadence for the whole window (sell-outs take ~30 s; a late flip is no slower).
BURST_INTERVAL_S = 15
BURST_JITTER_S = 3
BURST_MAX_SECONDS = 960  # 16 min > the 5-min schedule + Actions start-up jitter
BURST_WATCH_EVERY = timedelta(seconds=60)
BURST_FAIL_ALERT_AT = 5  # consecutive burst ticks in which every page fetch failed
STORE_RETRY_S = 2
CONFIG_ERRORS = (ValueError, TypeError, KeyError, AttributeError)  # config_from_dict
PC_WINDOW = timedelta(minutes=60)
PC_SECOND_WARNING = timedelta(minutes=10)
DAILY_AT = dtime(12, 0)
SCAN_WORKFLOW_PATH = ".github/workflows/scan.yml"
SCAN_WORKFLOW_NAME = "scan"
ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN", "GITHUB_REPOSITORY")
SECRET_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN")


@dataclass
class RunResult:
    events: list[Event]
    mode: str
    scanned: int
    state: dict
    failures: int = 0
    save_error: str | None = None
    daily: bool = False
    errors: list[str] = field(default_factory=list)  # burst: exception type names
    dirty: bool = False  # burst: in-memory state newer than state.json


# ---- (de)serialisation and loading ----------------------------------------

def event_to_dict(event: Event) -> dict:
    return dataclasses.asdict(event)


def event_from_dict(d: dict) -> Event:
    return Event(kind=str(d.get("kind", "")), key=str(d.get("key", "")),
                 play_slug=d.get("play_slug") or "", session_id=d.get("session_id"),
                 target_ids=list(d.get("target_ids") or []),
                 payload=dict(d.get("payload") or {}))


def load_config(store) -> Config:
    data, _sha = store.get(CONFIG_PATH)
    return config_from_dict(data) if data else default_config()


def _prepare(state) -> dict:
    """Coerce a (possibly malformed) state into shape; fixes dicts in place.

    Garbled parts are replaced or dropped (non-dict plays, non-dict or keyless outbox
    entries) so one bad write can never wedge the runner.
    """
    if not isinstance(state, dict):
        state = empty_state()
    plays = state.get("plays")
    if not isinstance(plays, dict):
        plays = state["plays"] = {}
    for slug in [s for s, e in plays.items() if not isinstance(e, dict)]:
        del plays[slug]
    for entry in plays.values():
        if not isinstance(entry.get("sessions"), dict):
            entry["sessions"] = {}
        for sid in [s for s, r in entry["sessions"].items() if not valid_session_record(r)]:
            del entry["sessions"][sid]
    state["emitted"] = clean_emitted(state.get("emitted"))
    box = state.get("outbox")
    state["outbox"] = [e for e in (box if isinstance(box, list) else [])
                       if isinstance(e, dict) and isinstance(e.get("key"), str) and e["key"]
                       and (isinstance(e.get("event"), dict) or isinstance(e.get("events"), list))]
    if not isinstance(state.get("meta"), dict):
        state["meta"] = {}
    meta = state["meta"]
    meta.setdefault("last_scan", None)
    if not isinstance(meta.get("fetch_fail_streak"), int):
        meta["fetch_fail_streak"] = 0
    return state


def load_state(store) -> dict:
    """Store errors propagate on purpose: never overwrite good state with an empty one."""
    data, _sha = store.get(STATE_PATH)
    return _prepare(data if data else empty_state())


def save_state(store, state: dict) -> str | None:
    """Write state.json; returns None on success, else the error type name."""
    try:
        store.update_json(STATE_PATH, lambda _old: state)
        return None
    except Exception as exc:
        return type(exc).__name__


def _fingerprint(state: dict) -> str:
    """State identity without the per-scan timestamps (``last_scan``/``last_seen``),
    so a burst tick that only re-confirmed the page does not cause a write."""
    st = copy.deepcopy(state)
    st.get("meta", {}).pop("last_scan", None)
    for entry in (st.get("plays") or {}).values():
        if isinstance(entry, dict):
            entry.pop("last_seen", None)
            for rec in (entry.get("sessions") or {}).values():
                if isinstance(rec, dict):
                    rec.pop("closed_seen_at", None)
    return json.dumps(st, sort_keys=True, default=str)


def _emit(state: dict, kind: str, key: str, payload: dict, now: datetime,
          slug: str = "") -> Event | None:
    if key in state["emitted"]:
        return None
    state["emitted"][key] = now.isoformat()
    return Event(kind, key, slug, None, [], payload)


# ---- outbox ------------------------------------------------------------------

def pc_delay(key: str) -> timedelta:
    """How long the PC waits before sending outbox entry ``key`` (GitHub goes first)."""
    return PC_ON_SALE_DELAY if key.startswith("ON_SALE:") else PC_DELAY


def _enqueue(state: dict, key: str, events: list[Event], now: datetime, who: str) -> None:
    if any(e.get("key") == key for e in state["outbox"]):
        return
    entry = {"key": key, "created": now.isoformat(), "attempts": 0,
             "not_before": (now + pc_delay(key) if who == "pc" else now).isoformat()}
    if key.startswith("SETUP:"):
        entry["events"] = [event_to_dict(e) for e in events]
    else:
        entry["event"] = event_to_dict(events[0])
    state["outbox"].append(entry)


def setup_key(events: list[Event]) -> str:
    slugs = "\n".join(sorted(e.play_slug for e in events))
    return "SETUP:" + hashlib.sha1(slugs.encode("utf-8")).hexdigest()[:12]


def _queue_events(state: dict, events: list[Event], first_scan: bool, now: datetime,
                  who: str) -> None:
    setup = [e for e in events if e.kind == "NEW_PLAY"] if first_scan else []
    if setup:
        _enqueue(state, setup_key(setup), setup, now, who)
    for ev in events:
        if ev not in setup:
            _enqueue(state, ev.key, [ev], now, who)


def _entry_events(entry: dict) -> list[Event]:
    raw = entry.get("events")
    if not isinstance(raw, list):
        raw = [entry.get("event") or {}]
    return [event_from_dict(d) for d in raw if isinstance(d, dict)]


def _reclaim_after(key: str) -> timedelta | None:
    return ON_SALE_GAP if claim_key(key) != key else None


def _closed_seen_at(entry: dict) -> datetime | None:
    """When the runner that derived the entry last saw the session not on sale."""
    for ev in _entry_events(entry):
        try:
            at = parse_iso((ev.payload or {}).get("closed_seen_at"))
        except (TypeError, ValueError):
            at = None
        if at is not None:
            return at
    return None


def _claim_sent(store, key: str, closed: datetime | None = None) -> bool:
    """True if the claim for outbox key ``key`` reads ``sent``; for ON_SALE only if the
    deriving runner did not see the session closed after that send (``closed`` newer
    than ``sent_at`` means a later episode, which may be sent again)."""
    try:
        data, _sha = store.get(claim_path(claim_key(key)))
        if not isinstance(data, dict) or data.get("status") != "sent":
            return False
        gap = _reclaim_after(key)
        if gap is None:
            return True
        sent_at = parse_iso(data.get("sent_at"))
        return sent_at is None or closed is None or closed <= sent_at
    except Exception:
        return False  # fail open: rather try to send


class _AllParts:
    """Telegram stand-in for ``send_with_claim``: sends every part, returns the last id.

    If a later part fails, the whole message is retried and the earlier parts are sent
    again: an accepted duplicate (better than a missing part).
    """

    def __init__(self, tg, parts: list[str]):
        self.tg = tg
        self.parts = parts

    def send(self, _text: str) -> int:
        mid = 0
        for part in self.parts:
            mid = self.tg.send(part)
        return mid


def _log_drop(state: dict, entry: dict, reason: str, now: datetime) -> None:
    log = state["meta"].setdefault("outbox_dropped", [])
    if not isinstance(log, list):
        log = state["meta"]["outbox_dropped"] = []
    log.append({"key": entry.get("key"), "reason": reason, "at": now.isoformat(),
                "attempts": entry.get("attempts", 0)})
    del log[:-DROPPED_LOG_MAX]


def _drop_notice(state: dict, dropped: list[dict], now: datetime) -> None:
    labels = []
    for entry in dropped:
        for ev in _entry_events(entry):
            title = ev.payload.get("title") or ev.play_slug
            labels.append(f"{ev.kind} {title}".strip())
    first = str(dropped[0].get("key"))
    ev = _emit(state, "DELIVERY_DROPPED", f"DROPPED:{first}",
               {"count": len(dropped), "summary": ", ".join(labels)}, now)
    if ev is not None:
        _enqueue(state, ev.key, [ev], now, "github")


def _deliver(state: dict, store, tg, claims: ClaimStore, now: datetime,
             plays: dict[str, Play], config: Config) -> int:
    """Try every due outbox entry once. Returns the number of dropped (failed) entries."""
    dropped: list[dict] = []
    keep: list[dict] = []
    for entry in state["outbox"]:
        key = str(entry.get("key", ""))
        try:
            created = parse_iso(entry.get("created")) or now
            attempts = int(entry.get("attempts", 0))
            not_before = parse_iso(entry.get("not_before")) or now
        except (TypeError, ValueError):
            created, attempts, not_before = now, 0, now
        expired = ("age" if now - created > OUTBOX_MAX_AGE
                   else "attempts" if attempts >= OUTBOX_MAX_ATTEMPTS else None)
        if expired:
            if not _claim_sent(store, key, _closed_seen_at(entry)):
                _log_drop(state, entry, expired, now)
                dropped.append(entry)
            continue
        if not_before > now:
            keep.append(entry)
            continue
        closed = _closed_seen_at(entry)
        if _claim_sent(store, key, closed):
            continue  # delivered by us earlier or by the other runner
        parts = [p for p in format_message(_entry_events(entry), plays, config) if p]
        if not parts:
            continue
        entry["attempts"] = attempts + 1
        # False = held by the other runner or the send failed: retry next run.
        if not send_with_claim(claims, _AllParts(tg, parts), claim_key(key), parts[0],
                               _reclaim_after(key), closed):
            # Back off so a short outage during a 15 s burst cannot burn all attempts.
            delay = min(RETRY_BASE_S * 2 ** attempts, RETRY_MAX_S)
            entry["not_before"] = (now + timedelta(seconds=delay)).isoformat()
            keep.append(entry)
    state["outbox"] = keep
    if dropped:
        _drop_notice(state, dropped, now)
    return len(dropped)


def _discard_outbox(state: dict, events: Iterable[Event] = ()) -> None:
    meta = state["meta"]
    meta["paused_discarded"] = (int(meta.get("paused_discarded", 0))
                                + len(state["outbox"]) + len(list(events)))
    state["outbox"] = []


def _confirmed_paused(store, config: Config) -> bool:
    """True only if a fresh read of config.json still says paused.

    Discarding consumes event keys for good, so a cached/stale ``paused`` must never be
    enough. If the fresh read fails, the events are kept (fail open: a missed alert is
    worse than one unwanted while pausing).
    """
    if not config.paused:
        return False
    try:
        return load_config(store).paused
    except Exception:
        return False


def _dispatch(state: dict, events: list[Event], first_scan: bool, store, tg, claims,
              now: datetime, plays: dict[str, Play], config: Config, who: str) -> int:
    if (events or state["outbox"]) and _confirmed_paused(store, config):
        _discard_outbox(state, events)
        return 0
    _queue_events(state, events, first_scan, now, who)
    return _deliver(state, store, tg, claims, now, plays, config)


# ---- scanning ------------------------------------------------------------------

def _scan(config: Config, state: dict, fetch_fn: Callable, sleep: Callable,
          only_slugs: list[str] | None):
    """Returns (plays, unscanned slugs, per-play failures, venue error or None)."""
    burst = only_slugs is not None
    requests = 0

    def get(url: str) -> str:
        nonlocal requests
        if requests:
            sleep(BURST_GAP_S if burst else REQUEST_GAP_S)
        requests += 1
        return fetch_fn(url).html

    refs: dict[str, str] = {}
    venue_error: str | None = None
    if burst:
        for slug in only_slugs:
            url = (state["plays"].get(slug) or {}).get("url")
            if url:
                refs[slug] = url
    else:
        for source in config.sources:
            try:
                found = parse_venue(get(source))
                if not found:
                    raise ParseError("venue page lists 0 plays")
            except Exception as exc:  # FetchError, ParseError or a parser bug
                venue_error = type(exc).__name__
                continue
            for ref in found:
                refs.setdefault(ref.slug, ref.url)

    plays: list[Play] = []
    unscanned: set[str] = set()
    failures = 0
    for slug, url in refs.items():
        try:
            plays.append(parse_play(get(url), url))
        except Exception:  # one bad page must never abort the scan (R3)
            failures += 1
            unscanned.add(slug)
    return plays, unscanned, failures, venue_error


def _play_pages_error(scanned: int, failed: int) -> str | None:
    total = scanned + failed
    if total and (scanned == 0 or failed >= max(2, total / 2)):
        return f"PlayPagesFailing {failed}/{total}"
    return None


def _track_play_failures(state: dict, plays: list[Play], unscanned: set[str],
                         config: Config, now: datetime) -> list[Event]:
    events = []
    for p in plays:
        entry = state["plays"].get(p.slug)
        if entry is not None:
            entry["fail_streak"] = 0
            entry["fail_since"] = None
    for slug in sorted(unscanned):
        entry = state["plays"].get(slug)
        if entry is None:  # never parsed yet: nothing stored to warn about
            continue
        streak = int(entry.get("fail_streak") or 0)
        if streak == 0:
            entry["fail_since"] = now.isoformat()
        entry["fail_streak"] = streak + 1
        watch = config.plays.get(slug)
        if entry["fail_streak"] == PLAY_FAIL_ALERT_AT and watch is not None and watch.active:
            since = entry.get("fail_since") or now.isoformat()
            ev = _emit(state, "PLAY_FAILING", f"PLAY_FAILING:{slug}:{since}",
                       {"title": entry.get("title") or slug, "play_url": entry.get("url"),
                        "detail": f"{PLAY_FAIL_ALERT_AT} taramadır okunamıyor"}, now, slug)
            if ev:
                events.append(ev)
    return events


def _day_text(d: datetime) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def _new_sessions_event(state: dict, since: datetime, now: datetime) -> Event | None:
    lines = []
    for slug, entry in state["plays"].items():
        if entry.get("removed"):
            continue
        starts = []
        for rec in entry["sessions"].values():
            try:
                first = parse_iso(rec.get("first_seen"))
                start = parse_iso(rec.get("start"))
            except (TypeError, ValueError):
                continue
            if first is not None and start is not None and first > since \
                    and not rec.get("gone"):
                starts.append(start.astimezone(ISTANBUL))
        if starts:
            lo, hi = min(starts), max(starts)
            span = _day_text(lo) if lo.date() == hi.date() else \
                f"{_day_text(lo)} – {_day_text(hi)}"
            lines.append(f"• {entry.get('title') or slug}: {len(starts)} seans ({span})")
    if not lines:
        return None
    day = now.astimezone(ISTANBUL).date().isoformat()
    return _emit(state, "NEW_SESSIONS", f"NEW_SESSIONS:{day}", {"lines": lines}, now)


def _daily(state: dict, now: datetime) -> list[Event]:
    """Mark today's daily run; summarise sessions first seen since the previous one."""
    meta = state["meta"]
    local = now.astimezone(ISTANBUL)
    try:
        since = parse_iso(meta.get("last_daily_at"))
        if since is None and meta.get("last_daily"):
            since = datetime.combine(datetime.fromisoformat(meta["last_daily"]).date(),
                                     DAILY_AT, tzinfo=ISTANBUL)
    except (TypeError, ValueError):
        since = None
    meta["last_daily"] = local.date().isoformat()
    meta["last_daily_at"] = now.isoformat()
    if since is None:  # very first daily run: everything is "new", say nothing
        return []
    ev = _new_sessions_event(state, since, now)
    return [ev] if ev else []


def run_once(store, tg, claims, now: datetime, who: str = "github",
             fetch_fn: Callable = fetch, sleep: Callable = time.sleep,
             persist_state: bool = True, state: dict | None = None,
             only_slugs: list[str] | None = None,
             config: Config | None = None) -> RunResult:
    config = config if config is not None else load_config(store)
    state = _prepare(copy.deepcopy(state) if state is not None else load_state(store))
    burst = only_slugs is not None
    failures = 0

    # R2: deliver what earlier runs could not.
    if state["outbox"] and _confirmed_paused(store, config):
        _discard_outbox(state)
    else:
        failures += _deliver(state, store, tg, claims, now, {}, config)

    plays, unscanned, play_failures, venue_error = _scan(config, state, fetch_fn, sleep,
                                                         only_slugs)
    failures += play_failures
    first_scan = not state["plays"]
    events: list[Event] = []
    scan_error = venue_error or (None if burst
                                 else _play_pages_error(len(plays), play_failures))

    if not burst:
        meta = state["meta"]
        if scan_error:
            failures += 1
            if int(meta.get("fetch_fail_streak", 0)) == 0:
                meta["fetch_fail_since"] = now.isoformat()
            meta["fetch_fail_streak"] = int(meta.get("fetch_fail_streak", 0)) + 1
            if meta["fetch_fail_streak"] == FETCH_FAIL_ALERT_AT:
                since = meta.get("fetch_fail_since") or now.isoformat()
                ev = _emit(state, "FETCH_FAILING", f"FETCH_FAILING:{since}",
                           {"detail": f"Biletinial {FETCH_FAIL_ALERT_AT} kez üst üste "
                                      f"okunamadı ({scan_error})"}, now)
                if ev:
                    events.append(ev)
        else:
            meta["fetch_fail_streak"] = 0
            meta["fetch_fail_since"] = None

    # A failed venue scan never reaches diff (it would look like every play vanished).
    # With several sources, plays from the venues that did load are diffed as a subset.
    # Play pages that failed are `unscanned`: kept as they are, never counted missing.
    if burst or not venue_error or plays:
        state, diff_events = diff(state, plays, config, now,
                                  complete=not burst and not venue_error,
                                  unscanned=unscanned)
        state = _prepare(cleanup(state, config, now))
        events.extend(diff_events)
        if not burst:
            events.extend(_track_play_failures(state, plays, unscanned, config, now))

    daily = False
    if not burst and not scan_error and daily_due(state, now):
        events.extend(_daily(state, now))
        daily = True

    failures += _dispatch(state, events, first_scan, store, tg, claims, now,
                          {p.slug: p for p in plays}, config, who)

    save_error = save_state(store, state) if persist_state else None
    return RunResult(events, "burst" if burst else "normal", len(plays), state,
                     failures, save_error, daily)


# ---- modes -----------------------------------------------------------------------

def _burst_targets(state: dict, config: Config,
                   now: datetime) -> list[tuple[str, datetime, datetime | None]]:
    """(slug, burst end, opens_at or None) for watched sessions that need burst polling.

    UPCOMING (or UNKNOWN: unrecognised markup at opening time) with
    ``opens_at - 10 min <= now <= opens_at + 4 h``, flipped to ON_SALE
    within the last 10 min, or sold out (coming from UPCOMING or ON_SALE) within the
    last 30 min.
    """
    out = []
    for slug, entry in (state.get("plays") or {}).items():
        watch = config.plays.get(slug)
        if watch is None or not watch.active or entry.get("removed"):
            continue
        for sid, rec in (entry.get("sessions") or {}).items():
            if not isinstance(rec, dict) or rec.get("gone"):
                continue
            try:
                status = Status(rec["status"])
                opens = parse_iso(rec.get("opens_at"))
                since = parse_iso(rec.get("status_since"))
                session = Session(sid, parse_iso(rec["start"]), None, status, opens,
                                  rec.get("url", ""), "")
            except (KeyError, ValueError, TypeError):
                continue
            if status in (Status.UPCOMING, Status.UNKNOWN) and opens is not None \
                    and opens - BURST_LEAD <= now <= opens + BURST_OVERDUE:
                end, window = opens + BURST_OVERDUE, opens
            elif status is Status.ON_SALE and since is not None \
                    and since <= now <= since + BURST_FLIP_TAIL:
                end, window = since + BURST_FLIP_TAIL, opens
            elif status is Status.SOLD_OUT and since is not None \
                    and rec.get("prev_status") in (Status.UPCOMING.value,
                                                   Status.ON_SALE.value) \
                    and since <= now <= since + BURST_SOLD_OUT_TAIL:
                end, window = since + BURST_SOLD_OUT_TAIL, opens
            else:
                continue
            if matching_targets(watch, session):
                out.append((slug, end, window))
    return out


def plan_mode(state: dict, config: Config, now: datetime) -> tuple[str, datetime | None]:
    """("burst", until) while a watched session is about to open, is overdue or just
    flipped; else ("normal", None).

    Burst iff a watched session (active play, >= 1 matching target) is UPCOMING with
    ``opens_at - 10 min <= now <= opens_at + 4 h`` (the site flips when the organiser
    enables sales, observed 55+ min after the announced time), or flipped to ON_SALE
    within the last 10 min, or sold out (from UPCOMING/ON_SALE) within the last 30 min
    (cart-expiry re-opens). ``until`` is the latest such end (``opens_at + 4 h`` /
    flip + 10 min / sell-out + 30 min); ``burst_loop`` caps a single run at
    ``max_seconds`` and the next scheduled run re-enters burst mode. Cadence: every 15 s +-3 s for the whole window.
    """
    found = _burst_targets(state, config, now)
    if not found:
        return "normal", None
    return "burst", max(end for _, end, _opens in found)


def burst_loop(store, tg, claims, now_fn: Callable[[], datetime], sleep: Callable,
               until: datetime, interval: float = BURST_INTERVAL_S,
               fetch_fn: Callable = fetch, max_seconds: float = BURST_MAX_SECONDS, *,
               who: str = "github", persist_state: bool = True,
               state: dict | None = None,
               rand: Callable[[float, float], float] = random.uniform,
               config: Config | None = None) -> RunResult:
    """Re-scan only the plays inside a burst window.

    ``config``: the last good config, used while ``config.json`` cannot be read or is
    invalid (without it the first tick needs a readable config).

    Tick interval: ``interval`` (15 s) +-3 s for the whole window (``rand`` gives the
    jitter). A tick fetches only the targeted play pages and reads
    ``config.json`` exactly once, so a config change (pause, new watch/target) is
    honoured by the next tick at the latest (bounded by one tick). If that read fails,
    the last good config is used for scanning, but a paused config is re-read again
    before anything is discarded (``_confirmed_paused``). Stops at ``until``, after
    ``max_seconds`` or when no watched session is in its window any more.

    State is saved whenever it differs from the last successfully saved version (so a
    failed save is retried on later ticks); ``save_error`` keeps the first failure and
    ``dirty`` says the final state is not saved. For ``who="github"`` ``pc_watch`` runs
    about every 60 s (heartbeat/run list only, no Biletinial requests) so the second
    PC warning is not lost. A failing tick (e.g. one store 502) is recorded by
    exception type name in ``RunResult.errors`` and the loop goes on with the last good
    config/state. After ``BURST_FAIL_ALERT_AT`` (5) consecutive ticks in which every page
    fetch failed, one ``FETCH_FAILING`` (key ``FETCH_FAILING:burst:<opens_at>``, once per
    opening window) goes through the outbox; a tick with a parsed page resets the count.
    """
    start = now_fn()
    deadline = min(until, start + timedelta(seconds=max_seconds))
    state = _prepare(copy.deepcopy(state)) if state is not None else load_state(store)
    base, jitter = interval, BURST_JITTER_S
    errors: list[str] = []
    result: RunResult | None = None
    saved_fp = _fingerprint(state)
    save_error: str | None = None
    last_watch: datetime | None = None
    fail_ticks = 0
    while True:
        now = now_fn()
        if now >= deadline:
            break
        try:
            try:
                config = load_config(store)
            except Exception as exc:
                if config is None:
                    raise
                errors.append(type(exc).__name__)
            targets = _burst_targets(state, config, now)
            if not targets:
                break
            slugs = sorted({slug for slug, _end, _opens in targets})
            result = run_once(store, tg, claims, now, who=who, fetch_fn=fetch_fn,
                              sleep=sleep, persist_state=False, state=state,
                              only_slugs=slugs, config=config)
            state = result.state
            fail_ticks = fail_ticks + 1 if result.scanned == 0 else 0
            if fail_ticks == BURST_FAIL_ALERT_AT:
                opens = [o for *_x, o in targets if o is not None]
                window = min(opens) if opens else deadline
                ev = _emit(state, "FETCH_FAILING",
                           f"FETCH_FAILING:burst:{window.isoformat()}",
                           {"detail": f"Hızlı modda Biletinial {BURST_FAIL_ALERT_AT} kez "
                                      f"üst üste okunamadı"}, now)
                if ev is not None:
                    _dispatch(state, [ev], False, store, tg, claims, now, {}, config, who)
            if who == "github" and (last_watch is None
                                    or now - last_watch >= BURST_WATCH_EVERY):
                last_watch = now
                watched = copy.deepcopy(state)
                pc_watch(store, watched, config, now, claims, tg, check_gh=False)
                state = watched
            fp = _fingerprint(state)
            if persist_state and fp != saved_fp:
                err = save_state(store, state)
                if err is None:
                    saved_fp = fp
                else:
                    save_error = save_error or err
        except Exception as exc:
            errors.append(type(exc).__name__)
        sleep(max(1.0, base + rand(-jitter, jitter)))
    final = result or RunResult([], "burst", 0, state)
    final.state = state
    final.errors = errors
    final.save_error = save_error
    final.dirty = persist_state and _fingerprint(state) != saved_fp
    return final


def daily_due(state: dict, now: datetime) -> bool:
    local = now.astimezone(ISTANBUL)
    if local.time() < DAILY_AT:
        return False
    return (state.get("meta") or {}).get("last_daily") != local.date().isoformat()


def scan_runs(runs, run_id: str | None = None,
              workflow: str = SCAN_WORKFLOW_PATH) -> list[dict]:
    """Completed runs of the scan workflow, without the current run (``run_id``)."""
    out = []
    for r in runs if isinstance(runs, list) else []:
        if not isinstance(r, dict) or r.get("status") != "completed":
            continue
        if run_id is not None and str(r.get("id")) == str(run_id):
            continue
        path = str(r.get("path") or "").split("@")[0]
        if path == workflow or r.get("name") == SCAN_WORKFLOW_NAME:
            out.append(r)
    return out


def pc_watch(store, state: dict, config: Config, now: datetime, claims, tg,
             check_gh: bool = True) -> list[Event]:
    """Opening-window liveness checks (PC heartbeat, own GitHub schedule).

    ``check_gh``: the GitHub check measures the scan cadence (time since the previous
    run ended), which is only meaningful at the START of a run; later in the same run
    the gap grows by the run's own elapsed time. ``main`` checks it once at run start;
    the in-burst and post-burst calls pass ``check_gh=False``.

    Mutates ``state`` in place (emitted keys, outbox); the caller saves it. The GitHub
    check ignores the current run (env ``GITHUB_RUN_ID``), runs still in progress and
    other workflows.
    """
    _prepare(state)
    opens = next_opening(state, config, now)
    if opens is None or opens - now > PC_WINDOW:
        return []
    iso = opens.isoformat()
    at = f"{opens.astimezone(ISTANBUL):%H:%M}"
    candidates: list[Event | None] = []
    try:
        hb = read_heartbeat(store)
    except Exception:
        hb = None  # unreadable counts as missing: warn rather than stay silent
    status = pc_status(hb, state, now)
    if status == "inactive":
        n = 2 if opens - now <= PC_SECOND_WARNING else 1
        candidates.append(_emit(state, "PC_INACTIVE", f"PC_INACTIVE:{iso}:{n}",
                                {"detail": f"Satış açılışı {at}; PC kaydı yok veya eski"},
                                now))
    elif status == "diverged":
        candidates.append(_emit(state, "PC_DIVERGED", f"PC_DIVERGED:{iso}",
                                {"detail": f"PC farklı görüyor (açılış {at})"}, now))
    gh = None
    if check_gh:
        try:
            runs = scan_runs(store.runs(per_page=30),
                             os.environ.get("GITHUB_RUN_ID") or None)
            gh = github_run_status(runs, now)
        except Exception:
            gh = None  # the run list itself is unavailable: nothing reliable to report
    if gh is not None and not gh["healthy"]:
        candidates.append(_emit(state, "GH_LAGGING", f"GH_LAGGING:{iso}",
                                {"detail": f"Son çalışma {gh['gap_s'] // 60} dk önce "
                                           f"(açılış {at})"}, now))
    events = [e for e in candidates if e is not None]
    _dispatch(state, events, False, store, tg, claims, now, {}, config, "github")
    return events


# ---- entry point -------------------------------------------------------------------

def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "***")
    return text


def _new_drops(state: dict, since: datetime) -> list[str]:
    keys = []
    for d in state.get("meta", {}).get("outbox_dropped") or []:
        try:
            at = parse_iso(d.get("at"))
        except (TypeError, ValueError, AttributeError):
            continue
        if at is not None and at >= since:
            keys.append(str(d.get("key")))
    return keys


def _get_retry(store, path: str, sleep: Callable):
    """``store.get`` retried once after a short pause (a transient 502 must not cost
    the whole run)."""
    try:
        return store.get(path)
    except Exception:
        sleep(STORE_RETRY_S)
        return store.get(path)


def _startup_config(store, state: dict, now: datetime,
                    sleep: Callable) -> tuple[Config, Event | None]:
    """(config, CONFIG_INVALID event or None). A valid config is cached in
    ``state["meta"]["config_cache"]``; an invalid one falls back to that cache."""
    data, _sha = _get_retry(store, CONFIG_PATH, sleep)
    try:
        config = config_from_dict(data) if data else default_config()
    except CONFIG_ERRORS as exc:
        cache = state["meta"].get("config_cache")
        try:
            config = config_from_dict(cache) if isinstance(cache, dict) else default_config()
        except CONFIG_ERRORS:
            config = default_config()
        raw = json.dumps(data, sort_keys=True, default=str)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        ev = _emit(state, "CONFIG_INVALID", f"CONFIG_INVALID:{digest}",
                   {"detail": f"config.json geçersiz ({type(exc).__name__}: "
                              f"{str(exc)[:200]}); son geçerli ayarlarla devam ediliyor"},
                   now)
        return config, ev
    if isinstance(data, dict) and data:
        state["meta"]["config_cache"] = data
    return config, None


def _fresh_config(store, fallback: Config) -> Config:
    try:
        return load_config(store)
    except Exception:  # store error or invalid config.json: keep the last good one
        return fallback


def main() -> int:
    """GitHub entry point.

    The initial ``state.json`` / ``config.json`` reads are retried once on a store
    error. An invalid ``config.json`` does not stop the run: the last good config
    (``state["meta"]["config_cache"]``, refreshed by every valid read; else the
    default) is used and one ``CONFIG_INVALID`` alert is sent per distinct bad content.

    Exit code 0, except 1 when: an unexpected exception escaped (message without
    secrets), ``state.json`` could not be saved by any step, including a single burst
    tick even if a later save succeeded (the claims still prevent duplicates,
    but a red run makes a persistent store fault visible), an alert was dropped from
    the outbox, or the burst / PC-watch step crashed. 2 when env variables are missing.
    """
    secrets: list[str] = []
    try:
        env = {k: os.environ.get(k, "") for k in ENV_KEYS}
        secrets = [env[k] for k in SECRET_KEYS if env[k]]
        missing = [k for k, v in env.items() if not v]
        if missing:
            print(f"missing environment variables: {', '.join(missing)}", file=sys.stderr)
            return 2
        store = Store(env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"])
        tg = Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"],
                      transport=default_transport)

        def now_fn() -> datetime:
            return datetime.now(ISTANBUL)

        started = now_fn()
        claims = ClaimStore(store, "github", now_fn)
        store.ensure_branch()
        res: RunResult | None = None
        errors: list[str | None] = []
        state = _prepare(_get_retry(store, STATE_PATH, time.sleep)[0] or empty_state())
        config, invalid = _startup_config(store, state, started, time.sleep)
        if invalid is not None:  # Telegram-visible, once per distinct bad config.json
            _dispatch(state, [invalid], False, store, tg, claims, started, {}, config,
                      "github")
        if plan_mode(state, config, started)[0] != "burst":
            res = run_once(store, tg, claims, now_fn(), fetch_fn=fetch, sleep=time.sleep,
                           state=state, config=config)
            state = res.state
            errors.append(res.save_error)
        # else: already inside a burst window: burst first, the normal scan afterwards
        problems: list[str] = []
        watch_events: list[Event] = []
        before = _fingerprint(state)
        try:  # GitHub cadence: once per run, measured at run start
            watch_events += pc_watch(store, state, config, started, claims, tg,
                                     check_gh=True)
        except Exception as exc:
            problems.append(f"pc_watch failed: {type(exc).__name__}")
        if _fingerprint(state) != before:
            errors.append(save_state(store, state))
        mode, until = plan_mode(state, config, now_fn())
        if mode == "burst":
            try:
                br = burst_loop(store, tg, claims, now_fn, time.sleep, until,
                                fetch_fn=fetch, state=state, config=config)
                state = br.state
                errors.append(br.save_error)
                if br.dirty:  # retry the final burst state once more below
                    errors.append(save_state(store, state))
                if br.errors:
                    print(f"burst tick errors: {', '.join(br.errors)}", file=sys.stderr)
            except Exception as exc:
                problems.append(f"burst failed: {type(exc).__name__}")
        if res is None:  # the normal scan skipped at run start
            res = run_once(store, tg, claims, now_fn(), fetch_fn=fetch, sleep=time.sleep,
                           state=state, config=_fresh_config(store, config))
            state = res.state
            errors.append(res.save_error)
        before = _fingerprint(state)
        try:
            watch_events += pc_watch(store, state, config, now_fn(), claims, tg,
                                     check_gh=False)
        except Exception as exc:
            problems.append(f"pc_watch failed: {type(exc).__name__}")
        if _fingerprint(state) != before:
            errors.append(save_state(store, state))
        if res.daily:
            claims.purge()
        print(f"mode={mode} scanned={res.scanned} events={len(res.events)} "
              f"watch_events={len(watch_events)} failures={res.failures} "
              f"outbox={len(state['outbox'])}")
        drops = _new_drops(state, started)
        if drops:
            problems.append(f"dropped undelivered alerts: {', '.join(drops)}")
        errors = [e for e in errors if e]
        if errors:
            problems.append(f"state not saved: {', '.join(errors)}")
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    except Exception as exc:
        print(_scrub(f"runner failed: {type(exc).__name__}: {exc}", secrets), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
