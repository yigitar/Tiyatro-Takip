from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest


class FakeStore:
    """In-memory stand-in for the GitHub-Contents-API Store.

    Store interface (implemented for real in a later task):
      create_file(path, data) -> bool        False if the file already exists
      get(path) -> (dict | None, sha | None) content and sha
      put(path, data, sha) -> bool           False on sha conflict
      delete(path, sha) -> None
      server_now() -> datetime               tz-aware, from the server Date header
      update_json(path, mutate, retries=4) -> dict
      list_paths(prefix) -> list[str]        paths starting with prefix (used by purge)
    Test hooks: ``fail`` makes every call raise RuntimeError; ``now`` is the server clock.
    The sha changes on every write (optimistic concurrency).
    """

    def __init__(self, now: datetime | None = None):
        self.files: dict[str, tuple[dict, str]] = {}
        self.now = now or datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
        self.fail = False
        self._n = 0

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("store down")

    def _sha(self) -> str:
        self._n += 1
        return f"sha{self._n}"

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def server_now(self) -> datetime:
        self._check()
        return self.now

    def create_file(self, path, data) -> bool:
        self._check()
        if path in self.files:
            return False
        self.files[path] = (copy.deepcopy(data), self._sha())
        return True

    def get(self, path):
        self._check()
        if path not in self.files:
            return None, None
        data, sha = self.files[path]
        return copy.deepcopy(data), sha

    def put(self, path, data, sha) -> bool:
        self._check()
        if path not in self.files or self.files[path][1] != sha:
            return False
        self.files[path] = (copy.deepcopy(data), self._sha())
        return True

    def delete(self, path, sha) -> None:
        self._check()
        if path in self.files and self.files[path][1] == sha:
            del self.files[path]

    def update_json(self, path, mutate, retries=4) -> dict:
        self._check()
        for _ in range(retries):
            data, sha = self.get(path)
            new = mutate(copy.deepcopy(data) if data is not None else {})
            ok = self.create_file(path, new) if sha is None else self.put(path, new, sha)
            if ok:
                return new
        raise RuntimeError("update_json: too many conflicts")

    def list_paths(self, prefix) -> list[str]:
        self._check()
        return sorted(p for p in self.files if p.startswith(prefix))


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()
