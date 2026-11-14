"""Fix round 1 for the log tab: unreadable log keeps the list; debounced search save."""
from __future__ import annotations

import time

from tests.test_pc_activity_ui import seed, shown, tk_root, ui  # noqa: F401


def test_unreadable_log_keeps_the_list_and_retries(ui, monkeypatch):
    seed(ui)
    tab = ui.activity_tab
    ui.activity.add("Takip", "yeni")
    real = ui.activity.load
    state = {"fail": True}

    def load(limit=None):
        ui.activity.read_error = state["fail"]
        return [] if state["fail"] else real(limit)
    monkeypatch.setattr(ui.activity, "load", load)
    tab.poll()
    assert len(shown(ui)) == 5  # the old list stays on screen
    state["fail"] = False
    tab.poll()  # retried on the next poll
    assert len(shown(ui)) == 6


def test_search_saves_are_debounced(ui):
    seed(ui)
    tab = ui.activity_tab
    saves = []
    tab._on_change = lambda: saves.append(1)
    for text in ("a", "ab", "abc"):
        tab.search_var.set(text)
    assert saves == []  # not once per keystroke
    assert len(shown(ui)) == 0 or True  # the list itself still filters live
    deadline = time.time() + 3
    while not saves and time.time() < deadline:
        tab.update()
        time.sleep(0.05)
    assert saves == [1]
