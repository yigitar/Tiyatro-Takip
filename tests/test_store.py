from __future__ import annotations

import base64
import json
import urllib.parse
from datetime import datetime, timezone

import pytest

from tiyatro.fetcher import FetchError, FetchResult, fetch
from tiyatro.models import ISTANBUL
from tiyatro.store import Store, StoreError

TOKEN = "ghp_SECRETTOKEN123"
DATE = "Fri, 13 Nov 2026 09:00:00 GMT"


class FakeGH:
    """Minimal in-memory GitHub Contents/refs API."""

    def __init__(self, branches=("main",)):
        self.files = {}  # path -> (bytes, sha)
        self.branches = {b: "headsha" for b in branches}
        self.n = 0
        self.calls = []
        self.before_put = None  # hook(path) run before a PUT is evaluated
        self.always_conflict = False
        self.status_override = None

    def _sha(self):
        self.n += 1
        return f"s{self.n}"

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        hdr = {"date": DATE}
        if self.status_override:
            return self.status_override, hdr, b'{"message":"boom"}'
        u = urllib.parse.urlparse(url)
        path = u.path
        if "/git/ref/heads/" in path and method == "GET":
            name = path.split("/git/ref/heads/")[1]
            if name in self.branches:
                return 200, hdr, json.dumps({"object": {"sha": self.branches[name]}}).encode()
            return 404, hdr, b'{"message":"Not Found"}'
        if path.endswith("/git/refs") and method == "POST":
            req = json.loads(body)
            self.branches[req["ref"].split("/")[-1]] = req["sha"]
            return 201, hdr, b"{}"
        if "/contents/" in path:
            fpath = path.split("/contents/", 1)[1]
            if method == "GET":
                if fpath in self.files:
                    data, sha = self.files[fpath]
                    return 200, hdr, json.dumps({
                        "sha": sha, "content": base64.b64encode(data).decode()}).encode()
                prefix = fpath.rstrip("/") + "/"
                kids = [p for p in self.files if p.startswith(prefix)]
                if kids:
                    return 200, hdr, json.dumps(
                        [{"path": p, "type": "file"} for p in sorted(kids)]).encode()
                return 404, hdr, b'{"message":"Not Found"}'
            if method == "PUT":
                if self.before_put:
                    self.before_put(fpath)
                req = json.loads(body)
                cur = self.files.get(fpath)
                if self.always_conflict:
                    return 409, hdr, b'{"message":"conflict"}'
                if cur is None and "sha" in req:
                    return 422, hdr, b'{"message":"Invalid request"}'
                if cur is not None and "sha" not in req:
                    msg = json.dumps({"message": "Invalid request. \"sha\" wasn't supplied."})
                    return 422, hdr, msg.encode()
                if cur is not None and cur[1] != req["sha"]:
                    return 409, hdr, b'{"message":"is at x but expected y"}'
                self.files[fpath] = (base64.b64decode(req["content"]), self._sha())
                return 200, hdr, b"{}"
            if method == "DELETE":
                req = json.loads(body)
                cur = self.files.get(fpath)
                if cur is None or cur[1] != req["sha"]:
                    return 409, hdr, b"{}"
                del self.files[fpath]
                return 200, hdr, b"{}"
        if path.startswith("/repos/") and path.count("/") == 3 and method == "GET":
            return 200, hdr, json.dumps({"default_branch": "main"}).encode()
        return 404, hdr, b"{}"


def mk(gh=None):
    gh = gh or FakeGH()
    return Store("owner/repo", TOKEN, transport=gh), gh


def test_get_missing_returns_none():
    s, _ = mk()
    assert s.get("x.json") == (None, None)


def test_create_get_roundtrip_and_headers():
    s, gh = mk()
    assert s.create_file("a.json", {"k": "ğüş", "a": 1}) is True
    data, sha = s.get("a.json")
    assert data == {"k": "ğüş", "a": 1} and sha
    raw = gh.files["a.json"][0].decode("utf-8")
    assert "ğüş" in raw and raw.index('"a"') < raw.index('"k"')
    h = gh.calls[0][2]
    assert h["Authorization"] == f"Bearer {TOKEN}"
    assert h["Accept"] == "application/vnd.github+json"
    assert h["X-GitHub-Api-Version"] == "2022-11-28"
    assert h.get("User-Agent")
    put = [c for c in gh.calls if c[0] == "PUT"][0]
    req = json.loads(put[3])
    assert req["branch"] == "data" and req["message"].isascii()


def test_create_file_existing_returns_false():
    s, _ = mk()
    assert s.create_file("a.json", {}) is True
    assert s.create_file("a.json", {"x": 1}) is False


def test_put_conflict_false_and_success():
    s, gh = mk()
    s.create_file("a.json", {"v": 1})
    _, sha = s.get("a.json")
    assert s.put("a.json", {"v": 2}, sha) is True
    assert s.put("a.json", {"v": 3}, sha) is False
    assert s.get("a.json")[0] == {"v": 2}


def test_put_other_error_raises():
    s, gh = mk()
    gh.status_override = 500
    with pytest.raises(StoreError):
        s.put("a.json", {}, "sha")


def test_delete():
    s, gh = mk()
    s.create_file("a.json", {})
    _, sha = s.get("a.json")
    s.delete("a.json", sha)
    assert "a.json" not in gh.files


def test_list_paths():
    s, _ = mk()
    s.create_file("claims/a.json", {})
    s.create_file("claims/b.json", {})
    s.create_file("state.json", {})
    assert s.list_paths("claims/") == ["claims/a.json", "claims/b.json"]
    assert s.list_paths("nothing/") == []


def test_update_json_missing_file_starts_empty():
    s, _ = mk()
    out = s.update_json("s.json", lambda d: {**d, "a": 1})
    assert out == {"a": 1} and s.get("s.json")[0] == {"a": 1}


def test_update_json_conflict_retry_preserves_both_writers():
    s, gh = mk()
    s.create_file("s.json", {"base": 0})
    other, _ = mk(gh)
    fired = []

    def interfere(path):
        if not fired:
            fired.append(1)
            other.update_json(path, lambda d: {**d, "theirs": 1})

    gh.before_put = interfere
    applied = []

    def mutate(d):
        applied.append(dict(d))
        return {**d, "mine": 1}

    out = s.update_json("s.json", mutate)
    assert out == {"base": 0, "theirs": 1, "mine": 1}
    assert s.get("s.json")[0] == out
    assert len(applied) == 2 and "theirs" in applied[1]


def test_update_json_retries_exhausted():
    s, gh = mk()
    s.create_file("s.json", {})
    gh.always_conflict = True
    n = []
    with pytest.raises(StoreError):
        s.update_json("s.json", lambda d: (n.append(1), d)[1], retries=3)
    assert len(n) == 3


def test_server_now_from_date_header():
    s, _ = mk()
    s.get("x.json")
    now = s.server_now()
    assert now.tzinfo is not None
    assert now == datetime(2026, 11, 13, 9, 0, 0, tzinfo=timezone.utc)
    assert now.utcoffset() == ISTANBUL.utcoffset(now)


def test_server_now_makes_request_when_none_seen():
    s, gh = mk()
    assert not gh.calls
    s.server_now()
    assert len(gh.calls) == 1 and gh.calls[0][0] == "GET"


def test_ensure_branch_exists_noop():
    gh = FakeGH(branches=("main", "data"))
    s, _ = mk(gh)
    s.ensure_branch()
    assert not [c for c in gh.calls if c[0] == "POST"]


def test_ensure_branch_creates_from_default():
    s, gh = mk()
    s.ensure_branch()
    assert gh.branches["data"] == "headsha"
    post = [c for c in gh.calls if c[0] == "POST"][0]
    assert json.loads(post[3]) == {"ref": "refs/heads/data", "sha": "headsha"}


def _assert_no_token(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        assert TOKEN not in str(exc) and TOKEN not in repr(exc)
        exc = exc.__cause__ or exc.__context__


def test_token_absent_from_errors():
    s, gh = mk()
    gh.status_override = 500
    for call in (lambda: s.get("a.json"), lambda: s.put("a.json", {}, "x"),
                 lambda: s.create_file("a.json", {}), lambda: s.list_paths("c/")):
        with pytest.raises(StoreError) as ei:
            call()
        _assert_no_token(ei.value)


def test_token_absent_when_transport_raises():
    def boom(method, url, headers, body):
        raise OSError(f"connect failed {headers['Authorization']}")

    s = Store("owner/repo", TOKEN, transport=boom)
    with pytest.raises(StoreError) as ei:
        s.get("a.json")
    _assert_no_token(ei.value)


def test_default_transport_error_has_no_token(monkeypatch):
    import urllib.request

    def boom(req, timeout=None):
        raise OSError(f"fail {req.get_header('Authorization')}")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    s = Store("owner/repo", TOKEN)
    with pytest.raises(StoreError) as ei:
        s.get("a.json")
    _assert_no_token(ei.value)


# ---- fetcher ----

def test_fetch_ok_and_headers():
    seen = {}

    def tr(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return 200, "<html>Merhaba ğ</html>".encode("utf-8")

    r = fetch("https://x.test/a", transport=tr)
    assert isinstance(r, FetchResult) and r.status == 200
    assert "Merhaba ğ" in r.html and r.latency_ms >= 0
    assert "Chrome/131" in seen["headers"]["User-Agent"]
    assert seen["headers"]["Accept-Language"] == "tr-TR,tr;q=0.9"
    assert seen["timeout"] == 30


def test_fetch_decodes_with_replacement():
    r = fetch("u", transport=lambda u, h, t: (200, b"ok \xff"))
    assert r.html.startswith("ok ")


@pytest.mark.parametrize("status", [404, 500])
def test_fetch_http_error(status):
    with pytest.raises(FetchError):
        fetch("u", transport=lambda u, h, t: (status, b""))


def test_fetch_timeout():
    def tr(u, h, t):
        raise TimeoutError("slow")

    with pytest.raises(FetchError):
        fetch("u", transport=tr)
