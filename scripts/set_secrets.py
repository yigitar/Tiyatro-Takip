"""Write the Telegram secrets to the repo's GitHub Actions secrets.

Values come from a KEY=VALUE env file (--env-file or env TOKENS_ENV_FILE; source key
names via --bot-token-key / --chat-id-key), are encrypted with the repo public key
(libsodium sealed box, PyNaCl) and PUT to the API as TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID. Only secret NAMES and HTTP statuses are ever printed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tiyatro.store import API, default_transport  # noqa: E402

TARGETS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def parse_env(path: str) -> dict:
    values = {}
    text = Path(path).read_bytes().decode("utf-8-sig")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


class _Api:
    def __init__(self, token, transport):
        self._token, self._transport = token, transport

    def call(self, method, path, payload=None):
        headers = {"Authorization": f"Bearer {self._token}",
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "tiyatro-takip"}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        failure = None
        try:
            status, _, data = self._transport(method, f"{API}{path}", headers, body)
        except Exception as exc:  # message dropped: it could echo headers or body
            failure = type(exc).__name__
        if failure is not None:
            raise RuntimeError(f"{method} {path} failed: {failure}")
        return status, data


def main(argv=None, transport=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--env-file", default=os.environ.get("TOKENS_ENV_FILE", ""),
                    help="KEY=VALUE file (default: env TOKENS_ENV_FILE)")
    ap.add_argument("--bot-token-key", default="TELEGRAM_BOT_TOKEN",
                    help="key in the env file holding the bot token")
    ap.add_argument("--chat-id-key", default="TELEGRAM_CHAT_ID",
                    help="key in the env file holding the chat id")
    ap.add_argument("--list", action="store_true", help="print existing secret names")
    args = ap.parse_args(argv)

    file_values = {}
    if args.env_file:
        if not os.path.exists(args.env_file):
            print("env file not found", file=sys.stderr)
            return 2
        file_values = parse_env(args.env_file)
    elif not args.list:
        print("no env file: pass --env-file or set TOKENS_ENV_FILE", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN") or file_values.get("GITHUB_TOKEN", "")
    if not token:
        print("no GITHUB_TOKEN (env or env file)", file=sys.stderr)
        return 2
    api = _Api(token, transport or default_transport)
    base = f"/repos/{args.repo}/actions/secrets"
    try:
        if args.list:
            status, data = api.call("GET", base)
            if status != 200:
                print(f"list failed: HTTP {status} (check token scope / repo name)",
                      file=sys.stderr)
                return 1
            for item in json.loads(data.decode("utf-8")).get("secrets", []):
                print(item["name"])
            return 0

        sources = ((args.bot_token_key, TARGETS[0]), (args.chat_id_key, TARGETS[1]))
        missing = [src for src, _ in sources if not file_values.get(src)]
        if missing:
            print(f"missing in env file: {', '.join(missing)}", file=sys.stderr)
            return 2
        try:
            from nacl.public import PublicKey, SealedBox
        except ImportError:
            print("PyNaCl required (pip install -r requirements-dev.txt)", file=sys.stderr)
            return 2

        status, data = api.call("GET", f"{base}/public-key")
        if status != 200:
            print(f"public key fetch failed: HTTP {status} "
                  "(check token scope / repo name)", file=sys.stderr)
            return 1
        info = json.loads(data.decode("utf-8"))
        box = SealedBox(PublicKey(base64.b64decode(info["key"])))
        rc = 0
        for src, name in sources:
            enc = base64.b64encode(box.encrypt(file_values[src].encode("utf-8"))).decode()
            status, _ = api.call("PUT", f"{base}/{name}",
                                 {"encrypted_value": enc, "key_id": info["key_id"]})
            if status in (201, 204):
                print(f"{name}: HTTP {status} ok")
            else:
                print(f"FAILED {name} ({status}) - check token scope / repo name")
                rc = 1
        return rc
    except Exception as exc:
        print(f"set_secrets failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
