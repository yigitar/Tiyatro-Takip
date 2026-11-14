"""Activity-log entries written by pc.service.Service (fake clock/store, no network)."""
from __future__ import annotations

import copy
import re
from datetime import timedelta

import pytest

from pc.activity import ActivityLog
from pc.service import Service
from tests.test_pc_service import (NOW, RunsStore, _flip_on_sale, _opening_env, make, tick_at)
from tests.test_runner import WATCH_CFG, FakeTG, mkplay, sess
from tiyatro.claims import ClaimStore
from tiyatro.models import Status


def entries(e, cat=None):
    """Oldest first."""
    got = list(reversed(e.svc.activity.load()))
    return [x for x in got if cat is None or x["cat"] == cat]


def titles(e, cat=None):
    return [x["title"] for x in entries(e, cat)]


# ---- scans ------------------------------------------------------------------------

def test_first_scan_entry_has_counts_and_no_change(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    [scan] = entries(e, "Liste kontrolü")
    assert scan["ts"] == NOW.isoformat(timespec="seconds")
    assert scan["title"] == "1 oyun, 1 seans (0 satışta, 1 yakında, 0 tükendi)"
    assert scan["detail"].splitlines()[0] == "değişiklik yok"
    assert "Normal mod" in scan["detail"] and "PC" in scan["detail"]
    assert scan["level"] == "info"


def test_normal_scans_each_get_one_entry_and_delivery_ticks_none(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    tick_at(e, NOW + timedelta(seconds=60))  # not due: no scan
    tick_at(e, NOW + timedelta(seconds=300))
    tick_at(e, NOW + timedelta(seconds=600))
    assert len(entries(e, "Liste kontrolü")) == 3


def test_status_counts_are_summed_over_sessions(monkeypatch, tmp_path):
    plays = [mkplay("p", sessions=[sess("a", Status.ON_SALE), sess("b"),
                                   sess("c", Status.SOLD_OUT), sess("d", Status.SOLD_OUT)]),
             mkplay("q", sessions=[sess("e", Status.ON_SALE)])]
    e = make(monkeypatch, tmp_path, plays=plays, config={"plays": {}})
    tick_at(e, NOW)
    [scan] = entries(e, "Liste kontrolü")
    assert scan["title"] == "2 oyun, 5 seans (2 satışta, 1 yakında, 2 tükendi)"


def test_a_change_makes_the_scan_say_so_and_logs_the_event(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    _flip_on_sale(e, t)
    scans = entries(e, "Liste kontrolü")
    assert scans[-1]["detail"].splitlines()[0].startswith("değişiklik var")
    [opening] = entries(e, "Bilet açılışı")
    assert "Bilet alınabilir" in opening["title"] and "Oyun p" in opening["title"]
    assert opening["ts"] == t.isoformat(timespec="seconds")


def test_new_play_goes_to_the_play_list_category(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path, config={"plays": {"p": {"active": False}}})
    tick_at(e, NOW)
    e.web.venue.append("r")
    e.web.set(mkplay("r"))
    tick_at(e, NOW + timedelta(minutes=5))
    got = entries(e, "Oyun listesi")
    assert any("Yeni oyun" in x["title"] and "Oyun r" in x["title"] for x in got)
    assert entries(e, "Liste kontrolü")[-1]["detail"].startswith("değişiklik var")


def test_new_session_of_a_known_play_is_logged(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path, config={"plays": {"p": {"active": False}}})
    tick_at(e, NOW)
    e.web.set(mkplay("p", sessions=[sess("s1"), sess("s2")]))
    tick_at(e, NOW + timedelta(minutes=5))
    [x] = [x for x in entries(e, "Oyun listesi") if "seans" in x["title"].lower()]
    assert "Oyun p" in x["title"] and "1 yeni seans" in x["title"]


# ---- burst coalescing ----------------------------------------------------------------

def burst_env(monkeypatch, tmp_path):
    return _opening_env(monkeypatch, tmp_path, NOW + timedelta(minutes=5))


def test_burst_ticks_are_coalesced_into_one_rolling_entry_per_5_minutes(monkeypatch, tmp_path):
    e = burst_env(monkeypatch, tmp_path)
    ticks = 0
    for i in range(0, 21):  # 0 s .. 300 s, every 15 s
        r = tick_at(e, NOW + timedelta(seconds=15 * i))
        assert r.scanned and r.mode == "burst"
        ticks += 1
    scans = entries(e, "Liste kontrolü")
    assert len(scans) == 1
    m = re.fullmatch(r"Hızlı mod: (\d+) kontrol, değişiklik yok", scans[0]["title"])
    assert m and int(m.group(1)) == ticks
    assert scans[0]["ts"] == (NOW + timedelta(minutes=5)).isoformat(timespec="seconds")


def test_burst_change_is_logged_immediately_after_flushing_the_rollup(monkeypatch, tmp_path):
    e = burst_env(monkeypatch, tmp_path)
    for i in range(0, 5):
        tick_at(e, NOW + timedelta(seconds=15 * i))
    assert entries(e, "Liste kontrolü") == []  # nothing yet: still collecting
    e.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    tick_at(e, NOW + timedelta(seconds=75))
    scans = entries(e, "Liste kontrolü")
    assert re.fullmatch(r"Hızlı mod: 5 kontrol, değişiklik yok", scans[0]["title"])
    assert scans[1]["detail"].startswith("değişiklik var")
    assert len(entries(e, "Bilet açılışı")) == 1


def test_leaving_burst_flushes_the_rollup(monkeypatch, tmp_path):
    e = _opening_env(monkeypatch, tmp_path, NOW + timedelta(minutes=5))
    for i in range(0, 3):
        tick_at(e, NOW + timedelta(seconds=15 * i))
    e.svc.log_shutdown()
    t = titles(e)
    assert any(x.startswith("Hızlı mod: 3 kontrol") for x in t)
    assert t[-1].startswith("Uygulama kapatıldı")


# ---- config changes ------------------------------------------------------------------

def test_remote_config_change_is_summarised_as_remote(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    cfg, _sha = e.store.files["config.json"]
    cfg = copy.deepcopy(cfg)
    cfg["plays"]["p"]["targets"].append({"id": "c", "name": "Çocuk", "people": 3, "rule": {}})
    e.store.files["config.json"] = (cfg, "worker")
    tick_at(e, NOW + timedelta(seconds=30))
    [x] = entries(e, "Takip")
    assert x["title"].startswith("Uzaktan (Telegram) değişiklik")
    assert "Çocuk hedefi eklendi (3 kişi)" in x["title"]
    assert "Oyun p" in x["title"]
    tick_at(e, NOW + timedelta(seconds=60))  # nothing changed since: no repeat
    assert len(entries(e, "Takip")) == 1


def test_remote_pause_and_unreadable_first_load_do_not_log_noise(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    assert entries(e, "Takip") == []  # the first config load is not a "change"
    cfg = copy.deepcopy(e.store.files["config.json"][0])
    cfg["paused"] = True
    e.store.files["config.json"] = (cfg, "w")
    tick_at(e, NOW + timedelta(seconds=30))
    assert "Tüm bildirimler duraklatıldı" in titles(e, "Takip")[0]


def test_pc_edit_is_logged_as_pc_and_not_again_as_remote(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.svc.set_people("p", "t1", 3)
    [x] = entries(e, "Takip")
    assert x["title"].startswith("PC'den")
    assert "kişi sayısı 2 → 3" in x["title"] and "Oyun p" in x["title"]
    tick_at(e, NOW + timedelta(seconds=30))  # reload sees our own change
    assert len(entries(e, "Takip")) == 1


def test_noop_edit_logs_nothing(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.svc.set_people("p", "t1", 2)
    assert entries(e, "Takip") == []


def test_each_edit_helper_reports_what_it_did(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.svc.set_active("p", False)
    e.svc.set_active("p", True)
    e.svc.ensure_target("p", "c")
    e.svc.set_days_group("p", "t1", "wd")
    e.svc.set_hours("p", "t1", "19:00", "22:00")
    e.svc.set_excluded_date("p", "t1", "2026-12-01", True)
    e.svc.remove_target("p", "c")
    e.svc.pause()
    e.svc.resume()
    got = titles(e, "Takip")
    assert any("takibi duraklatıldı" in t for t in got)
    assert any("takibi açıldı" in t for t in got)
    assert any("Çocuk hedefi eklendi" in t for t in got)
    assert any("günler Cmt, Paz → Pzt, Sal, Çar, Per, Cum" in t for t in got)
    assert any("saat 19:00–22:00" in t for t in got)
    assert any("hariç tutulan tarih eklendi: 01.12" in t for t in got)
    assert any("Çocuk hedefi silindi" in t for t in got)
    assert any("duraklatıldı" in t and "Tüm" in t for t in got)
    assert any("yeniden açıldı" in t for t in got)
    assert all(x["title"].startswith("PC'den") for x in entries(e, "Takip"))


def test_failed_edit_is_logged_as_a_warning_and_still_raises(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    with pytest.raises(ValueError):
        e.svc.set_people("p", "t1", 9)
    [x] = entries(e, "Hata/Uyarı")
    assert x["level"] == "warn" and "Ayar kaydedilemedi" in x["title"]
    assert entries(e, "Takip") == []


# ---- notifications --------------------------------------------------------------------

def test_pc_delivery_is_logged_as_sent(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    _flip_on_sale(e, t)
    assert entries(e, "Bildirim") == []  # queued, not yet sent (15 s grace)
    tick_at(e, t + timedelta(seconds=16))
    [x] = entries(e, "Bildirim")
    assert "Telegram'a gönderildi" in x["title"] and "Bilet alınabilir" in x["title"]
    assert x["ts"] == (t + timedelta(seconds=16)).isoformat(timespec="seconds")


def test_alert_sent_first_by_github_is_logged_as_claimed(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    _flip_on_sale(e, t)
    e.gh_run(t + timedelta(seconds=20))
    tick_at(e, t + timedelta(seconds=70))
    [x] = entries(e, "Bildirim")
    assert "GitHub" in x["title"] and "PC göndermedi" in x["title"]
    assert e.tg.sent == []


def test_failed_send_is_logged_as_retry(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    _flip_on_sale(e, t)
    e.tg.fail = True
    tick_at(e, t + timedelta(seconds=16))
    [x] = entries(e, "Bildirim")
    assert x["level"] == "warn" and "Gönderilemedi" in x["title"] and "tekrar" in x["title"]


# ---- errors / backoff -----------------------------------------------------------------

def test_startup_failure_logs_error_with_backoff_and_dedupes(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    e.store.fail = True
    tick_at(e, NOW)
    errs = entries(e, "Hata/Uyarı")
    assert errs and errs[0]["level"] in ("warn", "error")
    assert any("config.json okunamadı" in x["title"] for x in errs)
    assert any("30 sn" in x["title"] + x["detail"] for x in errs)
    n = len(errs)
    tick_at(e, NOW + timedelta(seconds=30))  # same failure again, soon: not repeated
    assert len(entries(e, "Hata/Uyarı")) == n
    tick_at(e, NOW + timedelta(minutes=11))  # long after: reported again
    assert len(entries(e, "Hata/Uyarı")) > n


def test_scan_failure_is_an_error_entry(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.web.venue = []  # venue lists nothing -> failed scan
    tick_at(e, NOW + timedelta(minutes=5))
    last = entries(e, "Liste kontrolü")[-1]
    assert last["level"] == "warn" and "okunamadı" in last["detail"].splitlines()[0]


def test_fetch_failing_event_is_an_error_entry(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.web.venue = []
    for i in range(1, 5):
        tick_at(e, NOW + timedelta(minutes=5 * i))
    assert any("Biletinial taraması başarısız" in x["title"]
               for x in entries(e, "Hata/Uyarı"))


# ---- GitHub status ------------------------------------------------------------------------

def run_entry(minutes_ago_end):
    return {"id": 1, "status": "completed", "conclusion": "success", "name": "scan",
            "path": ".github/workflows/scan.yml",
            "created_at": (NOW - timedelta(minutes=minutes_ago_end + 1)).isoformat(),
            "updated_at": (NOW - timedelta(minutes=minutes_ago_end)).isoformat()}


def test_github_status_logged_at_startup_and_on_transitions_only(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path, runs=[run_entry(2)])
    tick_at(e, NOW)
    [first] = entries(e, "GitHub")
    assert first["level"] == "info" and "2 dk önce" in first["title"]
    tick_at(e, NOW + timedelta(minutes=5))  # still healthy: silent
    assert len(entries(e, "GitHub")) == 1
    tick_at(e, NOW + timedelta(minutes=20))  # last run now > 10 min old
    bad = entries(e, "GitHub")[-1]
    assert bad["level"] == "warn" and "gecik" in bad["title"]
    tick_at(e, NOW + timedelta(minutes=26))  # still unhealthy: silent
    assert len(entries(e, "GitHub")) == 2
    e.store.runs_list = [run_entry(0)]
    e.clock.set(NOW + timedelta(minutes=40))
    e.store.runs_list = [{**run_entry(0), "created_at": (NOW + timedelta(minutes=39)).isoformat(),
                          "updated_at": (NOW + timedelta(minutes=40)).isoformat()}]
    e.svc.tick()
    assert "düzeldi" in entries(e, "GitHub")[-1]["title"]


def test_github_read_failure_is_a_warning_once(monkeypatch, tmp_path):
    class BadRuns(RunsStore):
        def runs(self, per_page=10):
            raise RuntimeError("502")
    e = make(monkeypatch, tmp_path, store=BadRuns())
    tick_at(e, NOW)
    tick_at(e, NOW + timedelta(minutes=6))
    warn = [x for x in entries(e, "Hata/Uyarı") if "GitHub çalışmaları" in x["title"]]
    assert len(warn) == 1


# ---- app lifecycle / refresh -------------------------------------------------------------

def test_startup_and_shutdown_lines(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    e.svc.log_startup()
    e.svc.log_shutdown()
    assert titles(e, "Uygulama") == ["Uygulama başlatıldı", "Uygulama kapatıldı"]


def test_manual_refresh_is_logged_with_new_and_removed_plays(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path, plays=[mkplay("a"), mkplay("b")])
    e.svc.refresh_now()
    e.web.venue = ["b", "c"]
    e.web.set(mkplay("c"))
    e.svc.refresh_now()
    last = entries(e, "Liste kontrolü")[-1]
    assert last["detail"].splitlines()[0].startswith("Elle güncelleme")
    assert "2 oyun" in last["title"]
    plays = " ".join(titles(e, "Oyun listesi"))
    assert "Oyun c" in plays and "Oyun a" in plays


def test_logging_failure_never_breaks_the_scan(monkeypatch, tmp_path):
    (tmp_path / "activity.jsonl").mkdir()  # the log file cannot be written
    e = make(monkeypatch, tmp_path)
    r = tick_at(e, NOW)
    assert r.scanned and r.errors == []
    assert e.svc.refresh_now()


def test_injected_activity_log_is_used(monkeypatch, tmp_path):
    store = RunsStore()
    log = ActivityLog(tmp_path / "elsewhere.jsonl", clock=lambda: NOW)
    from tests.test_runner import Web
    store.files["config.json"] = (copy.deepcopy(WATCH_CFG), "c")
    Web(monkeypatch, [mkplay()])
    svc = Service(store, FakeTG(), ClaimStore(store, "pc", lambda: NOW), clock=lambda: NOW,
                  local_dir=tmp_path, activity=log)
    assert svc.activity is log
    svc.log_startup()
    assert log.load()[0]["title"] == "Uygulama başlatıldı"
