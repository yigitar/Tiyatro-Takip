"""GitHub Contents API backed JSON store (files live on the `data` branch)."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Callable

from tiyatro.models import ISTANBUL

API = "https://api.github.com"
TIMEOUT = 30


class StoreError(RuntimeError):
    """Store failure. Messages never contain the access token."""


def default_transport(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    failure = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return (resp.status, {k.lower(): v for k, v in resp.headers.items()},
                    resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return (exc.code, {k.lower(): v for k, v in exc.headers.items()},
                    exc.read())
        except Exception:
            failure = f"HTTP {exc.code} (body unreadable)"
    except Exception as exc:
        failure = type(exc).__name__
    # raised outside the except blocks so no __context__ carries request details
    raise StoreError(f"{method} request failed: {failure}")


class Store:
    def __init__(self, repo: str, token: str, branch: str = "data",
                 transport: Callable | None = None):
        self.repo = repo
        self.branch = branch
        self._token = token
        self._transport = transport or default_transport
        self._last_date: str | None = None

    # -- plumbing --------------------------------------------------------
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "tiyatro-takip"}

    def _request(self, method: str, path: str, payload: dict | None = None):
        url = f"{API}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        failure = None
        try:
            status, headers, data = self._transport(method, url, self._headers(), body)
        except StoreError:
            raise
        except Exception as exc:
            failure = type(exc).__name__
        if failure is not None:
            raise StoreError(f"{method} {path} failed: {failure}")
        date = {k.lower(): v for k, v in headers.items()}.get("date")
        if date:
            self._last_date = date
        return status, data

    def _contents(self, path: str) -> str:
        return f"/repos/{self.repo}/contents/{urllib.parse.quote(path, safe='/')}"

    @staticmethod
    def _json(data: bytes):
        try:
            return json.loads(data.decode("utf-8")) if data else {}
        except ValueError:
            return {}

    def _fail(self, what: str, status: int, data: bytes):
        msg = self._json(data)
        text = msg.get("message", "") if isinstance(msg, dict) else ""
        raise StoreError(f"{what}: HTTP {status} {text[:120]}".strip())

    def _write(self, path: str, data: dict, sha: str | None):
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1)
        payload = {"message": f"data: update {path}".encode("ascii", "replace").decode(),
                   "content": base64.b64encode(raw.encode("utf-8")).decode("ascii"),
                   "branch": self.branch}
        if sha is not None:
            payload["sha"] = sha
        return self._request("PUT", self._contents(path), payload)

    # -- interface ---------------------------------------------------------
    def get(self, path: str):
        status, data = self._request(
            "GET", f"{self._contents(path)}?ref={urllib.parse.quote(self.branch)}")
        if status == 404:
            return None, None
        if status != 200:
            self._fail(f"get {path}", status, data)
        info = self._json(data)
        try:
            content = base64.b64decode(info["content"]).decode("utf-8")
            return json.loads(content), info["sha"]
        except (KeyError, ValueError, TypeError):
            raise StoreError(f"get {path}: unreadable content") from None

    def put(self, path: str, data: dict, sha: str) -> bool:
        status, body = self._write(path, data, sha)
        if status in (200, 201):
            return True
        if status in (409, 422):
            return False
        self._fail(f"put {path}", status, body)

    def create_file(self, path: str, data: dict) -> bool:
        status, body = self._write(path, data, None)
        if status in (200, 201):
            return True
        if status == 409:
            return False
        if status == 422 and ("sha" in json.dumps(self._json(body)).lower()
                              or "exist" in json.dumps(self._json(body)).lower()):
            return False
        self._fail(f"create {path}", status, body)

    def delete(self, path: str, sha: str) -> None:
        payload = {"message": f"data: delete {path}".encode("ascii", "replace").decode(),
                   "sha": sha, "branch": self.branch}
        status, body = self._request("DELETE", self._contents(path), payload)
        if status not in (200, 204, 404, 409):
            self._fail(f"delete {path}", status, body)

    def list_paths(self, prefix: str) -> list[str]:
        directory = prefix.rpartition("/")[0]
        status, data = self._request(
            "GET", f"{self._contents(directory)}?ref={urllib.parse.quote(self.branch)}")
        if status == 404:
            return []
        if status != 200:
            self._fail(f"list {prefix}", status, data)
        items = self._json(data)
        if not isinstance(items, list):
            return []
        return sorted(i["path"] for i in items
                      if i.get("type") == "file" and i["path"].startswith(prefix))

    def server_now(self) -> datetime:
        if self._last_date is None:
            status, data = self._request("GET", f"/repos/{self.repo}")
            if self._last_date is None:
                self._fail("server_now", status, data)
        try:
            return parsedate_to_datetime(self._last_date).astimezone(ISTANBUL)
        except (TypeError, ValueError):
            raise StoreError("server_now: bad Date header") from None

    def update_json(self, path: str, mutate: Callable[[dict], dict],
                    retries: int = 4) -> dict:
        for _ in range(retries):
            data, sha = self.get(path)
            new = mutate(data if data is not None else {})
            if not isinstance(new, dict):
                raise StoreError(f"update_json {path}: mutate returned "
                                 f"{type(new).__name__}, not dict")
            ok = self.create_file(path, new) if sha is None else self.put(path, new, sha)
            if ok:
                return new
        raise StoreError(f"update_json {path}: too many conflicts")

    def runs(self, per_page: int = 10) -> list[dict]:
        """Latest workflow runs of the repository (newest first)."""
        status, data = self._request(
            "GET", f"/repos/{self.repo}/actions/runs?per_page={int(per_page)}")
        if status != 200:
            self._fail("runs", status, data)
        info = self._json(data)
        runs = info.get("workflow_runs") if isinstance(info, dict) else None
        if not isinstance(runs, list):
            raise StoreError("runs: unreadable response")
        return runs

    def ensure_branch(self) -> None:
        ref = f"/repos/{self.repo}/git/ref/heads/{urllib.parse.quote(self.branch, safe='')}"
        status, data = self._request("GET", ref)
        if status == 200:
            return
        if status != 404:
            self._fail("ensure_branch", status, data)
        status, data = self._request("GET", f"/repos/{self.repo}")
        if status != 200:
            self._fail("ensure_branch repo", status, data)
        default = self._json(data).get("default_branch", "main")
        status, data = self._request(
            "GET", f"/repos/{self.repo}/git/ref/heads/{urllib.parse.quote(default, safe='')}")
        if status != 200:
            self._fail("ensure_branch default ref", status, data)
        sha = self._json(data)["object"]["sha"]
        status, data = self._request(
            "POST", f"/repos/{self.repo}/git/refs",
            {"ref": f"refs/heads/{self.branch}", "sha": sha})
        if status not in (200, 201, 422):  # 422: created concurrently
            self._fail("ensure_branch create", status, data)
