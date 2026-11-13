"""Home-PC heartbeat record and liveness / divergence judgements."""
from __future__ import annotations

from datetime import datetime, timezone

from tiyatro.timeutil import parse_iso

PATH = "heartbeat.json"


def write_heartbeat(store, now: datetime, last_scan_ok: datetime | None,
                    observed: dict[str, str]) -> None:
    record = {"ts": now.isoformat(),
              "last_scan_ok": last_scan_ok.isoformat() if last_scan_ok else None,
              "observed": dict(observed)}
    store.update_json(PATH, lambda _old: record)


def read_heartbeat(store) -> dict | None:
    data, _sha = store.get(PATH)
    return data


def _ts(value) -> datetime | None:
    """Tolerant timestamp: None/garbled -> None; naive -> assumed UTC."""
    if not isinstance(value, str):
        return None
    try:
        d = parse_iso(value)
    except ValueError:
        return None
    if d is not None and d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _state_statuses(state: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for play in (state.get("plays") or {}).values():
        for sid, sess in (play.get("sessions") or {}).items():
            out[sid] = sess.get("status")
    return out


def pc_status(hb: dict | None, state: dict, now: datetime, max_age_s: int = 600) -> str:
    ts = _ts(hb.get("ts")) if isinstance(hb, dict) else None
    if ts is None or (now - ts).total_seconds() > max_age_s:
        return "inactive"
    theirs = _state_statuses(state)
    observed = hb.get("observed")
    for sid, status in (observed.items() if isinstance(observed, dict) else ()):
        if sid in theirs and "gone" not in (status, theirs[sid]) and status != theirs[sid]:
            return "diverged"
    return "active"


def _run_end(run) -> datetime | None:
    """Completion time (``updated_at``), falling back to ``created_at``."""
    if not isinstance(run, dict):
        return None
    return _ts(run.get("updated_at")) or _ts(run.get("created_at"))


def github_run_status(runs: list[dict], now: datetime, grid_s: int = 300,
                      max_gap_s: int = 600) -> dict:
    """Health of the scheduled scan from its latest *completed* runs (newest first).

    ``gap_s``: seconds since the latest run ended (a long burst run is not "late").
    ``lag_s``: how much later than ``previous end + grid`` the latest run started.
    Healthy iff ``gap_s <= 600`` (5-min cron + startup slack) and the two latest runs
    did not both fail.
    """
    if not runs:
        return {"last_run": None, "lag_s": 0, "gap_s": 0, "healthy": False}
    latest_end = _run_end(runs[0])
    if latest_end is None:
        return {"last_run": None, "lag_s": 0, "gap_s": 0, "healthy": False}
    latest_start = _ts(runs[0].get("created_at")) or latest_end
    gap_s = int((now - latest_end).total_seconds())
    lag_s = 0
    prev_end = _run_end(runs[1]) if len(runs) > 1 else None
    if prev_end is not None:
        lag_s = max(0, int((latest_start - prev_end).total_seconds()) - grid_s)
    latest = latest_end
    both_failed = (len(runs) > 1
                   and all(isinstance(r, dict) and r.get("conclusion") == "failure"
                           for r in runs[:2]))
    return {"last_run": latest.isoformat(), "lag_s": lag_s, "gap_s": gap_s,
            "healthy": gap_s <= max_gap_s and not both_failed}
