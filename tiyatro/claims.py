"""Cross-runner notification claim locks stored as small JSON files.

Delivery is fail-open: a duplicate alert is preferred over a missed one.

Claim key = event key, except ON_SALE: one claim per session (``ON_SALE:<sid>``), because
each runner numbers its own ON_SALE episodes (``ON_SALE:<sid>:<n>``) and the counters
drift apart when only one runner sees an iade re-open. A ``sent`` ON_SALE claim is
re-claimable once its ``sent_at`` is older than the ON_SALE gap (``reclaim_after``), so
a later re-open is never suppressed by the other runner's earlier alert.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable

from tiyatro.timeutil import parse_iso

LEASE = timedelta(seconds=45)
_SAFE = re.compile(r"[A-Za-z0-9.\-]")


def claim_key(event) -> str:
    """Claim key for an ``Event`` or an event key: ``ON_SALE:<sid>`` for ON_SALE
    events (key ``ON_SALE:<sid>:<n>``), the event key itself for everything else."""
    key = event if isinstance(event, str) else event.key
    if key.startswith("ON_SALE:") and key.count(":") >= 2:
        return key.rsplit(":", 1)[0]
    return key


def claim_path(key: str) -> str:
    """claims/<key>.json; every char outside [A-Za-z0-9.-] becomes _XX (utf-8 hex bytes).

    '_' is itself escaped, so the mapping is injective.
    """
    out = []
    for ch in key:
        if _SAFE.fullmatch(ch):
            out.append(ch)
        else:
            out.extend(f"_{b:02x}" for b in ch.encode("utf-8"))
    return f"claims/{''.join(out)}.json"


class ClaimStore:
    def __init__(self, store, who: str, clock: Callable[[], datetime]):
        self.store = store
        self.who = who
        self.clock = clock

    def _now(self) -> datetime:
        try:
            return self.store.server_now()
        except Exception:
            return self.clock()

    def _new(self, key: str, now: datetime, attempts: int = 1) -> dict:
        return {"key": key, "claimed_by": self.who, "claimed_at": now.isoformat(),
                "lease_until": (now + LEASE).isoformat(), "status": "claimed",
                "sent_at": None, "message_id": None, "attempts": attempts, "note": None}

    def claim(self, key: str, reclaim_after: timedelta | None = None) -> bool:
        """True if we may send ``key`` now. ``reclaim_after``: a ``sent`` claim whose
        ``sent_at`` is at least this old may be claimed again (CAS on the sha)."""
        try:
            now = self._now()
            path = claim_path(key)
            if self.store.create_file(path, self._new(key, now)):
                return True
            cur, sha = self.store.get(path)
            if cur is None:
                return bool(self.store.create_file(path, self._new(key, now)))
            status = cur.get("status")
            if status == "sent":
                sent_at = parse_iso(cur.get("sent_at")) if reclaim_after else None
                if sent_at is None or now - sent_at < reclaim_after:
                    return False
            if status == "claimed":
                lease = parse_iso(cur.get("lease_until"))
                if lease is not None and lease > now:
                    return False
            return bool(self.store.put(
                path, self._new(key, now, int(cur.get("attempts", 0)) + 1), sha))
        except Exception:
            return True

    def _update(self, key: str, skip=None, **fields) -> None:
        path = claim_path(key)
        now = self._now()
        for _ in range(3):
            cur, sha = self.store.get(path)
            if cur is not None and skip is not None and skip(cur):
                return
            if cur is None:
                cur = self._new(key, now)
                cur.update(fields)
                if self.store.create_file(path, cur):
                    return
                continue
            cur.update(fields)
            if self.store.put(path, cur, sha):
                return
        raise RuntimeError("claim update conflict")

    def mark_sent(self, key: str, message_id: int) -> None:
        self._update(key, status="sent", sent_at=self._now().isoformat(),
                     message_id=message_id, claimed_by=self.who)

    def mark_failed(self, key: str, note: str = "") -> None:
        """No-op if the record is already `sent` or now owned by someone else."""
        self._update(key, skip=lambda c: c.get("status") == "sent"
                     or c.get("claimed_by") != self.who,
                     status="failed", note=note[:200])

    def purge(self, older_than_hours: int = 48) -> int:
        """Delete claims older than the cutoff (server time); returns the number deleted.

        Store errors never propagate: on a listing/delete failure the count deleted so
        far is returned.
        """
        count = 0
        try:
            cutoff = self._now() - timedelta(hours=older_than_hours)
            for path in self.store.list_paths("claims/"):
                cur, sha = self.store.get(path)
                at = parse_iso(cur.get("claimed_at")) if cur else None
                if at is not None and at < cutoff:
                    self.store.delete(path, sha)
                    count += 1
        except Exception:
            pass
        return count


def send_with_claim(claims: ClaimStore, tg, key: str, text: str,
                    reclaim_after: timedelta | None = None) -> bool:
    if not claims.claim(key, reclaim_after):
        return False
    try:
        message_id = tg.send(text)
    except Exception as exc:
        try:
            claims.mark_failed(key, type(exc).__name__)
        except Exception:
            pass
        return False
    try:
        claims.mark_sent(key, message_id)
    except Exception:
        pass
    return True
