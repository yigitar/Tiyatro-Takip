from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml
from nacl.public import PrivateKey, SealedBox

from tests.conftest import FakeStore
from tiyatro.config import config_to_dict, default_config
from tiyatro.runner import BURST_MAX_SECONDS
from tiyatro.state import empty_state

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- init branch
class EnsureStore(FakeStore):
    ensured = 0

    def ensure_branch(self):
        self.ensured += 1


def test_init_creates_both_files_then_is_idempotent():
    init = _load("init_data_branch")
    st = EnsureStore()
    out = io.StringIO()
    with redirect_stdout(out):
        assert init.main(["--repo", "o/r"], store=st) == 0
    assert st.ensured == 1
    assert st.files["config.json"][0] == config_to_dict(default_config())
    assert st.files["state.json"][0] == empty_state()
    shas = {p: s for p, (_, s) in st.files.items()}
    with redirect_stdout(out):
        assert init.main(["--repo", "o/r"], store=st) == 0
    assert {p: s for p, (_, s) in st.files.items()} == shas


def test_init_never_overwrites_existing_config():
    init = _load("init_data_branch")
    st = EnsureStore()
    st.create_file("config.json", {"custom": True})
    with redirect_stdout(io.StringIO()):
        init.main(["--repo", "o/r"], store=st)
    assert st.files["config.json"][0] == {"custom": True}
    assert "state.json" in st.files


def test_init_requires_token_without_store(monkeypatch):
    init = _load("init_data_branch")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with redirect_stderr(io.StringIO()):
        assert init.main(["--repo", "o/r"]) == 2


# ---------------------------------------------------------------- set_secrets
BOT = "123456:ABC-very-secret-bot-token"
CHAT = "-1009876543210"


KEYS = ["--bot-token-key", "SRC_BOT_TOKEN", "--chat-id-key", "SRC_CHAT_ID"]


def _env_file(tmp_path):
    p = tmp_path / "tokens.env"
    p.write_bytes(("﻿# comment\r\n"
                   f"SRC_BOT_TOKEN={BOT}\r\n"
                   f"SRC_CHAT_ID={CHAT}\r\n"
                   "GITHUB_TOKEN=ghp_fileFallbackToken\r\n").encode("utf-8"))
    return p


class FakeGitHub:
    def __init__(self, key: PrivateKey, put_status=201):
        self.key = key
        self.put_status = put_status
        self.calls = []
        self.puts = {}

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers))
        path = url.split("api.github.com", 1)[1]
        if method == "GET" and path.endswith("/actions/secrets/public-key"):
            pk = base64.b64encode(bytes(self.key.public_key)).decode()
            return 200, {}, json.dumps({"key_id": "kid1", "key": pk}).encode()
        if method == "GET" and path.endswith("/actions/secrets"):
            return 200, {}, json.dumps({"secrets": [{"name": "A"}, {"name": "B"}]}).encode()
        if method == "PUT" and "/actions/secrets/" in path:
            self.puts[path.rsplit("/", 1)[1]] = json.loads(body)
            return self.put_status, {}, b""
        return 404, {}, b"{}"


def test_set_secrets_round_trip_and_no_leak(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    key = PrivateKey.generate()
    gh = FakeGitHub(key)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert ss.main(["--repo", "o/r", "--env-file", str(_env_file(tmp_path)), *KEYS],
                       transport=gh) == 0
    assert set(gh.puts) == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
    box = SealedBox(key)
    for name, plain in (("TELEGRAM_BOT_TOKEN", BOT), ("TELEGRAM_CHAT_ID", CHAT)):
        body = gh.puts[name]
        assert body["key_id"] == "kid1"
        assert box.decrypt(base64.b64decode(body["encrypted_value"])).decode() == plain
        assert plain not in body["encrypted_value"]
    text = out.getvalue() + err.getvalue()
    for secret in (BOT, CHAT, "ghp_envToken"):
        assert secret not in text
    assert "TELEGRAM_BOT_TOKEN" in text and "201" in text
    assert all(h["Authorization"] == "Bearer ghp_envToken" for _, _, h in gh.calls)


def test_set_secrets_github_token_falls_back_to_file(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    gh = FakeGitHub(PrivateKey.generate())
    with redirect_stdout(io.StringIO()):
        assert ss.main(["--repo", "o/r", "--env-file", str(_env_file(tmp_path)), *KEYS],
                       transport=gh) == 0
    assert gh.calls[0][2]["Authorization"] == "Bearer ghp_fileFallbackToken"


def test_set_secrets_failure_does_not_leak(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    gh = FakeGitHub(PrivateKey.generate(), put_status=403)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ss.main(["--repo", "o/r", "--env-file", str(_env_file(tmp_path)), *KEYS],
                     transport=gh)
    assert rc == 1
    text = out.getvalue() + err.getvalue()
    assert BOT not in text and CHAT not in text and "403" in text
    assert "FAILED TELEGRAM_BOT_TOKEN (403)" in text and "check token scope" in text


def test_set_secrets_transport_exception_does_not_leak(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")

    def boom(method, url, headers, body):
        raise RuntimeError(f"{headers['Authorization']} {body}")

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ss.main(["--repo", "o/r", "--env-file", str(_env_file(tmp_path)), *KEYS],
                     transport=boom)
    assert rc == 1
    text = out.getvalue() + err.getvalue()
    assert "ghp_envToken" not in text and BOT not in text and CHAT not in text


def test_set_secrets_missing_value_reports_name_only(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    p = tmp_path / "t.env"
    p.write_text("SRC_BOT_TOKEN=x\n")
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        assert ss.main(["--repo", "o/r", "--env-file", str(p), *KEYS],
                       transport=FakeGitHub(PrivateKey.generate())) == 2
    assert "SRC_CHAT_ID" in err.getvalue()


def test_set_secrets_default_key_names(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    p = tmp_path / "d.env"
    p.write_text(f"TELEGRAM_BOT_TOKEN={BOT}\nTELEGRAM_CHAT_ID={CHAT}\n")
    gh = FakeGitHub(PrivateKey.generate())
    with redirect_stdout(io.StringIO()):
        assert ss.main(["--repo", "o/r", "--env-file", str(p)], transport=gh) == 0
    assert set(gh.puts) == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}


def test_set_secrets_needs_env_file_option_or_var(monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    monkeypatch.delenv("TOKENS_ENV_FILE", raising=False)
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        assert ss.main(["--repo", "o/r"], transport=FakeGitHub(PrivateKey.generate())) == 2
    assert "--env-file" in err.getvalue() and ":/" not in err.getvalue()


def test_set_secrets_env_file_from_variable(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    monkeypatch.setenv("TOKENS_ENV_FILE", str(_env_file(tmp_path)))
    gh = FakeGitHub(PrivateKey.generate())
    with redirect_stdout(io.StringIO()):
        assert ss.main(["--repo", "o/r", *KEYS], transport=gh) == 0
    assert len(gh.puts) == 2


def test_set_secrets_only_selected_keys_uploaded(tmp_path, monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    key = PrivateKey.generate()
    gh = FakeGitHub(key)
    with redirect_stdout(io.StringIO()):
        assert ss.main(["--repo", "o/r", "--env-file", str(_env_file(tmp_path)), *KEYS],
                       transport=gh) == 0
    box = SealedBox(key)
    got = {box.decrypt(base64.b64decode(b["encrypted_value"])).decode()
           for b in gh.puts.values()}
    assert got == {BOT, CHAT}  # the GITHUB_TOKEN line of the file is never uploaded


def test_set_secrets_list(monkeypatch):
    ss = _load("set_secrets")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_envToken")
    gh = FakeGitHub(PrivateKey.generate())
    out = io.StringIO()
    with redirect_stdout(out):
        assert ss.main(["--repo", "o/r", "--list"], transport=gh) == 0
    assert out.getvalue().split() == ["A", "B"]
    assert not gh.puts


# ---------------------------------------------------------------- workflows
WF = ROOT / ".github" / "workflows"


def _wf(name):
    doc = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))  # YAML 1.1 parses `on` as True
    return doc


def test_scan_workflow():
    wf = _wf("scan.yml")
    assert wf["name"] == "scan"
    assert wf["on"]["schedule"] == [{"cron": "*/5 * * * *"}]
    assert "workflow_dispatch" in wf["on"]
    assert wf["concurrency"] == {"group": "scan", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "write", "actions": "read"}
    job = next(iter(wf["jobs"].values()))
    assert len(wf["jobs"]) == 1 and job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] * 60 >= BURST_MAX_SECONDS + 300
    assert job["timeout-minutes"] == 25 and BURST_MAX_SECONDS == 960  # final review I4
    runs = [s.get("run") or "" for s in job["steps"]]
    # stdlib-only runtime: no pip step (Ubuntu ships the tz database)
    assert not any("pip install" in r for r in runs)
    step = next(s for s in job["steps"] if s.get("run") == "python -m tiyatro.runner")
    assert step["env"] == {
        "TELEGRAM_BOT_TOKEN": "${{ secrets.TELEGRAM_BOT_TOKEN }}",
        "TELEGRAM_CHAT_ID": "${{ secrets.TELEGRAM_CHAT_ID }}",
        "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
        "GITHUB_REPOSITORY": "${{ github.repository }}",
        "GITHUB_RUN_ID": "${{ github.run_id }}",
    }
    py = next(s for s in job["steps"]
              if str(s.get("uses", "")).startswith("actions/setup-python"))
    assert py["with"] == {"python-version": "3.12"}  # no pip cache without a pip step


def test_runtime_is_stdlib_only_and_tzdata_only_on_windows():
    import ast
    import sys as _sys
    req = [ln.strip() for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8")
           .splitlines() if ln.strip() and not ln.startswith("#")]
    assert req == ['tzdata; sys_platform == "win32"']
    for f in sorted((ROOT / "tiyatro").glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom)
                     and node.level == 0 and node.module else [])
            for name in names:
                top = name.split(".")[0]
                assert top == "tiyatro" or top in _sys.stdlib_module_names, (f.name, name)


def test_keepalive_workflow():
    wf = _wf("keepalive.yml")
    assert wf["name"] == "keepalive"
    (cron,) = wf["on"]["schedule"]
    parts = cron["cron"].split()
    assert len(parts) == 5 and parts[4] != "*" and parts[2] == "*" and parts[3] == "*"
    assert "workflow_dispatch" in wf["on"]
    assert wf["permissions"] == {"actions": "write"}
    steps = next(iter(wf["jobs"].values()))["steps"]
    assert len(steps) == 1
    assert "actions/workflows/scan.yml/enable" in steps[0]["run"]
    assert "-X PUT" in steps[0]["run"]
    assert "X-GitHub-Api-Version: 2022-11-28" in steps[0]["run"]


def test_net_test_removed():
    assert not (WF / "net-test.yml").exists()


def test_workflows_pinned_and_free_of_secret_literals():
    files = sorted(WF.glob("*.yml"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        for uses in re.findall(r"uses:\s*(\S+)", text):
            assert re.search(r"@v\d+(\.\d+)*$", uses), uses
        assert not re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}", text)
        assert "ghp_" not in text and "github_pat_" not in text
