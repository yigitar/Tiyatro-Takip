"""Fix round 1 for the service's activity logging: it must never disturb the alert path."""
from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from pc import activity as A
from tests.test_pc_service import NOW, _flip_on_sale, make, tick_at
from tests.test_pc_service_activity import entries, titles

JUNK_OUTBOX = [
    {"key": "K1", "attempts": "x", "events": [], "not_before": "bozuk"},
    {"key": "K2", "attempts": None},
    {"key": "K3", "attempts": [1], "events": "?"},
]


def test_malformed_outbox_attempts_do_not_break_ticks(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.svc._state["outbox"].extend(copy.deepcopy(JUNK_OUTBOX))
    r = tick_at(e, NOW + timedelta(minutes=5))
    assert r.scanned and r.errors == []


def test_alert_still_sent_with_junk_in_the_outbox(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    _flip_on_sale(e, t)
    e.svc._state["outbox"].append({"key": "J", "attempts": "x"})
    r = tick_at(e, t + timedelta(seconds=16))
    assert r.errors == [] and len(e.tg.sent) == 1


def test_any_logging_failure_is_swallowed(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("bug in logging")
    for name in ("snapshot", "counts_text", "diff_snapshots", "describe_config_diff",
                 "plain_headline", "change_summary", "friendly_error"):
        monkeypatch.setattr(A, name, boom)
    r = tick_at(e, NOW)
    assert r.scanned and r.errors == []
    e.svc.set_people("p", "t1", 3)
    assert e.svc.refresh_now()
    e.svc.reload_config()
    e.store.fail = True
    tick_at(e, NOW + timedelta(minutes=5))  # error path logging fails too: still no raise


def test_edit_reload_race_is_not_logged_as_remote(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    real_add = e.svc.activity.add
    fired = []

    def add(*a, **k):
        if not fired:
            fired.append(1)
            e.svc.reload_config()  # a reload landing while the edit is being logged
        return real_add(*a, **k)
    monkeypatch.setattr(e.svc.activity, "add", add)
    e.svc.set_people("p", "t1", 3)
    got = titles(e, "Takip")
    assert len(got) == 1 and got[0].startswith("PC'den")


def test_missing_config_file_logs_one_error_not_a_removal_diff(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    del e.store.files["config.json"]
    tick_at(e, NOW + timedelta(seconds=30))
    assert entries(e, "Takip") == []
    errs = [x for x in entries(e, "Hata/Uyarı") if "config.json" in x["title"]]
    assert len(errs) == 1 and "okunamadı" in errs[0]["title"]
    tick_at(e, NOW + timedelta(seconds=60))
    assert len([x for x in entries(e, "Hata/Uyarı") if "config.json" in x["title"]]) == 1


def test_service_errors_are_plain_turkish_with_technical_detail(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    import urllib.error

    def down(path):
        raise urllib.error.URLError("timed out")
    e.store.get = down
    tick_at(e, NOW)
    x = [y for y in entries(e, "Hata/Uyarı") if "config.json" in y["title"]][0]
    assert x["title"] == "config.json okunamadı: internet bağlantısı kurulamadı"
    assert "URLError" in x["detail"] and "timed out" in x["detail"]


def test_takip_result_column_does_not_repeat_the_title(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.svc.set_people("p", "t1", 3)
    [x] = entries(e, "Takip")
    assert A.result_text(x) and A.result_text(x) not in x["title"]
    assert "kişi sayısı 2 → 3" in x["detail"]


def test_long_titles_are_cut_with_an_ellipsis(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    e.svc._log("Takip", "u" * 500)
    t = titles(e, "Takip")[0]
    assert t.endswith("…") and len(t) == 300
