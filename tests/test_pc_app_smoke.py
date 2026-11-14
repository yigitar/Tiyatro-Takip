"""Headless smoke test of the Tk window with a fake Service (no network, tmp dirs only)."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta

import pytest

tk = pytest.importorskip("tkinter")

from pc import ui as appmod  # noqa: E402
from pc import app_logic as L  # noqa: E402
from pc import configedit  # noqa: E402
from pc.service import RefreshError  # noqa: E402
from pc.viewmodel import build_rows  # noqa: E402
from tiyatro.config import config_from_dict  # noqa: E402
from tiyatro.models import ISTANBUL, Event  # noqa: E402


def iso(days: float, hour: int = 20) -> str:
    d = (datetime.now(ISTANBUL) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    return d.isoformat()


class FakeService:
    def __init__(self):
        self.raw = {"plays": {"p1": {"active": True, "tag_overrides": {}, "targets": [
            {"id": "y", "name": "Yetişkin", "people": 2,
             "rule": {"days": [5, 6], "from_time": None, "to_time": None, "overrides": {},
                      "exclude_dates": []}}]}},
            "sources": ["x"], "paused": False, "version": 1}
        self._state = {"plays": {
            "p1": {"title": "Birinci Oyun", "url": "https://example.test/p1",
                   "tags": ["çocuk"], "genre": "Komedi", "duration_min": 90, "age_limit": 7,
                   "price_from": 150.0, "sessions": {
                       "a": {"start": iso(5), "status": "on_sale"},
                       "b": {"start": iso(6), "status": "upcoming", "opens_at": iso(2, 10)},
                       "c": {"start": iso(7), "status": "sold_out"}}},
            "p2": {"title": "İkinci Oyun", "url": "https://example.test/p2", "tags": [],
                   "sessions": {"d": {"start": iso(9), "status": "on_sale"}}}}}
        self.new_slugs, self.removed_slugs = {"p2"}, set()
        self.refresh_failures: list[str] = []
        self.calls: list[tuple] = []
        self.callbacks = []
        self.refresh_error: Exception | None = None
        self._cfg = config_from_dict(self.raw)

    @property
    def config(self):
        return self._cfg

    @property
    def state(self):
        return self._state

    def on_notify(self, cb):
        self.callbacks.append(cb)

    def rows(self, now=None):
        return build_rows(self._state, self._cfg, set(self._state["plays"]) - self.new_slugs,
                          self.removed_slugs, datetime.now(ISTANBUL))

    def status(self):
        return {"last_scan": iso(0, 12), "last_scan_ok": None, "mode": "normal",
                "burst_until": None, "next_opening": None, "paused": self.raw["paused"],
                "outbox_pending": 1, "heartbeat_age_s": 40,
                "github": {"last_run": iso(0, 11), "lag_s": 0, "gap_s": 120, "healthy": True},
                "errors": []}

    def reload_config(self):
        return self._cfg

    def refresh_now(self):
        if self.refresh_error:
            raise self.refresh_error
        self.new_slugs = set()
        return []

    def _apply(self, name, *args):
        self.calls.append((name, args))
        new = copy.deepcopy(self.raw)
        getattr(configedit, name)(new, *args)
        config_from_dict(new)
        self.raw, self._cfg = new, config_from_dict(new)

    def __getattr__(self, name):
        if name in ("set_active", "ensure_target", "set_people", "set_days", "set_hours",
                    "set_day_override", "set_excluded_date", "remove_target"):
            return lambda *a: self._apply(name, *a)
        if name in ("pause", "resume"):
            return lambda: self._apply("set_paused", name == "pause")
        raise AttributeError(name)


class Box:
    """Message box recorder (real ones would block the test)."""

    def __init__(self):
        self.shown = []
        self.answer = True

    def showerror(self, *a):
        self.shown.append(("error", a))

    def showwarning(self, *a):
        self.shown.append(("warning", a))

    def showinfo(self, *a):
        self.shown.append(("info", a))

    def askyesno(self, *a):
        self.shown.append(("ask", a))
        return self.answer


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def ui(tmp_path, tk_root):
    root = tk_root
    for w in root.winfo_children():
        w.destroy()
    root.configure(menu=tk.Menu(root))
    svc, box, opened, beeps = FakeService(), Box(), [], []
    app = appmod.App(root, svc, tmp_path, open_url=opened.append,
                     beep=lambda: beeps.append(1), msgbox=box, inline=True)
    app.refresh_rows()
    app.update_status()
    app.opened, app.beeps_log, app.box, app.svc = opened, beeps, box, svc
    try:
        yield app
    finally:
        app.closing = True
        for w in list(root.winfo_children()):
            w.destroy()


def test_window_lists_plays_with_markers_and_columns(ui):
    assert [c for c in ui.tree["columns"]] == [c[0] for c in appmod.COLUMNS]
    assert [ui.tree.heading(c)["text"] for c in ui.tree["columns"]] == [
        "Oyun", "Etiketler", "Tür", "Süre", "Yaş", "Fiyat", "Seans", "Durum özeti", "Takip"]
    kids = ui.tree.get_children()
    assert kids[0] == "p1"  # watched first
    assert "YENİ" in ui.tree.item("p2", "values")[0]
    assert "new" in ui.tree.item("p2", "tags")
    assert ui.refresh_btn and ui.pause_btn
    assert ui.status_labels["gh"].cget("text").startswith("sağlıklı")


def test_select_play_shows_detail_and_opens_page(ui):
    ui.select_slug("p1")
    assert len(ui.session_tree.get_children()) == 3
    assert "Düzenle" in [b.cget("text") for b in ui.target_buttons.values()]
    ui.open_btn.invoke()
    assert ui.opened == ["https://example.test/p1"]
    assert str(ui.add_buttons["y"].cget("state")) == "disabled"  # already has Yetişkin


def test_active_toggle_and_add_target_use_service(ui):
    ui.select_slug("p2")
    ui.active_chk.invoke()
    ui.pump()
    assert ("set_active", ("p2", True)) in ui.svc.calls
    ui.select_slug("p2")
    ui.add_buttons["c"].invoke()
    ui.pump()
    assert ("ensure_target", ("p2", "c")) in ui.svc.calls
    # activating already added the default (adult) target (final review I8)
    assert any("Çocuk" in t.name for t in ui.svc.config.plays["p2"].targets)


def test_editor_saves_everything_in_one_batch(ui):
    ui.select_slug("p1")
    ed = ui.open_editor("p1", "y")
    ed.people.set("3")
    ed.set_days([0, 1, 2, 3, 4])
    ed.from_var.set("18")
    ed.to_var.set("22:30")
    ed.over_vars[4][0].set("17:00")
    first_date = ed.dates[0]
    ed.excl_vars[first_date].set(True)
    ed.save()
    ui.pump()
    assert not ed.winfo_exists()
    t = ui.svc.config.plays["p1"].targets[0]
    assert t.people == 3 and t.rule.days == [0, 1, 2, 3, 4]
    assert (t.rule.from_time, t.rule.to_time) == ("18:00", "22:30")
    assert t.rule.overrides == {4: {"from": "17:00", "to": None}}
    assert t.rule.exclude_dates == [first_date]
    assert "18:00 sonrası" in ui.rows_by_slug["p1"].target_lines[0]


def test_editor_invalid_input_writes_nothing_and_shows_error(ui):
    ed = ui.open_editor("p1", "y")
    ed.from_var.set("25:99")
    ed.save()
    ui.pump()
    assert ed.winfo_exists() and "geçersiz saat" in ed.error.get()
    assert ui.svc.calls == []
    ed.from_var.set("")
    ed.people.set("abc")
    ed.save()
    assert "kişi sayısı" in ed.error.get() and ui.svc.calls == []
    ed.destroy()


def test_delete_target_asks_then_removes(ui):
    ui.box.answer = False
    ui.delete_target("p1", "y", "Yetişkin")
    assert ui.svc.calls == []
    ui.box.answer = True
    ui.delete_target("p1", "y", "Yetişkin")
    ui.pump()
    assert ui.svc.config.plays["p1"].targets == []


def test_refresh_success_and_failure_keep_list(ui):
    ui.refresh_list()
    ui.pump()
    assert "Liste güncellendi" in ui.msg_var.get()
    assert str(ui.refresh_btn.cget("state")) == "normal"
    assert "YENİ" not in ui.tree.item("p2", "values")[0]
    ui.svc.refresh_error = RefreshError("sayfa engellendi")
    ui.refresh_list()
    ui.pump()
    assert "sayfa engellendi" in ui.msg_var.get() and ui.box.shown[-1][0] == "warning"
    assert set(ui.tree.get_children()) == {"p1", "p2"}


def test_pause_toggle_and_status_badge(ui):
    ui.toggle_pause()
    ui.pump()
    ui.update_status()
    assert ui.svc.raw["paused"] is True and ui.pause_btn.cget("text") == "Devam"
    assert ui.paused_badge.winfo_manager() == "grid"
    ui.toggle_pause()
    ui.pump()
    ui.update_status()
    assert ui.pause_btn.cget("text") == "Duraklat"


def test_notification_popup_beeps_and_stacks(ui):
    ev = Event("ON_SALE", "k", "p1", "a", ["y"], {})
    for cb in ui.svc.callbacks:
        for i in range(5):
            cb(f"Başlık {i}", "Metin", [ev])
    ui.pump()
    assert len(ui.popups) == appmod.MAX_POPUPS and len(ui.popup_wait) == 2
    first = ui.popups[0]
    assert first.url == "https://example.test/p1"
    assert len(ui.beeps_log) == 1  # one shared beep series for all popups
    first.open_btn.invoke()
    assert ui.opened[-1] == "https://example.test/p1"
    first.seen_btn.invoke()
    assert len(ui.popups) == appmod.MAX_POPUPS and len(ui.popup_wait) == 1
    ui.beep_var.set(False)
    ui._beep_toggled()
    assert ui.beep_enabled is False
    assert json.loads((ui.local_dir / "ui.json").read_text(encoding="utf-8"))["beep"] is False
    ui.beep_var.set(True)
    ui._beep_toggled()
    ui.beeps_log.clear()
    ui.beep_count = 0  # a new notification restarts the series
    for _ in range(10):
        ui._beep_tick()
    assert len(ui.beeps_log) == L.BEEP_MAX  # max 5 beeps per batch, single timer
    while ui.popups:
        ui.popups[0].seen()
    assert len(ui.popup_wait) == 0 and ui._beep_after is None


def test_close_saves_ui_state_and_destroys(ui):
    ui.root.geometry("800x500+30+40")
    ui.root.update_idletasks()
    ui.close(destroy=False)  # the fixture destroys the shared root
    assert ui.closing
    saved = json.loads((ui.local_dir / "ui.json").read_text(encoding="utf-8"))
    assert saved["geometry"] == ui.root.geometry() and saved["geometry"].endswith("+30+40")


def test_scan_worker_survives_errors_never_busy_loops_and_stops_fast():
    import threading
    import time

    class Svc:
        def __init__(self):
            self.ticks = 0

        def tick(self):
            self.ticks += 1
            raise RuntimeError("boom")

        def next_interval(self):
            return 1.0

    svc, posted = Svc(), []
    worker = appmod.ScanWorker(svc, posted.append)
    worker.start()
    time.sleep(0.4)
    assert svc.ticks == 1  # slept instead of looping
    assert posted and posted[0][0] == "worker_error" and "boom" in posted[0][1]
    t0 = time.monotonic()
    worker.stop()
    worker.join(timeout=2)
    assert not worker.is_alive() and time.monotonic() - t0 < 1.5
    assert threading.active_count() >= 1


def test_scan_worker_wake_runs_next_tick():
    import time

    class Svc:
        ticks = 0

        def tick(self):
            Svc.ticks += 1
            return "ok"

        def next_interval(self):
            return 3600

    posted = []
    worker = appmod.ScanWorker(Svc(), posted.append)
    worker.start()
    time.sleep(0.2)
    worker.wake()
    time.sleep(0.2)
    worker.stop()
    worker.join(timeout=2)
    assert Svc.ticks == 2 and posted[0] == ("tick", "ok")


def test_actions_disable_button_until_done_and_ignore_double_clicks(ui):
    ui.pause_btn.invoke()
    assert str(ui.pause_btn.cget("state")) == "disabled"
    assert ui.pause_btn.cget("text") == "bekleniyor…"
    ui.pause_btn.invoke()  # ignored while pending (even if forced)
    ui.toggle_pause()
    assert ui.svc.calls == [("set_paused", (True,))]
    ui.pump()
    assert str(ui.pause_btn.cget("state")) == "normal" and ui.pause_btn.cget("text") == "Devam"

    ui.select_slug("p2")
    btn = ui.add_buttons["c"]
    btn.invoke()
    assert btn.cget("text") == "bekleniyor…"
    btn.invoke()
    ui.pump()
    assert [c for c in ui.svc.calls if c[0] == "ensure_target"] == [("ensure_target", ("p2", "c"))]
    assert str(ui.add_buttons["c"].cget("state")) == "disabled"  # now exists (from config ids)
    assert str(ui.add_buttons["y"].cget("state")) == "normal"


def test_failed_edit_reenables_and_reports(ui):
    def boom(*a):
        raise RuntimeError("store down")

    ui.svc.set_active = boom
    ui.select_slug("p2")
    ui.active_chk.invoke()
    ui.pump()
    assert ui.box.shown[-1][0] == "error" and "store down" in ui.box.shown[-1][1][1]
    assert ("active", "p2") not in ui._pending
    assert ui.active_var.get() is False  # checkbox restored to the real state


def test_identical_refreshes_do_not_rebuild_the_detail_pane(ui):
    ui.select_slug("p1")
    pane = ui.session_tree
    for _ in range(2):
        ui.refresh_rows()
        ui.root.update()
    assert ui.session_tree is pane
    ui.svc._apply("set_people", "p1", "y", 4)  # a real change does rebuild it
    ui.refresh_rows()
    assert ui.session_tree is not pane


def test_editor_cancel_and_failed_save_refresh_the_list(ui):
    calls = []
    real = ui.svc.rows
    ui.svc.rows = lambda *a, **k: calls.append(1) or real(*a, **k)
    ed = ui.open_editor("p1", "y")
    ed.destroy()
    ui.root.update()
    assert calls, "cancelled editor must refresh"

    calls.clear()
    ed = ui.open_editor("p1", "y")
    ed.people.set("4")

    def boom(*a):
        raise RuntimeError("write failed")

    ui.svc.set_people = boom
    ed.save()
    ui.pump()
    assert ed.winfo_exists() and "write failed" in ed.error.get() and calls
    assert str(ed.save_btn.cget("state")) == "normal" and ed.save_btn.cget("text") == "Kaydet"
    ed.destroy()


def test_unexpected_error_in_editor_validation_is_shown_inline(ui, monkeypatch):
    ed = ui.open_editor("p1", "y")

    def boom(*a):
        raise RuntimeError("surprise")

    monkeypatch.setattr(L, "dry_run", boom)
    ed.save()
    assert "surprise" in ed.error.get() and ui.svc.calls == []
    ed.destroy()
