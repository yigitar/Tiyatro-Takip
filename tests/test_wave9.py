"""Wave 9: discovery notice ("yeni oyun/etkinlik bulundu") for every source, silent baselines,
no flood on deploy, digest for many finds, genre without the 'Tiyatro' fallback."""
from __future__ import annotations

import copy
from datetime import timedelta

from tiyatro import runner
from tiyatro import srcevents as S
from tiyatro.config import config_from_dict, default_config
from tiyatro.models import Event
from tiyatro.notify import format_message
from tiyatro.state import diff, empty_state
from tests.test_runner import BASE, Env, NOW as RNOW, mkplay
from tests.test_wave6 import NOW, at, listing, new_state


def fold(st, cfg, items, now):
    return S.fold_listings(st, cfg, "basiskele", items, now)


# ---- source events: baseline, once per id, followed or not -----------------------------

def test_first_scan_of_a_source_is_silent_then_each_new_event_is_found_once():
    st, cfg = new_state(), default_config()
    assert fold(st, cfg, [listing("bk:a"), listing("bk:b")], NOW) == []          # baseline
    ev = fold(st, cfg, [listing("bk:a"), listing("bk:b"), listing("bk:c")],
              NOW + timedelta(minutes=5))
    assert [(e.kind, e.key) for e in ev] == [("SRC_NEW", "SRC_NEW:bk:c")]
    assert ev[0].payload["followed"] is False
    assert fold(st, cfg, [listing("bk:a"), listing("bk:c")], NOW + timedelta(minutes=10)) == []


def test_other_source_has_its_own_baseline():
    st, cfg = new_state(), default_config()
    fold(st, cfg, [listing("bk:a")], NOW)
    vkv = S.Listing("vkv", "vkv:1", "Yeni", "Tiyatro", at(5, 20), "VKV", "https://x.test/1",
                    follow_default=False)
    assert S.fold_listings(st, cfg, "vkv", [vkv], NOW + timedelta(minutes=5)) == []


def test_followed_new_event_says_followed_and_past_events_stay_silent():
    st, cfg = new_state(), default_config()
    fold(st, cfg, [listing("bk:a")], NOW)
    cfg.event_follow = {"bk:b": True}
    past = listing("bk:p", start=NOW - timedelta(days=1))
    ev = fold(st, cfg, [listing("bk:a"), listing("bk:b"), past], NOW + timedelta(minutes=5))
    assert [e.key for e in ev] == ["SRC_NEW:bk:b"] and ev[0].payload["followed"] is True
    assert "🟢 Takipte." in format_message(ev, {}, cfg)[0]


def test_nothing_in_the_state_at_deploy_time_triggers_the_notice():
    """Flood guard. Before this release no SRC_NEW key is ever in ``emitted`` for unfollowed
    events and NEW_PLAY keys may be missing too; only the *records* keep an item from being
    'new'. A state with records but an empty ``emitted`` must stay silent on the next scan."""
    st, cfg = new_state(), default_config()
    items = [listing(f"bk:{i}") for i in range(8)]
    fold(st, cfg, items, NOW)
    deployed = copy.deepcopy(st)
    deployed["emitted"] = {}                       # as in the pre-deploy state.json
    assert fold(deployed, cfg, items, NOW + timedelta(minutes=5)) == []
    # plays: same story (state has the play, emitted lost the key)
    play = mkplay("p1")
    state, evs = diff(empty_state(), [play], default_config(), RNOW)
    assert [e.kind for e in evs] == ["NEW_PLAY"]
    state["emitted"] = {}
    _state, evs = diff(state, [play], default_config(), RNOW + timedelta(minutes=5))
    assert evs == []


def test_event_dropped_by_pruning_cannot_be_announced_twice():
    st, cfg = new_state(), default_config()
    fold(st, cfg, [listing("bk:a")], NOW)
    first = fold(st, cfg, [listing("bk:a"), listing("bk:b")], NOW + timedelta(minutes=5))
    assert len(first) == 1
    del st["source_events"]["bk:b"]                # record lost: looks new again ...
    assert fold(st, cfg, [listing("bk:a"), listing("bk:b")], NOW + timedelta(minutes=10)) == []
    # ... but the emitted key (the dedupe) holds


# ---- Biletinial plays -----------------------------------------------------------------------

def test_new_play_payload_text_and_follow_note():
    play = mkplay("p1", title="Kuş Dili")
    state, _ = diff(empty_state(), [mkplay("p0")], default_config(), RNOW)
    cfg = default_config()
    _s, [ev] = diff(state, [mkplay("p0"), play], cfg, RNOW + timedelta(minutes=5))
    p = ev.payload
    assert p["source"] == "biletinial" and p["followed"] is False
    assert p["start"].startswith("2026-11-21T20:00")
    [text] = format_message([ev], {}, cfg)
    assert text.startswith("🆕 Yeni oyun bulundu: <b>Kuş Dili</b> · Biletinial · 21 Kasım Cumartesi 20:00 · ")
    assert BASE + "p1" in text and "Takipte DEĞİL" in text and "menüden" in text
    # a play the user already follows (entry exists in config) says so
    cfg2 = config_from_dict({"plays": {"p1": {"active": True, "targets": [],
                                              "tag_overrides": {}}}})
    _s, [ev2] = diff(state, [mkplay("p0"), play], cfg2, RNOW + timedelta(minutes=5))
    assert ev2.payload["followed"] is True
    assert "🟢 Takipte." in format_message([ev2], {}, cfg2)[0]


def test_new_play_over_the_wire_once_and_first_run_silent(monkeypatch):
    env = Env(monkeypatch, [mkplay("a")])
    env.run(RNOW)
    assert env.tg.sent == []                       # first-ever run
    env.web.set(mkplay("b", title="Yeni Oyun"))
    env.web.venue.append("b")
    env.run(RNOW + timedelta(minutes=5))
    env.run(RNOW + timedelta(minutes=10))
    assert len(env.tg.sent) == 1 and "Yeni oyun bulundu" in env.tg.sent[0]
    assert "Yeni Oyun" in env.tg.sent[0]


def test_paused_discards_the_discovery_notice(monkeypatch):
    env = Env(monkeypatch, [mkplay("a")], config={"plays": {}, "paused": True})
    env.run(RNOW)
    env.web.set(mkplay("b"))
    env.web.venue.append("b")
    env.run(RNOW + timedelta(minutes=5))
    assert env.tg.sent == []


# ---- many finds in one scan -> one digest, not a flood -----------------------------------------

def test_many_new_events_in_one_scan_go_out_as_one_digest(monkeypatch):
    st, cfg = new_state(), default_config()
    fold(st, cfg, [listing("bk:a")], NOW)
    items = [listing("bk:a")] + [listing(f"bk:n{i}", title=f"Oyun {i}") for i in range(7)]
    ev = fold(st, cfg, items, NOW + timedelta(minutes=5))
    assert len(ev) == 7
    state = _state_with(st)
    runner._queue_events(state, ev, False, NOW, "github")
    assert len(state["outbox"]) == 1 and state["outbox"][0]["key"].startswith("NEWFOLD:")
    [text] = format_message(runner._entry_events(state["outbox"][0]), {}, cfg)
    assert "(7)" in text and text.count("• ") == 7 and "Takipte DEĞİL" in text
    # same set -> same key (a retry/second runner does not duplicate)
    again = _state_with(st)
    runner._queue_events(again, ev, False, NOW, "pc")
    assert again["outbox"][0]["key"] == state["outbox"][0]["key"]
    # three or fewer stay individual messages
    few = _state_with(st)
    runner._queue_events(few, ev[:3], False, NOW, "github")
    assert [e["key"] for e in few["outbox"]] == [e.key for e in ev[:3]]


def _state_with(st):
    return runner._prepare({"plays": {}, "meta": {}, "emitted": dict(st["emitted"])})
