"""Non-UI core of the PC app: scan loop, heartbeat, takeover, config editing, refresh.

The PC is the second runner next to GitHub Actions (``who="pc"``):

- It keeps its own working copy of the state in ``<local_dir>/state.json`` (git-ignored,
  written atomically) and never writes ``state.json`` on the data branch. When the local
  copy is missing, the GitHub ``state.json`` is the baseline (so no NEW_PLAY storm).
  At startup a local copy older than 24 h (by ``meta.last_scan``) is discarded for
  GitHub's; a younger one takes over GitHub's ``emitted`` keys and drops outbox entries
  GitHub already emitted, so alerts handled while the PC was off are never re-sent
  (GitHub's claims are purged after 48 h and cannot be relied on for that).
- Failures never cause a tight retry loop: a failed heartbeat write, or a config/state
  read failing before the first scan, is retried after 30 s, doubling up to 5 min;
  success resets the backoff.
- ``tick()`` scans when due: every 300 s normally; in a burst window (see
  ``tiyatro.runner.plan_mode``) only the plays in the window, at the runner's burst
  cadence (12 s / 30 s / 60 s + jitter). Between scans a tick only delivers outbox
  entries whose ``not_before`` has passed (``run_once(only_slugs=[])``, no fetches).
- Takeover: the runner queues PC sends with ``not_before = now + 60 s`` and the claims
  make the PC skip anything GitHub already sent. Nothing of that is re-implemented here.
- Heartbeat: written every 4 min while a watched opening is <= 60 min away or a burst is
  active, with ``last_scan_ok`` and ``observed`` (session id -> status of the sessions
  of actively watched plays).
- GitHub run health is read every 5 min for display only (the PC sends no Telegram
  message about GitHub lag).
- Desktop: ``on_notify(callback)`` gets ``(title, text, events)`` for every event the
  PC observed (its own scan, its outbox, or an alert GitHub sent first), once per event
  key (remembered in ``<local_dir>/notified.json`` for 7 days). Events discarded while
  paused are not shown.

Config edits go through ``Store.update_json`` (merged with concurrent Worker writes by
the store's retry) and are validated with ``config_from_dict`` before anything is
written; a no-op edit writes nothing. Scans (``tick``/``refresh_now``) are serialised by
a lock so a UI thread and a worker thread can share one ``Service``.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from pc import configedit
from pc.settings import DEFAULT_LOCAL_DIR
from pc.viewmodel import Row, build_rows, format_events_for_desktop, when_text
from tiyatro.config import config_from_dict, default_config
from tiyatro.fetcher import fetch
from tiyatro.heartbeat import github_run_status, write_heartbeat
from tiyatro.models import ISTANBUL, Config, Event, Play
from tiyatro.runner import (BURST_INTERVAL_S, BURST_LEAD, BURST_TIERS, CONFIG_PATH,
                            PC_WINDOW, _burst_targets, _entry_events, _prepare, _scan,
                            load_config, load_state, plan_mode, run_once, scan_runs)
from tiyatro.state import next_opening
from tiyatro.tags import apply_overrides, derive_tags
from tiyatro.timeutil import parse_iso

NORMAL_INTERVAL_S = 300
HEARTBEAT_EVERY = timedelta(minutes=4)
GH_CHECK_EVERY = timedelta(minutes=5)
NOTIFIED_TTL = timedelta(days=7)
ERROR_TEXT_MAX = 200
LOCAL_STATE_MAX_AGE = timedelta(hours=24)  # older local state: GitHub's is the baseline
BACKOFF_FIRST_S = 30
BACKOFF_MAX_S = 300


def _next_backoff(current: int) -> int:
    """30 s after the first failure, doubling up to 5 min."""
    return BACKOFF_FIRST_S if not current else min(current * 2, BACKOFF_MAX_S)


def _str_set(value) -> set[str] | None:
    """A JSON list as a set of its strings; anything else -> None."""
    if not isinstance(value, list):
        return None
    return {v for v in value if isinstance(v, str)}


@dataclass
class TickResult:
    scanned: bool
    events: list[Event]
    gh_status: dict | None
    errors: list[str]
    mode: str
    next_opening: datetime | None
    status: dict = field(default_factory=dict)


class RefreshError(RuntimeError):
    """"Listeyi güncelle" failed; the previous list is kept."""


class _NoChange(Exception):
    def __init__(self, data: dict):
        super().__init__("no change")
        self.data = data


def _default_clock() -> datetime:
    return datetime.now(ISTANBUL)


def _err(what: str, exc: BaseException) -> str:
    return f"{what}: {type(exc).__name__}: {str(exc)[:ERROR_TEXT_MAX]}".rstrip(": ")


def _dt(value) -> datetime | None:
    try:
        return parse_iso(value) if isinstance(value, str) else None
    except ValueError:
        return None


class Service:
    def __init__(self, store, tg, claims, clock: Callable[[], datetime] = _default_clock,
                 fetch_fn: Callable = fetch, sleep: Callable = time.sleep,
                 local_dir: Path | str = DEFAULT_LOCAL_DIR,
                 rand: Callable[[float, float], float] = random.uniform):
        self._store = store
        self._tg = tg
        self._claims = claims
        self._clock = clock
        self._fetch = fetch_fn
        self._sleep = sleep
        self._local = Path(local_dir)
        self._rand = rand
        self._lock = threading.RLock()
        self._callbacks: list[Callable] = []
        self._config: Config | None = None
        self._state: dict | None = None
        self._next_scan_at: datetime | None = None
        self._hb_at: datetime | None = None       # last successful heartbeat write
        self._hb_try_at: datetime | None = None   # last attempt
        self._hb_backoff = 0                      # seconds; 0 = last write succeeded
        self._start_try_at: datetime | None = None  # failed config/state read before
        self._start_backoff = 0                     # the first scan
        self._reconciled = False  # local state checked against GitHub's state.json
        self._gh_at: datetime | None = None
        self._gh: dict | None = None
        self._last_scan_ok: datetime | None = None
        self._errors: list[str] = []
        notified = self._read_json("notified.json")
        self._notified: dict[str, str] = (
            {k: v for k, v in notified.items() if isinstance(k, str) and isinstance(v, str)}
            if isinstance(notified, dict) else {})
        listing = self._read_json("plays.json")
        listing = listing if isinstance(listing, dict) else {}
        self._known: set[str] | None = _str_set(listing.get("slugs"))
        self.new_slugs: set[str] = _str_set(listing.get("new")) or set()
        self.removed_slugs: set[str] = _str_set(listing.get("removed")) or set()
        self.refresh_failures: list[str] = []  # play pages that failed in the last refresh
        self._plays: list[Play] = []

    # ---- local files -------------------------------------------------------------
    def _read_json(self, name: str):
        try:
            return json.loads((self._local / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_json(self, name: str, data) -> None:
        """Atomic: write a temp file next to the target, then rename over it."""
        self._local.mkdir(parents=True, exist_ok=True)
        path = self._local / name
        tmp = path.with_name(name + ".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1),
                           encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # ---- accessors ---------------------------------------------------------------
    def on_notify(self, callback: Callable[[str, str, list[Event]], None]) -> None:
        self._callbacks.append(callback)

    @property
    def config(self) -> Config | None:
        return self._config

    @property
    def state(self) -> dict | None:
        """The PC's working state (local copy; None until known).

        A local copy whose last scan is missing or older than 24 h is ignored: GitHub
        may have handled (and its claims been purged for) alerts the PC never saw.
        """
        if self._state is None and not self._reconciled:
            local = self._read_json("state.json")
            if isinstance(local, dict) and self._fresh(local):
                self._state = _prepare(local)
        return self._state

    def _fresh(self, local: dict) -> bool:
        meta = local.get("meta") if isinstance(local.get("meta"), dict) else {}
        last = _dt(meta.get("last_scan"))
        if last is None or last.tzinfo is None:
            return False
        return self._clock() - last <= LOCAL_STATE_MAX_AGE

    @property
    def plays(self) -> list[Play]:
        """Plays of the last successful ``refresh_now``."""
        return list(self._plays)

    def reload_config(self) -> Config:
        """Re-read ``config.json`` (Worker edits become visible); keeps the last good
        config if the read fails (the exception propagates)."""
        config = load_config(self._store)
        with self._lock:
            self._config = config
        return config

    def _ensure_state(self, errors: list[str]) -> dict | None:
        """The working state; on first use it is reconciled with GitHub's state.json:
        no (fresh) local copy -> GitHub's state is the baseline; otherwise GitHub's
        ``emitted`` keys are merged in and outbox entries GitHub already emitted are
        dropped, so the PC never re-emits an alert GitHub handled meanwhile."""
        if self._reconciled and self._state is not None:
            return self._state
        local = self.state
        try:
            github = load_state(self._store)
        except Exception as exc:
            errors.append(_err("GitHub state.json okunamadı", exc))
            return local  # fresh local copy (<= 24 h): usable; reconcile next tick
        if local is None:
            self._state = github
        else:
            theirs = github.get("emitted") if isinstance(github.get("emitted"), dict) else {}
            for key, at in theirs.items():
                local["emitted"].setdefault(key, at)
            local["outbox"] = [e for e in local["outbox"] if e.get("key") not in theirs]
        self._reconciled = True
        try:
            self._write_json("state.json", self._state)
        except Exception as exc:
            errors.append(_err("yerel durum yazılamadı", exc))
        return self._state

    # ---- cadence -------------------------------------------------------------------
    def scan_interval(self, now: datetime) -> float:
        """Seconds between scans: 300 normally, the runner's burst tier cadence (with
        jitter) while a watched session is in its burst window."""
        state, config = self.state, self._config
        if state is None or config is None:
            return float(NORMAL_INTERVAL_S)
        targets = _burst_targets(state, config, now)
        if not targets:
            return float(NORMAL_INTERVAL_S)
        tier = min(t for *_rest, t in targets)
        base = BURST_INTERVAL_S if tier == 0 else BURST_TIERS[tier][1]
        jitter = BURST_TIERS[tier][2]
        return max(1.0, base + self._rand(-jitter, jitter))

    def _plan_next_scan(self, now: datetime) -> datetime:
        at = now + timedelta(seconds=self.scan_interval(now))
        state, config = self.state, self._config
        if state is not None and config is not None \
                and plan_mode(state, config, now)[0] == "normal":
            opening = next_opening(state, config, now)
            if opening is not None and opening - BURST_LEAD > now:
                at = min(at, opening - BURST_LEAD)  # do not start a burst late
        return at

    def _in_window(self, state: dict, config: Config, now: datetime) -> bool:
        if plan_mode(state, config, now)[0] == "burst":
            return True
        opening = next_opening(state, config, now)
        return opening is not None and opening - now <= PC_WINDOW

    def next_interval(self, now: datetime | None = None) -> float:
        """Seconds until ``tick()`` should run next: the next scan, the next outbox
        delivery (PC sends wait 60 s) or the next heartbeat, whichever comes first."""
        now = now or self._clock()
        state, config = self.state, self._config
        if self._next_scan_at is not None:
            due = [self._next_scan_at]
        elif self._start_backoff:  # config/state unreadable so far: back off
            due = [self._start_try_at + timedelta(seconds=self._start_backoff)]
        else:
            due = [now]
        if state is not None:
            for entry in state.get("outbox") or []:
                due.append(_dt(entry.get("not_before")) or now)
            if config is not None and self._in_window(state, config, now):
                due.append(self._heartbeat_due(now))
        return max(1.0, (min(due) - now).total_seconds())

    def _heartbeat_due(self, now: datetime) -> datetime:
        if self._hb_backoff:
            return self._hb_try_at + timedelta(seconds=self._hb_backoff)
        return self._hb_at + HEARTBEAT_EVERY if self._hb_at else now

    def _outbox_due(self, state: dict, now: datetime) -> bool:
        return any((_dt(e.get("not_before")) or now) <= now
                   for e in state.get("outbox") or [])

    # ---- scanning --------------------------------------------------------------------
    def tick(self) -> TickResult:
        with self._lock:
            now = self._clock()
            errors: list[str] = []
            events: list[Event] = []
            scanned = False
            mode = "normal"
            try:
                self.reload_config()
            except Exception as exc:
                errors.append(_err("config.json okunamadı", exc))
            config = self._config
            state = self._ensure_state(errors) if config is not None else None
            if config is None or state is None:
                self._start_backoff = _next_backoff(self._start_backoff)
                self._start_try_at = now
            else:
                self._start_backoff = 0
                mode = plan_mode(state, config, now)[0]
                only: list[str] | None = None
                act = False
                if self._next_scan_at is None or now >= self._next_scan_at:
                    act = scanned = True
                    if mode == "burst":
                        only = sorted({s for s, *_r in _burst_targets(state, config, now)})
                elif self._outbox_due(state, now):
                    act, only = True, []  # delivery only: no fetch
                if act:
                    try:
                        events = self._run(now, config, only, self._fetch, self._sleep,
                                           not scanned, errors)
                    except Exception as exc:
                        errors.append(_err("tarama başarısız", exc))
                    finally:
                        if scanned:
                            self._next_scan_at = self._plan_next_scan(now)
                state = self._state
                mode = plan_mode(state, config, now)[0]
                self._heartbeat(now, state, config, errors)
            self._check_github(now, errors)
            self._errors = errors
            opening = next_opening(state, config, now) if state and config else None
            return TickResult(scanned, events, self._gh, list(errors), mode, opening,
                              self.status())

    def _run(self, now: datetime, config: Config, only: list[str] | None,
             fetch_fn: Callable, sleep: Callable, delivery_only: bool,
             errors: list[str]) -> list[Event]:
        state = self._state
        before = [ev for entry in state["outbox"] for ev in _entry_events(entry)]
        discarded = int(state["meta"].get("paused_discarded", 0) or 0)
        res = run_once(self._store, self._tg, self._claims, now, who="pc",
                       fetch_fn=fetch_fn, sleep=sleep, persist_state=False, state=state,
                       only_slugs=only, config=config)
        new = res.state
        if delivery_only:  # not a scan: keep the last real scan time
            new["meta"]["last_scan"] = state["meta"].get("last_scan")
        elif (res.scanned > 0 if only is not None
              else int(new["meta"].get("fetch_fail_streak", 0) or 0) == 0):
            self._last_scan_ok = now
        self._state = new
        try:
            self._write_json("state.json", new)
        except Exception as exc:
            errors.append(_err("yerel durum yazılamadı", exc))
        if int(new["meta"].get("paused_discarded", 0) or 0) == discarded:
            after = [ev for entry in new["outbox"] for ev in _entry_events(entry)]
            self._notify(before + list(res.events) + after, now, errors)
        return list(res.events)

    def _notify(self, events: list[Event], now: datetime, errors: list[str]) -> None:
        fresh: list[Event] = []
        for ev in events:
            if ev.key and ev.key not in self._notified \
                    and all(ev.key != f.key for f in fresh):
                fresh.append(ev)
        if not fresh:
            return
        for ev in fresh:
            self._notified[ev.key] = now.isoformat()
        self._notified = {k: v for k, v in self._notified.items()
                          if (_dt(v) or now) >= now - NOTIFIED_TTL}
        try:
            self._write_json("notified.json", self._notified)
        except Exception as exc:
            errors.append(_err("yerel durum yazılamadı", exc))
        new_plays = [e for e in fresh if e.kind == "NEW_PLAY"]
        batches = ([new_plays] if new_plays else []) + \
            [[e] for e in fresh if e.kind != "NEW_PLAY"]
        for batch in batches:
            title, text = format_events_for_desktop(batch, {}, self._config)
            for callback in list(self._callbacks):
                try:
                    callback(title, text, batch)
                except Exception as exc:
                    errors.append(_err("masaüstü bildirimi gösterilemedi", exc))

    def _observed(self, state: dict, config: Config) -> dict[str, str]:
        out = {}
        for slug, entry in (state.get("plays") or {}).items():
            watch = config.plays.get(slug)
            if watch is None or not watch.active or entry.get("removed"):
                continue
            for sid, rec in (entry.get("sessions") or {}).items():
                if isinstance(rec, dict) and not rec.get("gone"):
                    out[sid] = rec.get("status")
        return out

    def _heartbeat(self, now: datetime, state: dict, config: Config,
                   errors: list[str]) -> None:
        if not self._in_window(state, config, now) or now < self._heartbeat_due(now):
            return
        self._hb_try_at = now
        try:
            write_heartbeat(self._store, now, self._last_scan_ok,
                            self._observed(state, config))
            self._hb_at = now
            self._hb_backoff = 0
        except Exception as exc:  # retry after 30 s, 60 s, ... up to 5 min
            self._hb_backoff = _next_backoff(self._hb_backoff)
            errors.append(_err("heartbeat yazılamadı", exc))

    def _check_github(self, now: datetime, errors: list[str]) -> None:
        if self._gh_at is not None and now - self._gh_at < GH_CHECK_EVERY:
            return
        self._gh_at = now
        try:
            self._gh = github_run_status(scan_runs(self._store.runs(per_page=30)), now)
        except Exception as exc:
            errors.append(_err("GitHub çalışmaları okunamadı", exc))

    # ---- "Listeyi güncelle" ----------------------------------------------------------
    def refresh_now(self) -> list[Play]:
        """Scan venue + play pages now, fold the result into the local state like a
        normal scan (events, alerts) and mark NEW/REMOVED plays against the previous
        list. A blocked/empty scrape raises ``RefreshError`` and changes nothing."""
        with self._lock:
            now = self._clock()
            errors: list[str] = []
            try:
                self.reload_config()
            except Exception as exc:
                if self._config is None:
                    raise RefreshError(_err("config.json okunamadı; önceki liste "
                                            "korunuyor", exc)) from None
                errors.append(_err("config.json okunamadı", exc))
            config = self._config
            state = self._ensure_state(errors)
            if state is None:
                raise RefreshError("durum okunamadı; önceki liste korunuyor")
            prev = self._known if self._known is not None else \
                {s for s, e in state["plays"].items() if not e.get("removed")}

            cache: dict[str, object] = {}

            def recording(url: str):
                try:
                    result = self._fetch(url)
                except Exception as exc:
                    cache[url] = exc
                    raise
                cache[url] = result
                return result

            plays, unscanned, _failures, venue_error = _scan(config, state, recording,
                                                             self._sleep, None)
            if venue_error or not plays:
                reason = venue_error or ("oyun sayfaları okunamadı" if unscanned
                                         else "oyun bulunamadı")
                raise RefreshError(f"Liste güncellenemedi: Biletinial okunamadı "
                                   f"({reason}); önceki liste korunuyor")

            def replay(url: str):  # same pages again for run_once, no second request
                if url in cache:
                    hit = cache[url]
                    if isinstance(hit, Exception):
                        raise hit
                    return hit
                return self._fetch(url)

            try:
                self._run(now, config, None, replay, lambda _s: None, False, errors)
            except Exception as exc:
                errors.append(_err("tarama başarısız", exc))
            self._next_scan_at = self._plan_next_scan(now)

            current = {p.slug for p in plays} | set(unscanned)
            self.new_slugs = current - prev if prev else set()
            self.removed_slugs = prev - current
            self._known = current
            # A play whose own page failed is still listed: keep its previous entry.
            previous = {p.slug: p for p in self._plays}
            kept = [previous.get(slug) or self._play_from_state(slug)
                    for slug in sorted(unscanned)]
            self._plays = ([self._display_play(p, config) for p in plays]
                           + [p for p in kept if p is not None])
            self.refresh_failures = sorted(unscanned)
            try:
                self._write_json("plays.json", {"slugs": sorted(current),
                                                "new": sorted(self.new_slugs),
                                                "removed": sorted(self.removed_slugs)})
            except Exception as exc:
                errors.append(_err("yerel liste yazılamadı", exc))
            self._errors = errors
            return list(self._plays)

    def _display_play(self, play: Play, config: Config) -> Play:
        entry = (self._state or {}).get("plays", {}).get(play.slug) or {}
        fill = {k: entry.get(k) for k in ("genre", "duration_min", "age_limit", "price_from")
                if getattr(play, k) in (None, "")}
        play = dataclasses.replace(play, **fill)
        return dataclasses.replace(
            play, tags=apply_overrides(derive_tags(play), config.plays.get(play.slug)))

    def _play_from_state(self, slug: str) -> Play | None:
        entry = (self._state or {}).get("plays", {}).get(slug)
        if not isinstance(entry, dict):
            return None
        return Play(slug, str(entry.get("title") or slug), str(entry.get("url") or ""),
                    entry.get("genre"), entry.get("duration_min"), entry.get("age_limit"),
                    entry.get("price_from"), [], set(entry.get("tags") or []))

    def rows(self, now: datetime | None = None) -> list[Row]:
        state = self.state or {"plays": {}}
        config = self._config or default_config()
        everything = set(state.get("plays") or {}) | set(config.plays)
        return build_rows(state, config, everything - self.new_slugs, self.removed_slugs,
                          now or self._clock())

    # ---- status ------------------------------------------------------------------------
    def _next_opening_info(self, state: dict, config: Config, now: datetime) -> dict | None:
        at = next_opening(state, config, now)
        if at is None:
            return None
        title = ""
        for slug, entry in (state.get("plays") or {}).items():
            watch = config.plays.get(slug)
            if watch is None or not watch.active or entry.get("removed"):
                continue
            for rec in (entry.get("sessions") or {}).values():
                if isinstance(rec, dict) and not rec.get("gone") \
                        and rec.get("status") == "upcoming" and _dt(rec.get("opens_at")) == at:
                    title = entry.get("title") or slug
                    break
            if title:
                break
        return {"title": title, "at": at.isoformat(), "text": when_text(at)}

    def status(self) -> dict:
        now = self._clock()
        state, config = self.state, self._config
        mode, until = (plan_mode(state, config, now) if state is not None
                       and config is not None else ("normal", None))
        return {
            "last_scan": (state or {}).get("meta", {}).get("last_scan"),
            "last_scan_ok": self._last_scan_ok.isoformat() if self._last_scan_ok else None,
            "mode": mode,
            "burst_until": until.isoformat() if until else None,
            "next_opening": (self._next_opening_info(state, config, now)
                             if state is not None and config is not None else None),
            "paused": bool(config.paused) if config is not None else False,
            "outbox_pending": len((state or {}).get("outbox") or []),
            "heartbeat_age_s": (int((now - self._hb_at).total_seconds())
                                if self._hb_at else None),
            "github": copy.deepcopy(self._gh),
            "errors": list(self._errors),
        }

    # ---- config editing ----------------------------------------------------------------
    def _edit(self, op: Callable, *args) -> dict:
        def mutate(raw: dict) -> dict:
            new = copy.deepcopy(raw)
            op(new, *args)
            if new == raw:
                raise _NoChange(raw)
            try:
                config_from_dict(new)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError(f"geçersiz ayar: {exc}") from None
            return new

        try:
            new = self._store.update_json(CONFIG_PATH, mutate)
        except _NoChange as nc:
            new = nc.data
        try:
            config = config_from_dict(new)
        except (ValueError, TypeError, KeyError, AttributeError):
            return new
        with self._lock:  # not held during the store I/O above
            self._config = config
        return new

    def set_active(self, slug: str, active: bool) -> dict:
        return self._edit(configedit.set_active, slug, active)

    def ensure_target(self, slug: str, kind: str) -> dict:
        return self._edit(configedit.ensure_target, slug, kind)

    def set_people(self, slug: str, target_id: str, n: int) -> dict:
        return self._edit(configedit.set_people, slug, target_id, n)

    def set_days(self, slug: str, target_id: str, days) -> dict:
        return self._edit(configedit.set_days, slug, target_id, days)

    def set_days_group(self, slug: str, target_id: str, group: str) -> dict:
        return self._edit(configedit.set_days_group, slug, target_id, group)

    def set_day(self, slug: str, target_id: str, weekday: int, on: bool) -> dict:
        return self._edit(configedit.set_day, slug, target_id, weekday, on)

    def set_hours(self, slug: str, target_id: str, from_time=configedit.KEEP,
                  to_time=configedit.KEEP) -> dict:
        return self._edit(configedit.set_hours, slug, target_id, from_time, to_time)

    def set_day_override(self, slug: str, target_id: str, weekday: int, from_time,
                         to_time) -> dict:
        return self._edit(configedit.set_day_override, slug, target_id, weekday,
                          from_time, to_time)

    def set_excluded_date(self, slug: str, target_id: str, iso_date: str,
                          excluded: bool) -> dict:
        return self._edit(configedit.set_excluded_date, slug, target_id, iso_date,
                          excluded)

    def remove_target(self, slug: str, target_id: str) -> dict:
        return self._edit(configedit.remove_target, slug, target_id)

    def set_paused(self, paused: bool) -> dict:
        return self._edit(configedit.set_paused, paused)

    def pause(self) -> dict:
        return self.set_paused(True)

    def resume(self) -> dict:
        return self.set_paused(False)
