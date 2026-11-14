"""Fix round 1 for pc/activity.py: read failures, retention, scrub, error wording."""
from __future__ import annotations

import pathlib

import pytest

from pc import activity as A
from tests.test_pc_activity import Clock, make


def _fail_reads(monkeypatch):
    real = pathlib.Path.read_text

    def read_text(self, *a, **k):
        if self.name == "activity.jsonl":
            raise PermissionError("locked")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)


def test_unreadable_file_at_the_cap_is_never_wiped(tmp_path, monkeypatch):
    clock = Clock()
    log = make(tmp_path, clock=clock, max_entries=10)
    for n in range(11):
        clock.tick(seconds=1)
        log.add("Takip", f"olay {n}")
    before = (tmp_path / "activity.jsonl").read_text(encoding="utf-8")
    _fail_reads(monkeypatch)
    log.add("Takip", "x")  # crosses the trim threshold: the rewrite must be skipped
    monkeypatch.undo()
    after = (tmp_path / "activity.jsonl").read_text(encoding="utf-8")
    assert after.startswith(before) and "olay 0" in after


def test_unreadable_load_is_flagged_and_recovers(tmp_path, monkeypatch):
    log = make(tmp_path)
    log.add("Takip", "a")
    _fail_reads(monkeypatch)
    assert log.load() == [] and log.read_error is True
    monkeypatch.undo()
    assert [e["title"] for e in log.load()] == ["a"] and log.read_error is False


def test_missing_file_is_empty_not_an_error(tmp_path):
    log = make(tmp_path)
    assert log.load() == [] and log.read_error is False


def test_retention_drops_quiet_scans_before_important_lines(tmp_path):
    clock = Clock()
    log = make(tmp_path, clock=clock, max_entries=10)
    for n in range(4):
        clock.tick(seconds=1)
        log.add("Bilet açılışı" if n % 2 else "Takip", f"önemli {n}")
    for n in range(40):
        clock.tick(seconds=1)
        log.add("Liste kontrolü", f"tarama {n}", "değişiklik yok\nPC")
    titles = [e["title"] for e in log.load()]
    assert len(titles) == 10
    assert {f"önemli {n}" for n in range(4)} <= set(titles)
    assert titles[0] == "tarama 39"


def test_basic_auth_value_is_scrubbed(tmp_path):
    log = make(tmp_path)
    log.add("Hata/Uyarı", "x", "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==")
    assert "dXNlcjpwYXNz" not in (tmp_path / "activity.jsonl").read_text(encoding="utf-8")


def test_cut_adds_an_ellipsis():
    assert A.cut("a" * 400, 300).endswith("…") and len(A.cut("a" * 400, 300)) == 300
    assert A.cut("kısa", 300) == "kısa"


def test_public_time_helpers():
    assert A.format_ts("2026-11-14T14:35:10+03:00") == "14.11.2026 14:35:10"
    assert A.format_ts("bozuk") == "bozuk"


@pytest.mark.parametrize("text, friendly", [
    ("yerel durum yazılamadı: PermissionError: [Errno 13] denied",
     "dosya yazılamadı (erişim reddedildi)"),
    ("GitHub çalışmaları okunamadı: URLError: <urlopen error timed out>",
     "internet bağlantısı kurulamadı"),
    ("tarama başarısız: TimeoutError: timed out", "internet bağlantısı kurulamadı"),
    ("tarama başarısız: ConnectionResetError: boom", "internet bağlantısı kurulamadı"),
    ("tarama başarısız: ParseError: no plays",
     "Biletinial sayfası okunamadı (sayfa yapısı değişmiş olabilir)"),
    ("config.json okunamadı: RuntimeError: HTTP 401: Bad credentials",
     "GitHub erişimi reddedildi (anahtarı kontrol edin)"),
    ("config.json okunamadı: RuntimeError: HTTP 403 rate limit",
     "GitHub erişimi reddedildi (anahtarı kontrol edin)"),
    ("tarama başarısız: FetchError: HTTP 404: https://x", "adres bulunamadı (HTTP 404)"),
    ("tarama başarısız: FetchError: HTTP 503", "sunucu geçici olarak yanıt vermiyor"),
])
def test_friendly_error_mapping(text, friendly):
    title, detail = A.friendly_error(text)
    assert title == f"{text.split(': ')[0]}: {friendly}"
    assert text.split(": ", 1)[1] in detail  # the technical text is kept in the detail


def test_friendly_error_shortens_windows_paths():
    title, detail = A.friendly_error(
        "yerel durum yazılamadı: PermissionError: [Errno 13] 'C:\\Users\\Ali\\x\\pc\\state.json'")
    assert "Ali" not in detail and "C:\\" not in detail
    assert "PermissionError" in detail and "state.json" in detail


def test_unknown_error_is_left_as_is():
    assert A.friendly_error("bir şey oldu") == ("bir şey oldu", "")
    assert A.friendly_error("x: ValueError: kötü değer") == ("x: ValueError: kötü değer", "")
