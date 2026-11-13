"""PC app settings: where the secrets are and which repository to use.

Lookup order for every setting: environment variable, then the optional, git-ignored
``pc/local/settings.json``, then the default. Nothing machine specific is hardcoded here.

=================  ======================  ==================  =====================
setting            environment variable    settings.json key   default
=================  ======================  ==================  =====================
secrets file       TOKENS_ENV_FILE         env_file            (none: process env)
repository         GITHUB_REPOSITORY       repo                (required)
GitHub token key   TIYATRO_GH_TOKEN_KEY    gh_token_key        TIYATRO_GH_TOKEN
bot token key      TIYATRO_TG_TOKEN_KEY    tg_token_key        TELEGRAM_BOT_TOKEN
chat id key        TIYATRO_CHAT_ID_KEY     chat_id_key         TELEGRAM_CHAT_ID
=================  ======================  ==================  =====================

The ``*_key`` settings are the KEY NAMES looked up in the secrets file (``KEY=VALUE``
lines); without a secrets file the process environment is used. Errors name the missing
key, never a value.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

DEFAULT_LOCAL_DIR = Path(__file__).resolve().parent / "local"

_FIELDS = (  # (attribute, env var, settings.json key, default)
    ("env_file", "TOKENS_ENV_FILE", "env_file", None),
    ("repo", "GITHUB_REPOSITORY", "repo", None),
    ("gh_token_key", "TIYATRO_GH_TOKEN_KEY", "gh_token_key", "TIYATRO_GH_TOKEN"),
    ("tg_token_key", "TIYATRO_TG_TOKEN_KEY", "tg_token_key", "TELEGRAM_BOT_TOKEN"),
    ("chat_id_key", "TIYATRO_CHAT_ID_KEY", "chat_id_key", "TELEGRAM_CHAT_ID"),
)


class SettingsError(RuntimeError):
    """Missing/invalid setting. The message names the key, never a secret value."""


@dataclass(frozen=True)
class Settings:
    repo: str
    gh_token: str = field(repr=False)
    tg_token: str = field(repr=False)
    chat_id: str = field(repr=False)
    env_file: str | None = None
    local_dir: Path = DEFAULT_LOCAL_DIR

    def __repr__(self) -> str:
        return (f"Settings(repo={self.repo!r}, gh_token='***', tg_token='***', "
                f"chat_id='***', env_file={self.env_file!r}, local_dir={str(self.local_dir)!r})")

    __str__ = __repr__


def read_env_file(path) -> dict[str, str]:
    """``KEY=VALUE`` lines; blank lines, ``#`` comments and an ``export`` prefix are
    ignored, one pair of surrounding quotes is stripped."""
    out: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _local_json(local_dir: Path) -> dict:
    path = local_dir / "settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise SettingsError(f"{path.name} okunamadı (geçerli JSON değil)") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path.name} bir JSON nesnesi olmalı")
    return data


def load_settings(environ: Mapping[str, str] | None = None,
                  local_dir: Path | str | None = None) -> Settings:
    env = os.environ if environ is None else environ
    local = Path(local_dir) if local_dir is not None else DEFAULT_LOCAL_DIR
    file_cfg = _local_json(local)

    values: dict[str, str | None] = {}
    for attr, env_key, json_key, default in _FIELDS:
        value = env.get(env_key) or file_cfg.get(json_key) or default
        values[attr] = str(value).strip() if value else None

    if not values["repo"]:
        raise SettingsError("repo ayarı eksik (GITHUB_REPOSITORY ya da settings.json 'repo')")

    secrets: Mapping[str, str] = env
    if values["env_file"]:
        path = Path(values["env_file"])
        if not path.is_file():
            raise SettingsError("env_file bulunamadı (TOKENS_ENV_FILE / settings.json "
                                "'env_file')")
        try:
            secrets = {**env, **{k: v for k, v in read_env_file(path).items() if v}}
        except (OSError, UnicodeDecodeError):
            raise SettingsError("env_file okunamadı") from None

    found = {}
    for attr in ("gh_token_key", "tg_token_key", "chat_id_key"):
        key = values[attr]
        value = (secrets.get(key) or "").strip()
        if not value:
            raise SettingsError(f"{key} bulunamadı veya boş (gizli anahtar dosyası)")
        found[attr] = value
    return Settings(repo=values["repo"], gh_token=found["gh_token_key"],
                    tg_token=found["tg_token_key"], chat_id=found["chat_id_key"],
                    env_file=values["env_file"], local_dir=local)
