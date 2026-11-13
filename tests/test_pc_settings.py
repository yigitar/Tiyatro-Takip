from __future__ import annotations

import json

import pytest

from pc.settings import Settings, SettingsError, load_settings, read_env_file

SECRET_GH = "ghp_secretvalue123"
SECRET_TG = "123456:tg-secret-token"
SECRET_CHAT = "987654321"


def _env_file(tmp_path, text):
    p = tmp_path / "secrets.env"
    p.write_text(text, encoding="utf-8")
    return p


def _full_file(tmp_path, gh_key="TIYATRO_GH_TOKEN"):
    return _env_file(tmp_path, f"# yorum\n\n{gh_key}={SECRET_GH}\n"
                               f"export TELEGRAM_BOT_TOKEN=\"{SECRET_TG}\"\n"
                               f"TELEGRAM_CHAT_ID='{SECRET_CHAT}'\n")


def test_read_env_file_parses_comments_export_and_quotes(tmp_path):
    p = _full_file(tmp_path)
    assert read_env_file(p) == {"TIYATRO_GH_TOKEN": SECRET_GH,
                                "TELEGRAM_BOT_TOKEN": SECRET_TG,
                                "TELEGRAM_CHAT_ID": SECRET_CHAT}


def test_env_vars_only(tmp_path):
    p = _full_file(tmp_path)
    s = load_settings(environ={"TOKENS_ENV_FILE": str(p), "GITHUB_REPOSITORY": "o/r"},
                      local_dir=tmp_path / "local")
    assert (s.repo, s.gh_token, s.tg_token, s.chat_id) == ("o/r", SECRET_GH, SECRET_TG,
                                                           SECRET_CHAT)


def test_settings_json_fills_gaps_and_env_wins(tmp_path):
    p = _full_file(tmp_path, gh_key="OTHER_GH")
    local = tmp_path / "local"
    local.mkdir()
    (local / "settings.json").write_text(json.dumps(
        {"env_file": str(p), "repo": "file/repo", "gh_token_key": "OTHER_GH"}),
        encoding="utf-8")
    s = load_settings(environ={}, local_dir=local)
    assert s.repo == "file/repo" and s.gh_token == SECRET_GH
    s2 = load_settings(environ={"GITHUB_REPOSITORY": "env/repo"}, local_dir=local)
    assert s2.repo == "env/repo"  # env first, then settings.json


def test_secret_falls_back_to_process_env_without_file(tmp_path):
    s = load_settings(environ={"GITHUB_REPOSITORY": "o/r", "TIYATRO_GH_TOKEN": SECRET_GH,
                               "TELEGRAM_BOT_TOKEN": SECRET_TG,
                               "TELEGRAM_CHAT_ID": SECRET_CHAT},
                      local_dir=tmp_path)
    assert s.gh_token == SECRET_GH


def test_missing_repo_names_key(tmp_path):
    p = _full_file(tmp_path)
    with pytest.raises(SettingsError, match="repo"):
        load_settings(environ={"TOKENS_ENV_FILE": str(p)}, local_dir=tmp_path)


def test_missing_or_empty_secret_names_key_never_value(tmp_path):
    p = _env_file(tmp_path, f"TIYATRO_GH_TOKEN=\nTELEGRAM_BOT_TOKEN={SECRET_TG}\n"
                            f"TELEGRAM_CHAT_ID={SECRET_CHAT}\n")
    with pytest.raises(SettingsError) as info:
        load_settings(environ={"TOKENS_ENV_FILE": str(p), "GITHUB_REPOSITORY": "o/r"},
                      local_dir=tmp_path)
    msg = str(info.value)
    assert "TIYATRO_GH_TOKEN" in msg
    assert SECRET_TG not in msg and SECRET_CHAT not in msg


def test_missing_env_file_is_a_settings_error(tmp_path):
    with pytest.raises(SettingsError, match="env_file"):
        load_settings(environ={"TOKENS_ENV_FILE": str(tmp_path / "yok.env"),
                               "GITHUB_REPOSITORY": "o/r"}, local_dir=tmp_path)


def test_bad_settings_json_is_a_settings_error(tmp_path):
    (tmp_path / "settings.json").write_text("{bozuk", encoding="utf-8")
    with pytest.raises(SettingsError, match="settings.json"):
        load_settings(environ={}, local_dir=tmp_path)


def test_repr_and_str_never_reveal_secrets(tmp_path):
    p = _full_file(tmp_path)
    s = load_settings(environ={"TOKENS_ENV_FILE": str(p), "GITHUB_REPOSITORY": "o/r"},
                      local_dir=tmp_path)
    for text in (repr(s), str(s), f"{s}"):
        assert SECRET_GH not in text and SECRET_TG not in text and SECRET_CHAT not in text
        assert "o/r" in text
    assert isinstance(s, Settings)
