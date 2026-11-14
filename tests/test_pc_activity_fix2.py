"""Fix round 2: config recovery, add() never raises, error wording scope."""
from __future__ import annotations

import pathlib
from datetime import timedelta

import pytest

from pc import activity as A
from tests.test_pc_activity import make
from tests.test_pc_service import NOW, make as make_env, tick_at
from tests.test_pc_service_activity import entries


def test_config_coming_back_after_an_outage_is_not_a_diff(monkeypatch, tmp_path):
    e = make_env(monkeypatch, tmp_path)
    tick_at(e, NOW)
    saved = e.store.files.pop("config.json")
    tick_at(e, NOW + timedelta(seconds=30))  # outage: fallback to the default config
    e.store.files["config.json"] = saved
    tick_at(e, NOW + timedelta(seconds=60))  # recovery
    assert entries(e, "Takip") == []
    # a later real remote change is reported again
    import copy
    cfg = copy.deepcopy(saved[0])
    cfg["paused"] = True
    e.store.files["config.json"] = (cfg, "w")
    tick_at(e, NOW + timedelta(seconds=90))
    assert len(entries(e, "Takip")) == 1


def test_add_never_raises_on_an_unreadable_but_appendable_file(tmp_path, monkeypatch):
    log = make(tmp_path)
    log.add("Takip", "a")
    log._count = None  # forces a re-read on the next add
    real = pathlib.Path.read_text

    def read_text(self, *a, **k):
        if self.name == "activity.jsonl":
            raise PermissionError("locked")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    assert log.add("Takip", "b") is not None  # appended, no TypeError
    monkeypatch.undo()
    assert [e["title"] for e in log.load()] == ["b", "a"]


def test_add_swallows_unexpected_errors(tmp_path, monkeypatch):
    log = make(tmp_path)
    monkeypatch.setattr(log, "_read_text", lambda: 1 / 0)
    assert log.add("Takip", "x") is None


def test_unmapped_error_titles_never_carry_windows_paths():
    title, _ = A.friendly_error(
        "yerel durum yazılamadı: ValueError: bad 'C:\\Users\\Ali\\proj\\pc\\state.json' x")
    assert "Ali" not in title and "C:\\" not in title and "state.json" in title
    title, _ = A.friendly_error("kayıt C:\\Users\\Ali\\a.txt açılamadı")
    assert "Ali" not in title and "a.txt" in title


@pytest.mark.parametrize("text, friendly", [
    ("tarama başarısız: FetchError: HTTP 403: https://biletinial.com/x",
     "siteye erişilemedi (HTTP 403)"),
    ("tarama başarısız: FetchError: HTTP 401: https://biletinial.com/x",
     "siteye erişilemedi (HTTP 401)"),
    ("tarama başarısız: FetchError: HTTP 429: https://biletinial.com/x",
     "site çok fazla istek aldı, bekleniyor (HTTP 429)"),
    ("config.json okunamadı: StoreError: GET x: HTTP 403 forbidden",
     "GitHub erişimi reddedildi (anahtarı kontrol edin)"),
    ("GitHub çalışmaları okunamadı: RuntimeError: HTTP 401",
     "GitHub erişimi reddedildi (anahtarı kontrol edin)"),
])
def test_http_wording_depends_on_the_source(text, friendly):
    title, _ = A.friendly_error(text)
    assert title.endswith(friendly)
