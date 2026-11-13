"""Home-PC heartbeat record and liveness / divergence judgements."""
from __future__ import annotations

from datetime import datetime

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


def _state_statuses(state: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for play in (state.get("plays") or {}).values():
        for sid, sess in (play.get("sessions") or {}).items():
            out[sid] = sess.get("status")
    return out


def pc_status(hb: dict | None, state: dict, now: datetime, max_age_s: int = 600) -> str:
    ts = parse_iso(hb.get("ts")) if hb else None
    if ts is None or (now - ts).total_seconds() > max_age_s:
        return "inactive"
    theirs = _state_statuses(state)
    for sid, status in (hb.get("observed") or {}).items():
        if sid in theirs and "gone" not in (status, theirs[sid]) and status != theirs[sid]:
            return "diverged"
    return "active"


def github_run_status(runs: list[dict], now: datetime, grid_s: int = 300) -> dict:
    if not runs:
        return {"last_run": None, "lag_s": 0, "gap_s": 0, "healthy": False}
    latest = parse_iso(runs[0]["created_at"])
    gap_s = int((now - latest).total_seconds())
    lag_s = 0
    if len(runs) > 1:
        prev = parse_iso(runs[1]["created_at"])
        lag_s = max(0, int((latest - prev).total_seconds()) - grid_s)
    both_failed = (len(runs) > 1
                   and all(r.get("conclusion") == "failure" for r in runs[:2]))
    return {"last_run": latest.isoformat(), "lag_s": lag_s, "gap_s": gap_s,
            "healthy": gap_s <= 900 and not both_failed}
