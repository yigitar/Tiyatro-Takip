"""Pure tests of the activity log (pc/activity.py): file, rotation, filter, wording."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

import pytest

from pc import activity as A
from tiyatro.models import ISTANBUL

T0 = datetime(2026, 11, 14, 14, 35, 10, tzinfo=ISTANBUL)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, **kw):
        self.t += timedelta(**kw)


def make(tmp_path, **kw):
    return A.ActivityLog(tmp_path / "activity.jsonl", clock=kw.pop("clock", Clock()), **kw)


def lines(tmp_path):
    return (tmp_path / "activity.jsonl").read_text(encoding="utf-8").splitlines()


def test_add_appends_json_line_with_istanbul_iso_timestamp(tmp_path):
    log = make(tmp_path)
    entry = log.add("Liste kontrolü", "6 oyun", "değişiklik yok")
    row = json.loads(lines(tmp_path)[0])
    assert row == {"ts": "2026-11-14T14:35:10+03:00", "cat": "Liste kontrolü",
                   "level": "info", "title": "6 oyun", "detail": "değişiklik yok"}
    assert entry == row


def test_load_is_newest_first_and_limited(tmp_path):
    clock = Clock()
    log = make(tmp_path, clock=clock)
    for n in range(5):
        clock.tick(minutes=1)
        log.add("Takip", f"olay {n}")
    assert [e["title"] for e in log.load()] == [f"olay {n}" for n in (4, 3, 2, 1, 0)]
    assert [e["title"] for e in log.load(2)] == ["olay 4", "olay 3"]


def test_missing_file_loads_empty(tmp_path):
    assert make(tmp_path).load() == []


def test_corrupt_lines_and_garbage_are_skipped(tmp_path):
    path = tmp_path / "activity.jsonl"
    good = json.dumps({"ts": "2026-11-14T10:00:00+03:00", "cat": "Takip", "level": "info",
                       "title": "iyi", "detail": ""})
    path.write_bytes(("not json\n" + good + "\n[1,2]\n{\"ts\": 5}\n\xff\xfe broken\n"
                      ).encode("latin-1"))
    log = A.ActivityLog(path, clock=Clock())
    assert [e["title"] for e in log.load()] == ["iyi"]
    log.add("Takip", "yeni")  # appending to a damaged file still works
    assert [e["title"] for e in log.load()] == ["yeni", "iyi"]


def test_unterminated_last_line_does_not_swallow_next_entry(tmp_path):
    path = tmp_path / "activity.jsonl"
    path.write_text('{"ts": "2026-11-14T10:00:00+03:00", "cat": "Takip", "level": "info", '
                    '"title": "a", "detail": ""}', encoding="utf-8")  # no trailing newline
    log = A.ActivityLog(path, clock=Clock())
    log.add("Takip", "b")
    assert [e["title"] for e in log.load()] == ["b", "a"]


def test_entry_fields_are_sanitised(tmp_path):
    path = tmp_path / "activity.jsonl"
    path.write_text(json.dumps({"ts": "2026-11-14T10:00:00+03:00", "cat": "Takip",
                                "level": "weird", "title": 5, "detail": None}) + "\n",
                    encoding="utf-8")
    [e] = A.ActivityLog(path, clock=Clock()).load()
    assert e["level"] == "info" and e["title"] == "5" and e["detail"] == ""


def test_rotation_keeps_newest_and_is_atomic(tmp_path):
    clock = Clock()
    log = make(tmp_path, clock=clock, max_entries=10)
    for n in range(35):
        clock.tick(seconds=1)
        log.add("Takip", f"olay {n}")
    titles = [e["title"] for e in log.load()]
    assert titles[0] == "olay 34" and len(titles) == 10
    assert titles == [f"olay {n}" for n in range(34, 24, -1)]
    assert len(lines(tmp_path)) <= 11  # trimmed back to the cap, with a little slack
    assert not list(tmp_path.glob("*.tmp"))


def test_rotation_on_a_file_already_over_the_cap(tmp_path):
    path = tmp_path / "activity.jsonl"
    path.write_text("".join(json.dumps({"ts": "2026-11-14T10:00:00+03:00", "cat": "Takip",
                                        "level": "info", "title": str(n), "detail": ""}) + "\n"
                            for n in range(50)), encoding="utf-8")
    log = A.ActivityLog(path, clock=Clock(), max_entries=10)
    log.add("Takip", "son")
    assert [e["title"] for e in log.load()][:2] == ["son", "49"]
    assert len(log.load()) == 10


def test_secrets_never_reach_the_file(tmp_path):
    secrets = ["mysecretvalue123"]
    log = make(tmp_path, secrets=secrets)
    token = "1234567890:ABCdefGhIjKlMnOpQrStUvWxYz012345"
    log.add("Hata/Uyarı", f"hata {token}", "Bearer abc.def.ghi ve mysecretvalue123 ghp_" + "a" * 30)
    raw = (tmp_path / "activity.jsonl").read_text(encoding="utf-8")
    assert token not in raw and "mysecretvalue123" not in raw and "abc.def.ghi" not in raw
    assert "ghp_" + "a" * 30 not in raw and "***" in raw


def test_unknown_category_and_level_are_normalised(tmp_path):
    log = make(tmp_path)
    e = log.add("Bilinmeyen", "x", level="panic")
    assert e["level"] == "info" and e["cat"] == "Bilinmeyen"  # kept readable


def test_write_failure_never_raises(tmp_path):
    (tmp_path / "activity.jsonl").mkdir()  # a directory where the file should be
    log = make(tmp_path)
    assert log.add("Takip", "x") is None
    assert log.load() == []


def test_clear_removes_entries_and_bumps_version(tmp_path):
    log = make(tmp_path)
    log.add("Takip", "x")
    v = log.version
    log.clear()
    assert log.load() == [] and log.version > v
    log.add("Takip", "y")
    assert [e["title"] for e in log.load()] == ["y"]


def test_version_changes_on_add(tmp_path):
    log = make(tmp_path)
    v = log.version
    log.add("Takip", "x")
    assert log.version == v + 1


def test_threads_do_not_lose_or_tear_entries(tmp_path):
    log = make(tmp_path, max_entries=3000)

    def work(k):
        for n in range(50):
            log.add("Takip", f"t{k}-{n}")

    threads = [threading.Thread(target=work, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(log.load()) == 400
    assert all(json.loads(line) for line in lines(tmp_path))


# ---- filter ------------------------------------------------------------------

def sample():
    return [
        {"ts": "2026-11-14T14:35:00+03:00", "cat": "Liste kontrolü", "level": "info",
         "title": "6 oyun", "detail": "değişiklik yok"},
        {"ts": "2026-11-14T14:30:00+03:00", "cat": "Hata/Uyarı", "level": "warn",
         "title": "Biletinial okunamadı", "detail": ""},
        {"ts": "2026-11-14T14:20:00+03:00", "cat": "Hata/Uyarı", "level": "error",
         "title": "Kayıt yazılamadı", "detail": ""},
        {"ts": "2026-11-14T14:10:00+03:00", "cat": "Takip", "level": "info",
         "title": "İstanbul Işık oyunu takibe alındı", "detail": "PC'den"},
    ]


def test_filter_by_category():
    got = A.filter_entries(sample(), cats={"Takip", "Hata/Uyarı"})
    assert [e["title"] for e in got] == ["Biletinial okunamadı", "Kayıt yazılamadı",
                                         "İstanbul Işık oyunu takibe alındı"]
    assert A.filter_entries(sample(), cats=set()) == []
    assert len(A.filter_entries(sample(), cats=None)) == 4  # None = all


def test_filter_by_level():
    got = A.filter_entries(sample(), min_level="warn")
    assert [e["level"] for e in got] == ["warn", "error"]
    assert len(A.filter_entries(sample(), min_level="error")) == 1
    assert len(A.filter_entries(sample(), min_level="info")) == 4


def test_filter_text_is_case_and_turkish_diacritic_insensitive():
    assert len(A.filter_entries(sample(), text="isik oyunu")) == 1
    assert len(A.filter_entries(sample(), text="IŞIK OYUNU")) == 1
    assert len(A.filter_entries(sample(), text="istanbul")) == 1
    assert len(A.filter_entries(sample(), text="degisiklik")) == 1  # searches detail
    assert len(A.filter_entries(sample(), text="hata/uyari")) == 2  # and the category
    assert len(A.filter_entries(sample(), text="  ")) == 4
    assert A.filter_entries(sample(), text="yokboyle") == []


def test_filter_keeps_order_and_combines():
    got = A.filter_entries(sample(), cats={"Hata/Uyarı"}, min_level="error", text="kayıt")
    assert [e["title"] for e in got] == ["Kayıt yazılamadı"]


# ---- wording -----------------------------------------------------------------

def test_when_text_today_and_older():
    now = datetime(2026, 11, 14, 18, 0, tzinfo=ISTANBUL)
    assert A.when_text("2026-11-14T14:35:10+03:00", now) == "14:35"
    assert A.when_text("2026-11-13T09:05:00+03:00", now) == "13.11 09:05"
    assert A.when_text("garbage", now) == "?"


def test_sentence_reads_as_plain_turkish():
    e = {"ts": "2026-11-14T14:35:10+03:00", "cat": "Liste kontrolü", "level": "info",
         "title": "6 oyun, 17 seans (3 satışta, 1 yakında, 13 tükendi)",
         "detail": "değişiklik yok\nKaynak: PC"}
    assert A.sentence(e) == ("14:35 — Liste kontrolü: 6 oyun, 17 seans "
                             "(3 satışta, 1 yakında, 13 tükendi), değişiklik yok")


def test_result_text_is_first_line_of_detail():
    assert A.result_text({"detail": "değişiklik yok\nKaynak: PC"}) == "değişiklik yok"
    assert A.result_text({"detail": ""}) == ""


def test_level_labels():
    assert A.level_label("warn") == "Uyarı" and A.level_label("error") == "Hata"
    assert A.level_label("info") == "Bilgi"


# ---- filter persistence ------------------------------------------------------

def test_filters_default_when_missing_or_corrupt(tmp_path):
    default = {"cats": list(A.CATEGORIES), "level": "all", "text": ""}
    assert A.load_filters(tmp_path / "ui.json") == default
    (tmp_path / "ui.json").write_text("{not json", encoding="utf-8")
    assert A.load_filters(tmp_path / "ui.json") == default
    (tmp_path / "ui.json").write_text(json.dumps({"activity": {
        "cats": "x", "level": 7, "text": 3}}), encoding="utf-8")
    assert A.load_filters(tmp_path / "ui.json") == default


def test_filters_roundtrip_and_unknown_categories_dropped(tmp_path):
    (tmp_path / "ui.json").write_text(json.dumps({"activity": {
        "cats": ["Takip", "Uydurma"], "level": "warn", "text": "ışık"}}), encoding="utf-8")
    assert A.load_filters(tmp_path / "ui.json") == {"cats": ["Takip"], "level": "warn",
                                                    "text": "ışık"}
    (tmp_path / "ui.json").write_text(json.dumps({"activity": {
        "cats": [], "level": "all", "text": ""}}), encoding="utf-8")
    assert A.load_filters(tmp_path / "ui.json")["cats"] == []  # "none ticked" is a real choice


# ---- config diff wording -----------------------------------------------------

def cfg(**plays):
    return {"plays": plays, "sources": ["x"], "paused": False, "version": 1}


def play(active=True, targets=(), tags=None):
    return {"active": active, "targets": list(targets), "tag_overrides": tags or {}}


def target(tid="y", name="Yetişkin", people=2, days=None, frm=None, to=None, excl=()):
    return {"id": tid, "name": name, "people": people,
            "rule": {"days": days, "from_time": frm, "to_time": to, "overrides": {},
                     "exclude_dates": list(excl)}}


TITLES = {"p1": "Hamlet"}


def test_diff_new_play_and_target():
    old = cfg()
    new = cfg(p1=play(True, [target()]))
    assert A.describe_config_diff(old, new, TITLES) == [
        "Hamlet takibe alındı", "Hamlet: Yetişkin hedefi eklendi (2 kişi)"]


def test_diff_deactivate_activate_remove():
    on = cfg(p1=play(True, [target()]))
    off = cfg(p1=play(False, [target()]))
    assert A.describe_config_diff(on, off, TITLES) == ["Hamlet takibi duraklatıldı"]
    assert A.describe_config_diff(off, on, TITLES) == ["Hamlet takibi açıldı"]
    assert A.describe_config_diff(on, cfg(), TITLES) == ["Hamlet takipten çıkarıldı"]


def test_diff_target_rule_changes():
    base = cfg(p1=play(True, [target()]))
    changed = cfg(p1=play(True, [target(people=3, days=[5, 6], frm="18:00",
                                        excl=["2026-12-01"])]))
    got = A.describe_config_diff(base, changed, TITLES)
    assert "Hamlet / Yetişkin: kişi sayısı 2 → 3" in got
    assert "Hamlet / Yetişkin: günler her gün → Cmt, Paz" in got
    assert "Hamlet / Yetişkin: saat 18:00 sonrası" in got
    assert any("hariç" in line and "01.12" in line for line in got)


def test_diff_target_removed_and_paused_and_unknown_slug():
    base = cfg(p1=play(True, [target(), target("c", "Çocuk")]))
    new = cfg(p1=play(True, [target()]))
    assert A.describe_config_diff(base, new, TITLES) == ["Hamlet: Çocuk hedefi silindi"]
    paused = dict(new, paused=True)
    assert A.describe_config_diff(new, paused, {}) == ["Tüm bildirimler duraklatıldı"]
    assert A.describe_config_diff(paused, new, {}) == ["Tüm bildirimler yeniden açıldı"]
    other = cfg(zzz=play(True))
    assert A.describe_config_diff(cfg(), other, {}) == ["zzz takibe alındı"]  # slug fallback


def test_diff_identical_is_empty_and_malformed_is_safe():
    assert A.describe_config_diff(cfg(), cfg(), {}) == []
    assert A.describe_config_diff({}, {"plays": "x"}, {}) == []
    assert A.describe_config_diff(None, None, {}) == []
