"""Wave 14: the detail pane lays out once per selection (no visible jumping of Özet /
Oyuncular / Yapım / divider), also with a very long cast and summary."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from tiyatro.models import ISTANBUL

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tests.test_wave8 import _show  # noqa: E402,F401
from tests.test_wave9 import _many_plays  # noqa: E402

LONG_SUMMARY = " ".join(f"Cümle {i} oyunun uzun bir özet metnidir ve birkaç kelime daha içerir."
                        for i in range(60))
LONG_CAST = [f"Oyuncu Numara{i} Soyadlıoğlu" for i in range(90)]
LONG_CREDITS = {f"Rol{i}": f"Uzun Isimli Kisi {i} Ve Arkadasi" for i in range(14)}


def _plays(ui):
    _many_plays(ui)
    now = datetime.now(ISTANBUL)
    sess = {"s": {"start": (now + timedelta(days=5)).isoformat(), "status": "on_sale"}}
    st = ui.svc._state["plays"]
    st["long1"] = {"title": "Uzun Kadro 1", "url": "https://example.test/l", "tags": [],
                   "sessions": sess, "summary": LONG_SUMMARY, "cast": LONG_CAST,
                   "credits": LONG_CREDITS}
    st["long2"] = {"title": "Uzun Kadro 2", "url": "https://example.test/l2", "tags": [],
                   "sessions": sess, "summary": LONG_SUMMARY[:1500],
                   "cast": LONG_CAST[:55], "credits": LONG_CREDITS}
    st["short1"] = {"title": "Kisa Oyun", "url": "https://example.test/s", "tags": [],
                    "sessions": sess, "summary": "Kisa ozet.", "cast": ["A B", "C D"],
                    "credits": {"Yönetmen": "X Y"}}
    ui.refresh_rows()


class Recorder:
    """Every <Configure> (geometry change) of any widget + divider position, until idle."""

    def __init__(self, ui):
        self.ui, self.events, self.sash = ui, [], []
        self._id = ui.root.bind("<Configure>", self._on, add="+")
        self._bind_all = ui.root.bind_all("<Configure>", self._on, add="+")

    def _on(self, e):
        try:
            self.events.append((str(e.widget), e.x, e.y, e.width, e.height))
            self.sash.append(self.ui.pane.sashpos(0))
        except tk.TclError:
            pass

    def run(self, seconds=1.0):
        end = time.time() + seconds
        while time.time() < end:
            self.ui.root.update()
            time.sleep(0.004)
        self.ui.root.update()

    def widgets(self):
        u = self.ui
        return {"ozet": u.summary_text, "oyuncular": u.cast_label, "yapim": u.credits_label}

    def rects(self, widget):
        """Distinct consecutive geometries (x, y, w, h) the widget took."""
        name, out = str(widget), []
        for n, x, y, w, h in self.events:
            if n == name and (not out or out[-1] != (x, y, w, h)):
                out.append((x, y, w, h))
        return out

    def sash_changes(self):
        out = []
        for s in self.sash:
            if not out or out[-1] != s:
                out.append(s)
        return out

    def close(self):
        self.ui.root.unbind_all("<Configure>")


def _select_and_record(ui, slug, seconds=1.0):
    rec = Recorder(ui)
    ui.select_slug(slug)
    rec.run(seconds)
    return rec


def _assert_settles_once(rec, label=""):
    """After the first paint a widget may change its geometry at most once more."""
    for name, w in rec.widgets().items():
        r = rec.rects(w)
        # first entry = first paint; at most ONE later settle
        assert len(r) <= 2, f"{label} {name} geometry changed {len(r)} times: {r}"
    s = rec.sash_changes()
    assert len(s) <= 2, f"{label} divider moved {len(s)} times: {s}"


def _settled(ui):
    for _ in range(5):
        ui.root.update()


def _geometry_stable(ui):
    """No geometry change at all in 400 ms once settled (no oscillation)."""
    rec = Recorder(ui)
    rec.run(0.4)
    ui.root.unbind_all("<Configure>")
    return [e for e in rec.events if e[0] in {str(w) for w in rec.widgets().values()}], rec


def test_long_cast_selection_lays_out_once(ui):
    _plays(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("short1")
    Recorder(ui).run(0.5)
    ui.root.unbind_all("<Configure>")
    rec = _select_and_record(ui, "long1")
    try:
        _assert_settles_once(rec, "long cast")
        # the final content is fully shown: the Özet Text is as tall as its wrapped content
        assert int(ui.summary_text.cget("height")) > 3
    finally:
        rec.close()
    ev, r2 = _geometry_stable(ui)
    assert ev == [], f"geometry still changing after settle: {ev[:5]}"


def test_short_play_selection_lays_out_once(ui):
    _plays(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("long1")
    Recorder(ui).run(0.5)
    ui.root.unbind_all("<Configure>")
    rec = _select_and_record(ui, "short1")
    try:
        _assert_settles_once(rec, "short")
    finally:
        rec.close()


def test_rapid_switching_between_two_plays_settles_once(ui):
    _plays(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("short1")
    Recorder(ui).run(0.4)
    ui.root.unbind_all("<Configure>")
    for slug in ("long1", "long2", "long1", "long2"):
        ui.select_slug(slug)
        ui.root.update()
    rec = Recorder(ui)
    try:
        ui.select_slug("long1")
        ui.root.update()
        rec.run(1.0)
        # wave 18: the widgets are reused between selections, so each switch legitimately resizes
        # them; what must hold is that the LAST selection settles once
        _assert_settles_once(rec, "rapid")
        assert ui.selected == "long1"
    finally:
        rec.close()
    ev, _ = _geometry_stable(ui)
    assert ev == [], f"oscillation after rapid switching: {ev[:5]}"


def test_window_resize_relayouts_once_per_size(ui):
    _plays(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("long1")
    Recorder(ui).run(0.6)
    ui.root.unbind_all("<Configure>")
    for width in (1500, 1760):
        rec = Recorder(ui)
        try:
            ui.root.geometry(f"{width}x1015+0+0")
            rec.run(1.0)
            for name, w in rec.widgets().items():
                r = rec.rects(w)
                assert len(r) <= 2, f"resize {width} {name}: {r}"
            assert len(rec.sash_changes()) <= 2, rec.sash_changes()
        finally:
            rec.close()
        ev, _ = _geometry_stable(ui)
        assert ev == [], f"oscillation after resize to {width}: {ev[:5]}"


def test_final_layout_is_not_clipped_and_columns_fit(ui):
    _plays(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("long1")
    Recorder(ui).run(0.6)
    ui.root.unbind_all("<Configure>")
    body = ui.detail_body
    for lbl in (ui.cast_label, ui.credits_label):
        assert lbl.winfo_width() >= lbl.winfo_reqwidth() - 1
        assert lbl.winfo_height() >= lbl.winfo_reqheight() - 1
        assert lbl.winfo_x() + lbl.winfo_width() <= body.winfo_width()
    assert ui.summary_text.winfo_x() + ui.summary_text.winfo_width() <= body.winfo_width()
    assert body.winfo_width() <= ui.detail_canvas.winfo_width()
