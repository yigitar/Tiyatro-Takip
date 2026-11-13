"""Scan orchestration: fetch -> parse -> diff -> outbox -> Telegram (claim protected).

Delivery guarantees (a missed alert is worse than a duplicate):
- ``state.diff`` records every event key in ``state["emitted"]`` *before* anything is
  sent, so each event is first put into the persistent ``state["outbox"]`` and retried
  every run until the claim for its key reads ``sent`` (written by us or by the other
  runner). An entry is dropped after 6 h or 20 send attempts; the drop is logged in
  ``state["meta"]["outbox_dropped"]`` and counted as a failure. After a failed attempt
  the entry waits 10 s, 20 s, 40 s ... (max 10 min) before the next try.
- One claim per event: claim key = event key. Exception: the first scan (old state had
  no plays) folds all NEW_PLAY events into a single ``SETUP:<YYYY-MM-DDTHH:MM>`` entry.
- If the Telegram send succeeded but saving ``state.json`` failed, the next run derives
  the same events again; their claims already read ``sent`` so they are not re-sent.
- The PC runner (``who="pc"``) queues with ``not_before = now + 60 s`` so GitHub gets
  the first chance; afterwards the PC only sends if the claim is not ``sent``/held.

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
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Callable, Iterable

from tiyatro.claims import ClaimStore, claim_path, send_with_claim
from tiyatro.config import config_from_dict, default_config
from tiyatro.fetcher import fetch
from tiyatro.heartbeat import github_run_status, pc_status, read_heartbeat
from tiyatro.models import ISTANBUL, Config, Event, Play, Session, Status
from tiyatro.notify import Telegram, default_transport, format_message
from tiyatro.parser import ParseError, parse_play, parse_venue
from tiyatro.rules import matching_targets
from tiyatro.state import cleanup, diff, empty_state, next_opening
from tiyatro.store import Store
from tiyatro.timeutil import parse_iso

STATE_PATH = "state.json"
CONFIG_PATH = "config.json"
REQUEST_GAP_S = 2
FETCH_FAIL_ALERT_AT = 3
OUTBOX_MAX_AGE = timedelta(hours=6)
OUTBOX_MAX_ATTEMPTS = 20
PC_DELAY = timedelta(seconds=60)
RETRY_BASE_S = 10
RETRY_MAX_S = 600
DROPPED_LOG_MAX = 50
BURST_LEAD = timedelta(minutes=10)
BURST_INTERVAL_S = 12
BURST_MAX_SECONDS = 780
PC_WINDOW = timedelta(minutes=60)
PC_SECOND_WARNING = timedelta(minutes=10)
DAILY_AT = dtime(12, 0)
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


def _prepare(state: dict) -> dict:
    """Fill in runner-owned keys in place."""
    state.setdefault("plays", {})
    state.setdefault("emitted", {})
    if not isinstance(state.get("outbox"), list):
        state["outbox"] = []
    meta = state.setdefault("meta", {})
    meta.setdefault("last_scan", None)
    meta.setdefault("fetch_fail_streak", 0)
    return state


def load_state(store) -> dict:
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
    st = copy.deepcopy(state)
    st.get("meta", {}).pop("last_scan", None)
    return json.dumps(st, sort_keys=True, default=str)


def _emit(state: dict, kind: str, key: str, payload: dict, now: datetime) -> Event | None:
    if key in state["emitted"]:
        return None
    state["emitted"][key] = now.isoformat()
    return Event(kind, key, "", None, [], payload)


# ---- outbox ------------------------------------------------------------------

def _enqueue(state: dict, key: str, events: list[Event], now: datetime, who: str) -> None:
    if any(e.get("key") == key for e in state["outbox"]):
        return
    entry = {"key": key, "created": now.isoformat(), "attempts": 0,
             "not_before": (now + PC_DELAY if who == "pc" else now).isoformat()}
    if key.startswith("SETUP:"):
        entry["events"] = [event_to_dict(e) for e in events]
    else:
        entry["event"] = event_to_dict(events[0])
    state["outbox"].append(entry)


def _queue_events(state: dict, events: list[Event], first_scan: bool, now: datetime,
                  who: str) -> None:
    setup = [e for e in events if e.kind == "NEW_PLAY"] if first_scan else []
    if setup:
        local = now.astimezone(ISTANBUL)
        _enqueue(state, f"SETUP:{local:%Y-%m-%dT%H:%M}", setup, now, who)
    for ev in events:
        if ev not in setup:
            _enqueue(state, ev.key, [ev], now, who)


def _entry_events(entry: dict) -> list[Event]:
    raw = entry.get("events")
    if not isinstance(raw, list):
        raw = [entry.get("event") or {}]
    return [event_from_dict(d) for d in raw]


def _claim_sent(store, key: str) -> bool:
    try:
        data, _sha = store.get(claim_path(key))
    except Exception:
        return False  # fail open: rather try to send
    return isinstance(data, dict) and data.get("status") == "sent"


class _AllParts:
    """Telegram stand-in for ``send_with_claim``: sends every part, returns the last id."""

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
    log.append({"key": entry.get("key"), "reason": reason, "at": now.isoformat(),
                "attempts": entry.get("attempts", 0)})
    del log[:-DROPPED_LOG_MAX]


def _deliver(state: dict, store, tg, claims: ClaimStore, now: datetime,
             plays: dict[str, Play], config: Config) -> int:
    """Try every due outbox entry once. Returns the number of dropped (failed) entries."""
    failures = 0
    keep: list[dict] = []
    for entry in state["outbox"]:
        key = str(entry.get("key", ""))
        created = parse_iso(entry.get("created")) or now
        attempts = int(entry.get("attempts", 0))
        expired = ("age" if now - created > OUTBOX_MAX_AGE
                   else "attempts" if attempts >= OUTBOX_MAX_ATTEMPTS else None)
        if expired:
            if not _claim_sent(store, key):
                _log_drop(state, entry, expired, now)
                failures += 1
            continue
        not_before = parse_iso(entry.get("not_before")) or now
        if not_before > now:
            keep.append(entry)
            continue
        if _claim_sent(store, key):
            continue  # delivered by us earlier or by the other runner
        parts = [p for p in format_message(_entry_events(entry), plays, config) if p]
        if not parts:
            continue
        entry["attempts"] = attempts + 1
        # False = held by the other runner or the send failed: retry next run.
        if not send_with_claim(claims, _AllParts(tg, parts), key, parts[0]):
            # Back off so a short outage during a 12 s burst cannot burn all attempts.
            delay = min(RETRY_BASE_S * 2 ** attempts, RETRY_MAX_S)
            entry["not_before"] = (now + timedelta(seconds=delay)).isoformat()
            keep.append(entry)
    state["outbox"] = keep
    return failures


def _discard_outbox(state: dict, events: Iterable[Event] = ()) -> None:
    meta = state["meta"]
    meta["paused_discarded"] = (int(meta.get("paused_discarded", 0))
                                + len(state["outbox"]) + len(list(events)))
    state["outbox"] = []


def _dispatch(state: dict, events: list[Event], first_scan: bool, store, tg, claims,
              now: datetime, plays: dict[str, Play], config: Config, who: str) -> int:
    if config.paused:
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
        if requests and not burst:
            sleep(REQUEST_GAP_S)
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


def run_once(store, tg, claims, now: datetime, who: str = "github",
             fetch_fn: Callable = fetch, sleep: Callable = time.sleep,
             persist_state: bool = True, state: dict | None = None,
             only_slugs: list[str] | None = None) -> RunResult:
    config = load_config(store)
    state = _prepare(copy.deepcopy(state) if state is not None else load_state(store))
    burst = only_slugs is not None
    failures = 0

    # R2: deliver what earlier runs could not.
    if config.paused:
        _discard_outbox(state)
    else:
        failures += _deliver(state, store, tg, claims, now, {}, config)

    plays, unscanned, play_failures, venue_error = _scan(config, state, fetch_fn, sleep,
                                                         only_slugs)
    failures += play_failures
    first_scan = not state["plays"]
    events: list[Event] = []

    if not burst:
        meta = state["meta"]
        if venue_error:
            failures += 1
            if int(meta.get("fetch_fail_streak", 0)) == 0:
                meta["fetch_fail_since"] = now.isoformat()
            meta["fetch_fail_streak"] = int(meta.get("fetch_fail_streak", 0)) + 1
            if meta["fetch_fail_streak"] == FETCH_FAIL_ALERT_AT:
                since = meta.get("fetch_fail_since") or now.isoformat()
                ev = _emit(state, "FETCH_FAILING", f"FETCH_FAILING:{since}",
                           {"detail": f"Biletinial {FETCH_FAIL_ALERT_AT} kez üst üste "
                                      f"okunamadı ({venue_error})"}, now)
                if ev:
                    events.append(ev)
        else:
            meta["fetch_fail_streak"] = 0
            meta["fetch_fail_since"] = None

    # A failed venue scan never reaches diff (it would look like every play vanished).
    # With several sources, plays from the venues that did load are diffed as a subset.
    if burst or not venue_error or plays:
        state, diff_events = diff(state, plays, config, now,
                                  complete=not burst and not venue_error,
                                  unscanned=unscanned)
        state = _prepare(cleanup(state, config, now))
        events.extend(diff_events)

    daily = False
    if not burst and not venue_error and daily_due(state, now):
        state["meta"]["last_daily"] = now.astimezone(ISTANBUL).date().isoformat()
        daily = True

    failures += _dispatch(state, events, first_scan, store, tg, claims, now,
                          {p.slug: p for p in plays}, config, who)

    save_error = save_state(store, state) if persist_state else None
    return RunResult(events, "burst" if burst else "normal", len(plays), state,
                     failures, save_error, daily)


# ---- modes -----------------------------------------------------------------------

def _window_openings(state: dict, config: Config, now: datetime) -> list[tuple[str, datetime]]:
    """(slug, opens_at) of watched sessions with now in [opens_at-10m, opens_at+10m]."""
    out = []
    for slug, entry in (state.get("plays") or {}).items():
        watch = config.plays.get(slug)
        if watch is None or not watch.active or entry.get("removed"):
            continue
        for sid, rec in (entry.get("sessions") or {}).items():
            if rec.get("gone"):
                continue
            try:
                opens = parse_iso(rec.get("opens_at"))
                if opens is None or not opens - BURST_LEAD <= now <= opens + BURST_LEAD:
                    continue
                session = Session(sid, parse_iso(rec["start"]), None, Status(rec["status"]),
                                  opens, rec.get("url", ""), "")
            except (KeyError, ValueError, TypeError):
                continue
            if matching_targets(watch, session):
                out.append((slug, opens))
    return out


def plan_mode(state: dict, config: Config, now: datetime) -> tuple[str, datetime | None]:
    found = _window_openings(state, config, now)
    if not found:
        return "normal", None
    return "burst", max(opens for _, opens in found) + BURST_LEAD


def burst_loop(store, tg, claims, now_fn: Callable[[], datetime], sleep: Callable,
               until: datetime, interval: float = BURST_INTERVAL_S,
               fetch_fn: Callable = fetch, max_seconds: float = BURST_MAX_SECONDS, *,
               who: str = "github", persist_state: bool = True,
               state: dict | None = None) -> RunResult | None:
    """Re-scan only the plays inside a burst window every ``interval`` seconds.

    Stops at ``until``, after ``max_seconds`` or when no watched session is in its
    window any more. State is saved only when an iteration changed something.
    """
    start = now_fn()
    deadline = min(until, start + timedelta(seconds=max_seconds))
    state = _prepare(copy.deepcopy(state)) if state is not None else load_state(store)
    result: RunResult | None = None
    while True:
        now = now_fn()
        if now >= deadline:
            break
        slugs = sorted({slug for slug, _ in _window_openings(state, load_config(store), now)})
        if not slugs:
            break
        before = _fingerprint(state)
        result = run_once(store, tg, claims, now, who=who, fetch_fn=fetch_fn, sleep=sleep,
                          persist_state=False, state=state, only_slugs=slugs)
        state = result.state
        if persist_state and _fingerprint(state) != before:
            result.save_error = save_state(store, state)
        sleep(interval)
    return result


def daily_due(state: dict, now: datetime) -> bool:
    local = now.astimezone(ISTANBUL)
    if local.time() < DAILY_AT:
        return False
    return (state.get("meta") or {}).get("last_daily") != local.date().isoformat()


def pc_watch(store, state: dict, config: Config, now: datetime, claims, tg) -> list[Event]:
    """Opening-window liveness checks (PC heartbeat, own GitHub schedule).

    Mutates ``state`` in place (emitted keys, outbox); the caller saves it.
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
    try:
        gh = github_run_status(store.runs(), now)
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


def main() -> int:
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

        claims = ClaimStore(store, "github", now_fn)
        store.ensure_branch()
        res = run_once(store, tg, claims, now_fn(), fetch_fn=fetch, sleep=time.sleep)
        state, errors = res.state, [res.save_error]
        config = load_config(store)
        mode, until = plan_mode(state, config, now_fn())
        if mode == "burst":
            br = burst_loop(store, tg, claims, now_fn, time.sleep, until, fetch_fn=fetch,
                            state=state)
            if br is not None:
                state = br.state
                errors.append(br.save_error)
        before = _fingerprint(state)
        watch_events = pc_watch(store, state, config, now_fn(), claims, tg)
        if _fingerprint(state) != before:
            errors.append(save_state(store, state))
        if res.daily:
            claims.purge()
        print(f"mode={mode} scanned={res.scanned} events={len(res.events)} "
              f"watch_events={len(watch_events)} failures={res.failures} "
              f"outbox={len(state['outbox'])}")
        errors = [e for e in errors if e]
        if errors:
            print(f"state not saved: {', '.join(errors)}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(_scrub(f"runner failed: {type(exc).__name__}: {exc}", secrets), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
