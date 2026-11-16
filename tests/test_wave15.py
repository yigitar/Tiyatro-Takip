"""Wave 15 A: the session table (Seans / Durum / Satis acilisi / Baglanti) of a play with
several sessions is measured once per selection: no visible resize after it appeared."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from tiyatro.models import ISTANBUL

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tests.test_wave8 import _show  # noqa: E402,F401
from tests.test_wave9 import _many_plays  # noqa: E402
from tests.test_wave14 import Recorder, _geometry_stable, LONG_SUMMARY, LONG_CAST, LONG_CREDITS  # noqa: E402


def _sessions(n, status="on_sale"):
    now = datetime.now(ISTANBUL)
    return {f"s{i}": {"start": (now + timedelta(days=2 + i, hours=i)).isoformat(),
                      "status": status, "url": f"https://example.test/s{i}"}
            for i in range(n)}


def _multi(ui):
    _many_plays(ui)
    st = ui.svc._state["plays"]
    for n in (1, 2, 3, 8, 11):
        st[f"m{n}"] = {"title": f"Oyun {n} seans", "url": "https://example.test/m", "tags": [],
                       "sessions": _sessions(n), "summary": LONG_SUMMARY if n > 2 else "Kisa.",
                       "cast": LONG_CAST[:40] if n > 2 else ["A B"],
                       "credits": LONG_CREDITS if n > 2 else {"Yönetmen": "X Y"}}
    ui.refresh_rows()


def _table_widgets(ui):
    t = ui.session_tree
    return {"frame": t, "body": t.body, "header": t.header, "hbar": t.hbar,
            "holder": t.master}


def _rects(rec, widget):
    name, out = str(widget), []
    for n, x, y, w, h in rec.events:
        if n == name and (not out or out[-1] != (x, y, w, h)):
            out.append((x, y, w, h))
    return out


def _check(ui, rec, label):
    for name, w in _table_widgets(ui).items():
        r = _rects(rec, w)
        assert len(r) <= 2, f"{label}: session table {name} changed {len(r)} times: {r}"
    ev, _ = _geometry_stable(ui)
    still = [e for e in ev] + [e for e in rec.events[len(rec.events):]]
    assert still == [], f"{label}: still changing: {still[:5]}"
    late = Recorder(ui)
    late.run(0.4)
    ui.root.unbind_all("<Configure>")
    tw = {str(w) for w in _table_widgets(ui).values()}
    moved = [e for e in late.events if e[0] in tw]
    assert moved == [], f"{label}: table re-laid out after settle: {moved[:5]}"


def _select(ui, slug, seconds=1.0):
    rec = Recorder(ui)
    ui.select_slug(slug)
    rec.run(seconds)
    return rec


@pytest.mark.parametrize("n", [2, 3, 8, 11])
def test_multi_session_table_measured_once(ui, n):
    _multi(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("m1")
    Recorder(ui).run(0.5)
    ui.root.unbind_all("<Configure>")
    rec = _select(ui, f"m{n}")
    try:
        _check(ui, rec, f"{n} sessions")
        t = ui.session_tree
        # all rows visible: body as tall as rows need (no scrollbar for <= 8 rows)
        assert t.body.winfo_height() >= min(max(n, 2), 8) * t.row_px - 1
    finally:
        rec.close()


def test_rapid_switch_between_one_and_eight_sessions(ui):
    _multi(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("m1")
    Recorder(ui).run(0.4)
    ui.root.unbind_all("<Configure>")
    for slug in ("m8", "m1", "m8", "m1"):
        ui.select_slug(slug)
        ui.root.update()
    rec = Recorder(ui)
    try:
        ui.select_slug("m8")      # wave 18: the table is reused, each switch resizes it; the
        ui.root.update()          # LAST selection must settle once
        rec.run(1.0)
        _check(ui, rec, "rapid 1<->8")
        assert ui.selected == "m8"
    finally:
        rec.close()


def test_table_height_is_final_from_first_paint(ui):
    """The table body height right after selection (before pumping) equals the final one."""
    _multi(ui)
    _show(ui, 1760, 1015)
    ui.select_slug("m1")
    Recorder(ui).run(0.4)
    ui.root.unbind_all("<Configure>")
    ui.select_slug("m8")
    ui.root.update_idletasks()
    first = ui.session_tree.body.winfo_height()
    Recorder(ui).run(0.8)
    ui.root.unbind_all("<Configure>")
    assert ui.session_tree.body.winfo_height() == first
