"""Wave 4 B: state["sources"] bookkeeping, status icons, runner integration."""
from __future__ import annotations

from datetime import timedelta

from tests.test_runner import NOW, Env, mkplay, sess
from tiyatro import sources as S
from tiyatro.fetcher import FetchError
from tiyatro.models import Status
from tiyatro.runner import _fingerprint, _prepare


def test_record_check_change_time_only_when_content_changes():
    st = {}
    t0 = NOW
    r = S.record_check(st, "biletinial", t0, True, items=[["a", 1]])
    assert r["last_check_at"] == t0.isoformat() and r["last_change_at"] == t0.isoformat()
    assert r["ok"] is True and r["items_count"] == 1 and r["error"] is None
    t1 = t0 + timedelta(minutes=5)
    r = S.record_check(st, "biletinial", t1, True, items=[["a", 1]])
    assert r["last_check_at"] == t1.isoformat() and r["last_change_at"] == t0.isoformat()
    t2 = t1 + timedelta(minutes=5)
    r = S.record_check(st, "biletinial", t2, True, items=[["a", 1], ["b", 2]])
    assert r["last_change_at"] == t2.isoformat() and r["items_count"] == 2
    t3 = t2 + timedelta(minutes=5)
    r = S.record_check(st, "biletinial", t3, False, error="FetchError\nboom" + "x" * 200)
    assert r["ok"] is False and r["last_ok_at"] == t2.isoformat()
    assert r["last_change_at"] == t2.isoformat() and r["items_count"] == 2
    assert len(r["error"]) <= S.ERROR_MAX and "\n" not in r["error"]
    # item order does not matter for dict content
    assert S.items_hash({"a": 1, "b": 2}) == S.items_hash({"b": 2, "a": 1})


def test_status_ok_stale_failed_unknown():
    st = {}
    S.record_check(st, "vkv", NOW, True, items=[])
    rec = st["sources"]["vkv"]
    assert S.status_of(rec, NOW + timedelta(minutes=10)) == "ok"
    assert S.status_of(rec, NOW + timedelta(minutes=31)) == "stale"
    S.record_check(st, "vkv", NOW, False, error="Timeout")
    assert S.status_of(st["sources"]["vkv"], NOW) == "failed"
    assert S.status_of({}, NOW) == "unknown" and S.status_of(None, NOW) == "unknown"
    assert S.status_of({"last_check_at": "garbage"}, NOW) == "unknown"


def test_describe_and_line_formatting():
    st = {}
    S.record_check(st, "biletinial", NOW, True, items=[1, 2, 3])
    S.record_check(st, "vkv", NOW, False, error="Timeout")
    views = S.describe(st, NOW + timedelta(minutes=1))
    assert [v["id"] for v in views] == ["biletinial", "vkv"]
    assert views[0]["icon"] == "✅" and views[1]["icon"] == "❌"
    line = S.line(views[0])
    assert "Biletinial" in line and "Son kontrol 09:00" in line
    assert "Son güncelleme 09:00" in line and "3 öğe" in line
    assert "hata: Timeout" in S.line(views[1])
    assert S.describe({}, NOW) == [] and S.describe({"sources": "x"}, NOW) == []


def test_runner_records_biletinial_ok_and_failure(monkeypatch):
    env = Env(monkeypatch, [mkplay("a"), mkplay("b")])
    env.run(NOW)
    rec = env.saved_state()["sources"]["biletinial"]
    assert rec["ok"] is True and rec["items_count"] == 2 and rec["error"] is None
    first_change = rec["last_change_at"]
    env.run(NOW + timedelta(minutes=5))
    rec = env.saved_state()["sources"]["biletinial"]
    assert rec["last_check_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert rec["last_change_at"] == first_change  # same content
    env.web.set(mkplay("a", sessions=[sess(status=Status.SOLD_OUT)]))
    env.run(NOW + timedelta(minutes=10))
    rec = env.saved_state()["sources"]["biletinial"]
    assert rec["last_change_at"] == (NOW + timedelta(minutes=10)).isoformat()
    env.web.venue_error = FetchError("HTTP 503: x")
    env.run(NOW + timedelta(minutes=15))
    rec = env.saved_state()["sources"]["biletinial"]
    assert rec["ok"] is False and rec["error"] == "FetchError"
    assert rec["last_ok_at"] == (NOW + timedelta(minutes=10)).isoformat()
    assert rec["items_count"] == 2


def test_check_times_do_not_change_the_fingerprint():
    st = _prepare({"plays": {}, "meta": {}, "emitted": {}})
    S.record_check(st, "biletinial", NOW, True, items=[1])
    fp = _fingerprint(st)
    S.record_check(st, "biletinial", NOW + timedelta(minutes=5), True, items=[1])
    assert _fingerprint(st) == fp
    S.record_check(st, "biletinial", NOW + timedelta(minutes=10), False, error="Timeout")
    assert _fingerprint(st) != fp


def test_prepare_drops_garbled_sources():
    st = _prepare({"plays": {}, "sources": {"a": 1, "vkv": {"ok": True}}, "meta": {}})
    assert list(st["sources"]) == ["vkv"]
    assert _prepare({"plays": {}, "sources": "x", "meta": {}})["sources"] == {}


# ---- PC service + strip ------------------------------------------------------------------

def test_service_exposes_source_status_and_report(monkeypatch, tmp_path):
    from tests.test_pc_service import make, tick_at, NOW as PNOW
    e = make(monkeypatch, tmp_path, plays=[mkplay("p")])
    assert e.svc.sources() == []
    tick_at(e, PNOW)
    [v] = e.svc.sources()
    assert v["id"] == "biletinial" and v["icon"] == "✅" and v["items_count"] == 1
    e.clock.set(PNOW + timedelta(minutes=1))
    e.svc.refresh_now()
    assert e.svc.sources_report() == "✅ Biletinial"
    e.web.venue_error = FetchError("HTTP 403: blocked")
    e.clock.set(PNOW + timedelta(minutes=2))
    import pytest
    from pc.service import RefreshError
    with pytest.raises(RefreshError):
        e.svc.refresh_now()
    assert e.svc.sources_report() == "❌ Biletinial"
    assert e.svc.sources()[0]["status"] == "failed"


import pytest  # noqa: E402

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import tk_root, ui  # noqa: E402,F401  (fixtures)


def _fake_sources(status="ok"):
    st = {}
    S.record_check(st, "biletinial", NOW, status == "ok", items=[1], error=None if status == "ok" else "Timeout")
    return S.describe(st, NOW)


def test_sources_strip_shows_icons_times_and_legend(ui):
    ui.svc.sources = lambda now=None: _fake_sources()
    ui.refresh_rows()
    assert "✅ Biletinial" in ui.source_labels[0].cget("text")
    assert "Son kontrol" in ui.source_labels[0].cget("text")
    first = ui.source_labels[0]
    ui.refresh_rows()
    assert ui.source_labels[0] is first  # not rebuilt unless the text changes
    ui.svc.sources = lambda now=None: _fake_sources("failed")
    ui.refresh_rows()
    assert ui.source_labels[0].cget("text").startswith("❌")
    ui.svc.sources = lambda now=None: []
    ui.refresh_rows()
    assert ui.source_labels == [] and ui.source_bar.winfo_manager() == ""


def test_sources_strip_survives_service_errors(ui):
    def boom(now=None):
        raise RuntimeError("x")
    ui.svc.sources = boom
    ui.refresh_rows()


def test_refresh_message_names_each_source(ui):
    ui.svc.sources_report = lambda now=None: "✅ Biletinial · ❌ VKV Ford Otosan Gölcük"
    ui.refresh_list()
    ui.pump()
    assert "Kaynaklar: ✅ Biletinial · ❌ VKV Ford Otosan Gölcük" in ui.msg_var.get()
