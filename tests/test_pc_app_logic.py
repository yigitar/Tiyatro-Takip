from __future__ import annotations

import copy
import json
import socket
from datetime import datetime, timedelta

import pytest

from pc import app_logic as L
from tiyatro.config import config_from_dict
from tiyatro.models import ISTANBUL, Event, Rule, Target

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=ISTANBUL)


# ---- time validation ----------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("", None), ("   ", None), (None, None), ("18:30", "18:30"), ("9:05", "09:05"),
    (" 20:00 ", "20:00"), ("18", "18:00"), ("0:00", "00:00"), ("23:59", "23:59"),
    ("18.30", "18:30"),
])
def test_parse_time_ok(raw, want):
    assert L.parse_time(raw) == want


@pytest.mark.parametrize("raw", ["24:00", "12:60", "abc", "12:5", "-1", "1830", "12:30:00"])
def test_parse_time_rejects(raw):
    with pytest.raises(ValueError) as e:
        L.parse_time(raw, "başlangıç")
    assert "başlangıç" in str(e.value)


# ---- days ---------------------------------------------------------------------------

def test_day_flag_roundtrip_and_all_days_is_none():
    assert L.days_to_flags(None) == [True] * 7
    assert L.days_to_flags([0, 6]) == [True, False, False, False, False, False, True]
    assert L.flags_to_days([True] * 7) is None
    assert L.flags_to_days(L.days_to_flags([5, 6])) == [5, 6]
    assert L.flags_to_days([False] * 7) == []


def test_quick_day_sets():
    assert L.QUICK_DAYS["Hafta içi"] == [0, 1, 2, 3, 4]
    assert L.QUICK_DAYS["Hafta sonu"] == [5, 6]
    assert L.QUICK_DAYS["Hepsi"] == [0, 1, 2, 3, 4, 5, 6]


# ---- target summary -----------------------------------------------------------------

def test_target_summary_text_matches_view_model():
    t = Target("y", "Yetişkin", 2, Rule(days=[5, 6], from_time="18:00"))
    assert L.target_summary_text(t) == "Yetişkin: 2 kişi · hafta sonu · 18:00 sonrası"


# ---- edit plan ----------------------------------------------------------------------

def raw_cfg():
    return {"plays": {"p": {"active": True, "tag_overrides": {}, "targets": [
        {"id": "y", "name": "Yetişkin", "people": 2,
         "rule": {"days": None, "from_time": None, "to_time": None, "overrides": {},
                  "exclude_dates": ["2026-12-01"]}}]}},
        "sources": ["x"], "paused": False, "version": 1}


def form(**kw):
    base = dict(people=2, days=None, from_text="", to_text="", overrides={}, excluded=set(),
                dates=["2026-12-01", "2026-12-02"])
    base.update(kw)
    return base


def test_plan_orders_and_covers_all_fields():
    plan = L.build_edit_plan("p", "y", **form(
        people=3, days=[5, 6], from_text="18", to_text="22:30",
        overrides={4: ("17:00", ""), 6: ("", "")}, excluded={"2026-12-02"}))
    names = [n for n, _a in plan]
    assert names[:3] == ["set_people", "set_days", "set_hours"]
    assert ("set_people", ("p", "y", 3)) in plan
    assert ("set_days", ("p", "y", [5, 6])) in plan
    assert ("set_hours", ("p", "y", "18:00", "22:30")) in plan
    assert ("set_day_override", ("p", "y", 4, "17:00", None)) in plan
    # row 6 is blank -> override removed (None, None); untouched weekdays cleared too
    assert ("set_day_override", ("p", "y", 6, None, None)) in plan
    assert sum(1 for n, _a in plan if n == "set_day_override") == 7
    assert ("set_excluded_date", ("p", "y", "2026-12-02", True)) in plan
    assert ("set_excluded_date", ("p", "y", "2026-12-01", False)) in plan


def test_plan_invalid_time_raises_with_field_name():
    with pytest.raises(ValueError) as e:
        L.build_edit_plan("p", "y", **form(from_text="25:00"))
    assert "başlangıç" in str(e.value)
    with pytest.raises(ValueError) as e:
        L.build_edit_plan("p", "y", **form(overrides={2: ("10:00", "xx")}))
    assert "Çar" in str(e.value)


def test_plan_rejects_bad_people_and_empty_days():
    with pytest.raises(ValueError):
        L.build_edit_plan("p", "y", **form(people=5))
    with pytest.raises(ValueError):
        L.build_edit_plan("p", "y", **form(people=0))
    with pytest.raises(ValueError) as e:
        L.build_edit_plan("p", "y", **form(days=[]))
    assert "gün" in str(e.value)


def test_plan_rejects_from_after_to():
    with pytest.raises(ValueError):
        L.build_edit_plan("p", "y", **form(from_text="22:00", to_text="18:00"))


def test_dry_run_applies_plan_and_validates_without_touching_input():
    cfg = raw_cfg()
    before = copy.deepcopy(cfg)
    plan = L.build_edit_plan("p", "y", **form(
        people=4, days=[0, 1], from_text="10:00", overrides={5: ("12:00", "14:00")},
        excluded={"2026-12-02"}))
    out = L.dry_run(plan, cfg)
    assert cfg == before
    rule = out["plays"]["p"]["targets"][0]["rule"]
    assert out["plays"]["p"]["targets"][0]["people"] == 4
    assert rule["days"] == [0, 1] and rule["from_time"] == "10:00"
    assert rule["overrides"] == {"5": {"from": "12:00", "to": "14:00"}}
    assert rule["exclude_dates"] == ["2026-12-02"]
    config_from_dict(out)


def test_dry_run_unknown_target_is_value_error():
    plan = L.build_edit_plan("p", "zz", **form())
    with pytest.raises(ValueError):
        L.dry_run(plan, raw_cfg())


def test_apply_plan_calls_service_methods_in_order():
    calls = []

    class Svc:
        def __getattr__(self, name):
            return lambda *a: calls.append((name, a))

    plan = [("set_people", ("p", "y", 2)), ("set_hours", ("p", "y", None, None))]
    L.apply_plan(Svc(), plan)
    assert calls == plan


def test_editor_dates_union_of_sessions_and_existing_excludes():
    entry = {"sessions": {
        "a": {"start": "2026-11-20T20:00:00+03:00"},
        "b": {"start": "2026-11-21T20:00:00+03:00", "gone": True},
        "c": {"start": "2026-11-20T15:00:00+03:00"},
        "old": {"start": "2026-11-01T20:00:00+03:00"}}}
    assert L.editor_dates(entry, ["2026-12-31", "bad"], NOW) == ["2026-11-20", "2026-12-31"]


# ---- sessions / rows ----------------------------------------------------------------

def test_session_lines_sorted_and_labelled():
    entry = {"sessions": {
        "s2": {"start": "2026-11-22T20:00:00+03:00", "status": "upcoming",
               "opens_at": "2026-11-16T10:00:00+03:00"},
        "s1": {"start": "2026-11-20T20:00:00+03:00", "status": "on_sale"},
        "s3": {"start": "2026-11-25T20:00:00+03:00", "status": "sold_out"},
        "gone": {"start": "2026-11-26T20:00:00+03:00", "status": "on_sale", "gone": True},
        "past": {"start": "2026-11-01T20:00:00+03:00", "status": "on_sale"},
        "odd": {"start": "2026-11-27T20:00:00+03:00", "status": "weird"}}}
    lines = L.session_lines(entry, NOW)
    assert [x.when for x in lines] == [
        "20 Kasım Cuma 20:00", "22 Kasım Pazar 20:00", "25 Kasım Çarşamba 20:00",
        "27 Kasım Cuma 20:00"]
    assert lines[0].status == "satışta" and lines[0].opens == ""
    assert lines[1].status == "yakında" and lines[1].opens == "16 Kasım Pazartesi 10:00"
    assert lines[2].status == "tükendi"
    assert lines[3].status == "belirsiz"
    assert all(x.icon for x in lines)


def test_session_lines_tolerates_missing_entry():
    assert L.session_lines(None, NOW) == []
    assert L.session_lines({"sessions": None}, NOW) == []


class FakeRow:
    def __init__(self, **kw):
        self.slug, self.title, self.tags, self.genre = "s", "Oyun", ["çocuk", "gündüz"], "Komedi"
        self.duration, self.age, self.price = "90 dk", "7+", "100 TL"
        self.session_count, self.status_summary = 3, "1 satışta"
        self.watched, self.active, self.is_new, self.is_removed = True, True, False, False
        self.target_lines = ["x"]
        self.__dict__.update(kw)


def test_row_values_and_tags():
    r = FakeRow()
    assert L.row_values(r) == ("Oyun", "çocuk, gündüz", "Komedi", "90 dk", "7+", "100 TL",
                               "3", "1 satışta", "Takipte")
    assert L.row_tags(r) == ("watched",)
    assert L.row_tags(FakeRow(is_new=True)) == ("watched", "new")
    assert L.row_tags(FakeRow(is_removed=True, watched=False)) == ("removed",)
    assert L.row_values(FakeRow(is_new=True))[0] == "YENİ · Oyun"
    assert L.row_values(FakeRow(is_removed=True, watched=False))[0] == "KALKTI · Oyun"
    assert L.row_values(FakeRow(watched=False, active=True))[-1] == "—"
    assert L.row_values(FakeRow(watched=False, active=False))[-1] == "Pasif"
    assert L.row_values(FakeRow(watched=False, active=False, target_lines=[]))[-1] == "—"


# ---- status -------------------------------------------------------------------------

def status(**kw):
    base = {"last_scan": "2026-11-13T11:58:30+03:00", "last_scan_ok": None, "mode": "normal",
            "burst_until": None, "next_opening": None, "paused": False, "outbox_pending": 0,
            "heartbeat_age_s": None, "github": None, "errors": []}
    base.update(kw)
    return base


def test_status_view_basic_and_colors():
    v = L.status_view(status(), NOW)
    assert v.last_scan == "13.11 11:58:30"
    assert v.mode == "Normal" and v.next_opening == "yok"
    assert v.github.startswith("bilinmiyor") and v.github_color == L.GRAY
    assert v.heartbeat == "yok" and v.paused is False and v.pending == "0"
    assert v.errors == [] and v.warning == ""


def test_status_view_burst_github_health_and_alerts():
    gh = {"last_run": "2026-11-13T11:55:00+03:00", "lag_s": 130, "gap_s": 300,
          "healthy": True}
    v = L.status_view(status(
        mode="burst", paused=True, outbox_pending=2, heartbeat_age_s=95, github=gh,
        next_opening={"title": "Oyun", "at": "x", "text": "16 Kasım Pazartesi 10:00"},
        errors=["a", "b"]), NOW, refresh_failures=["x", "y"])
    assert v.mode == "Hızlı"
    assert v.next_opening == "Oyun · 16 Kasım Pazartesi 10:00"
    assert v.github_color == L.GREEN and "sağlıklı" in v.github and "2 dk" in v.github
    assert v.heartbeat == "1 dk önce"
    assert v.paused is True and v.pending == "2"
    assert v.errors == ["a", "b"]
    assert "2 sayfa" in v.warning


def test_status_view_unhealthy_github_is_red_and_old_heartbeat_amber():
    gh = {"last_run": "2026-11-13T11:30:00+03:00", "lag_s": 0, "gap_s": 1800,
          "healthy": False}
    v = L.status_view(status(github=gh, heartbeat_age_s=900), NOW)
    assert v.github_color == L.RED and "sorunlu" in v.github
    assert v.heartbeat_color == L.AMBER
    assert L.status_view(status(heartbeat_age_s=60), NOW).heartbeat_color == L.GREEN


def test_status_view_survives_garbage_values():
    v = L.status_view(status(last_scan="nonsense", github={"last_run": None}), NOW)
    assert v.last_scan == "bilinmiyor"


def test_ago_text():
    assert L.ago_text(5) == "az önce"
    assert L.ago_text(95) == "1 dk önce"
    assert L.ago_text(3 * 3600 + 60) == "3 sa önce"
    assert L.ago_text(2 * 86400) == "2 gün önce"
    assert L.ago_text(-4) == "az önce"


# ---- popup / beeps --------------------------------------------------------------------

def test_popup_text_clamps_long_bodies():
    t, body = L.popup_text("Başlık", "x" * 5000)
    assert t == "Başlık" and len(body) <= L.POPUP_MAX_CHARS and body.endswith("…")
    assert L.popup_text("T", "kısa") == ("T", "kısa")


def test_beep_schedule():
    assert L.BEEP_INTERVAL_S == 20 and L.BEEP_MAX == 5
    assert L.should_beep(0, True, False) is True
    assert L.should_beep(4, True, False) is True
    assert L.should_beep(5, True, False) is False
    assert L.should_beep(0, False, False) is False  # beeps disabled
    assert L.should_beep(1, True, True) is False  # dismissed


def test_event_url_prefers_session_url_then_play_url_then_row():
    e1 = Event("ON_SALE", "k", "p", "s", ["y"], {"url": "https://s", "play_url": "https://p"})
    e2 = Event("NEW_PLAY", "k", "p", None, [], {"play_url": "https://p"})
    e3 = Event("NEW_PLAY", "k", "p", None, [], {})
    assert L.event_url([e1], {}) == "https://s"
    assert L.event_url([e2], {}) == "https://p"
    assert L.event_url([e3], {"p": "https://row"}) == "https://row"
    assert L.event_url([e3], {}) == ""
    assert L.event_url([], {}) == ""


# ---- scrub ----------------------------------------------------------------------------

def test_scrub_removes_known_secrets_and_token_shapes():
    s = L.scrub_secrets("fail ghp_abcdefghijklmnopqrstuvwxyz0123 and bot123456:AAAbbbCCC_dd-x "
                        "plus MYSECRETVALUE abc end", ["MYSECRETVALUE", "", "ab"])
    assert "MYSECRETVALUE" not in s and "ghp_" not in s and "AAAbbb" not in s
    assert s.count("***") == 3
    assert "abc" in s  # tiny secrets are not blindly replaced everywhere


def test_scrub_github_pat_and_bearer():
    s = L.scrub_secrets("Authorization: Bearer abc.def-ghi github_pat_11AAA_bbbCCC")
    assert "abc.def-ghi" not in s and "github_pat_" not in s


# ---- ui prefs -------------------------------------------------------------------------

def test_ui_prefs_roundtrip_and_bad_file(tmp_path):
    p = tmp_path / "ui.json"
    assert L.load_ui(p) == {"geometry": None, "beep": True}
    L.save_ui(p, {"geometry": "900x600+10+20", "beep": False})
    assert L.load_ui(p) == {"geometry": "900x600+10+20", "beep": False}
    p.write_text("{not json", encoding="utf-8")
    assert L.load_ui(p) == {"geometry": None, "beep": True}
    p.write_text(json.dumps({"geometry": "evil", "beep": "yes"}), encoding="utf-8")
    assert L.load_ui(p) == {"geometry": None, "beep": True}


def test_save_ui_creates_dir_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "sub" / "ui.json"
    L.save_ui(p, {"geometry": None, "beep": True})
    assert [x.name for x in p.parent.iterdir()] == ["ui.json"]


def test_sanitize_geometry_keeps_window_on_screen():
    assert L.sanitize_geometry("900x600+10+20", 1920, 1080) == "900x600+10+20"
    assert L.sanitize_geometry("900x600+5000+20", 1920, 1080) == "900x600"
    assert L.sanitize_geometry("900x600+-3000+20", 1920, 1080) == "900x600"
    assert L.sanitize_geometry("9000x6000+0+0", 1920, 1080) == "1920x1080+0+0"
    assert L.sanitize_geometry("junk", 1920, 1080) is None
    assert L.sanitize_geometry(None, 1920, 1080) is None
    assert L.sanitize_geometry("100x100+0+0", 1920, 1080) == "400x300+0+0"  # minimum size


# ---- single instance ------------------------------------------------------------------

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_single_instance_second_acquire_fails_until_release():
    port = free_port()
    a, b = L.SingleInstance(port), L.SingleInstance(port)
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()
    b.release()  # idempotent
