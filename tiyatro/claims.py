"""Cross-runner notification claim locks stored as small JSON files.

Delivery is fail-open: a duplicate alert is preferred over a missed one.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable

LEASE = timedelta(seconds=45)
_SAFE = re.compile(r"[A-Za-z0-9.\-]")


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


def _p(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


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

    def claim(self, key: str) -> bool:
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
                return False
            if status == "claimed":
                lease = _p(cur.get("lease_until"))
                if lease is not None and lease > now:
                    return False
            return bool(self.store.put(
                path, self._new(key, now, int(cur.get("attempts", 0)) + 1), sha))
        except Exception:
            return True

    def _update(self, key: str, **fields) -> None:
        path = claim_path(key)
        now = self._now()
        for _ in range(3):
            cur, sha = self.store.get(path)
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
        self._update(key, status="failed", note=note[:200])

    def purge(self, older_than_hours: int = 48) -> int:
        cutoff = self._now() - timedelta(hours=older_than_hours)
        count = 0
        for path in self.store.list_paths("claims/"):
            try:
                cur, sha = self.store.get(path)
                at = _p(cur.get("claimed_at")) if cur else None
                if at is not None and at < cutoff:
                    self.store.delete(path, sha)
                    count += 1
            except Exception:
                continue
        return count


def send_with_claim(claims: ClaimStore, tg, key: str, text: str) -> bool:
    if not claims.claim(key):
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
