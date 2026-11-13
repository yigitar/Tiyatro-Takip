import copy
import json
from datetime import datetime, timedelta

from tiyatro.models import ISTANBUL, Config, Play, PlayWatch, Rule, Session, Status, Target
from tiyatro.state import cleanup, diff, empty_state, next_opening

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=ISTANBUL)
SAT_20 = datetime(2026, 11, 21, 20, 0, tzinfo=ISTANBUL)  # Saturday evening
WED_15 = datetime(2026, 11, 18, 15, 0, tzinfo=ISTANBUL)  # Wednesday afternoon
OPENS = datetime(2026, 11, 16, 10, 0, tzinfo=ISTANBUL)

EVENING = Target("t1", "Akşam", 2, Rule(days=[5, 6], from_time="18:00"))


def sess(sid="s1", status=Status.UPCOMING, start=SAT_20, opens_at=OPENS):
    return Session(sid, start, None, status, opens_at, f"https://example.test/{sid}", "Sahne")


def play(slug="p", sessions=(), title="Oyun"):
    return Play(slug, title, f"https://example.test/{slug}", "Dram", 90, None, 100.0,
                list(sessions))


def cfg(**plays):
    return Config(plays=plays, sources=[], paused=False, version=1)


WATCH = cfg(p=PlayWatch(active=True, targets=[EVENING]))
NOWATCH = cfg()


def kinds(events):
    return [e.kind for e in events]


def test_empty_state_shape():
    st = empty_state()
    assert st["plays"] == {} and st["emitted"] == {}
    assert st["meta"]["fetch_fail_streak"] == 0


def test_new_and_removed_play():
    st, ev = diff(empty_state(), [play()], NOWATCH, T0)
    assert kinds(ev) == ["NEW_PLAY"] and ev[0].key == "NEW_PLAY:p"
    assert ev[0].target_ids == [] and ev[0].payload["title"] == "Oyun"

    st, ev = diff(st, [play("q")], NOWATCH, T0 + timedelta(minutes=5))
    assert kinds(ev) == ["NEW_PLAY"]  # q only; p missing once -> silent
    assert st["plays"]["p"]["missing_count"] == 1

    st, ev = diff(st, [play("q")], NOWATCH, T0 + timedelta(minutes=10))
    assert kinds(ev) == ["REMOVED_PLAY"] and ev[0].key == "REMOVED_PLAY:p"
    assert st["plays"]["p"]["removed"] is True

    st, ev = diff(st, [play("q")], NOWATCH, T0 + timedelta(minutes=15))
    assert ev == []

    # re-appearance: silently clears removal, no new NEW_PLAY
    st, ev = diff(st, [play("q"), play()], NOWATCH, T0 + timedelta(minutes=20))
    assert ev == []
    assert st["plays"]["p"]["removed"] is False
    assert st["plays"]["p"]["missing_count"] == 0


def test_missing_once_then_seen_resets_counter():
    st, _ = diff(empty_state(), [play()], NOWATCH, T0)
    st, _ = diff(st, [], NOWATCH, T0 + timedelta(minutes=5))
    st, _ = diff(st, [play()], NOWATCH, T0 + timedelta(minutes=10))
    st, ev = diff(st, [], NOWATCH, T0 + timedelta(minutes=15))
    assert ev == [] and st["plays"]["p"]["missing_count"] == 1


def test_first_scan_emits_new_play_for_every_play():
    _, ev = diff(empty_state(), [play("a"), play("b"), play("c")], NOWATCH, T0)
    assert sorted(e.key for e in ev) == ["NEW_PLAY:a", "NEW_PLAY:b", "NEW_PLAY:c"]


def test_on_sale_transition_once():
    st, _ = diff(empty_state(), [play(sessions=[sess()])], WATCH, T0)
    t1 = T0 + timedelta(minutes=5)
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, t1)
    assert kinds(ev) == ["ON_SALE"]
    e = ev[0]
    assert e.key == "ON_SALE:s1:1" and e.session_id == "s1" and e.target_ids == ["t1"]
    assert e.payload["start"] == SAT_20.isoformat()
    assert e.payload["url"] == "https://example.test/s1"
    json.dumps(e.payload)
    assert st["plays"]["p"]["sessions"]["s1"]["on_sale_count"] == 1
    assert st["plays"]["p"]["ever_opened"] is True

    # still on sale -> nothing
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, t1 + timedelta(minutes=5))
    assert ev == []

    # sold out, then back on sale >= 30 min after last notification -> new event
    st, _ = diff(st, [play(sessions=[sess(status=Status.SOLD_OUT)])], WATCH, t1 + timedelta(minutes=10))
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, t1 + timedelta(minutes=30))
    assert [e.key for e in ev] == ["ON_SALE:s1:2"]
    assert st["plays"]["p"]["sessions"]["s1"]["on_sale_count"] == 2


def test_on_sale_retransition_within_30_min_suppressed():
    st, _ = diff(empty_state(), [play(sessions=[sess()])], WATCH, T0)
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0)
    assert kinds(ev) == ["ON_SALE"]
    st, _ = diff(st, [play(sessions=[sess(status=Status.SOLD_OUT)])], WATCH, T0 + timedelta(minutes=5))
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0 + timedelta(minutes=29))
    assert ev == []
    assert st["plays"]["p"]["sessions"]["s1"]["on_sale_count"] == 1
    # still on sale once the 30 min gap has passed -> the deferred alert fires (not lost)
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0 + timedelta(minutes=31))
    assert [e.key for e in ev] == ["ON_SALE:s1:2"]


def test_first_seen_on_sale_counts_as_transition():
    _, ev = diff(empty_state(), [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0)
    assert sorted(kinds(ev)) == ["NEW_PLAY", "ON_SALE"]


def test_first_seen_sold_out_no_on_sale():
    st, ev = diff(empty_state(), [play(sessions=[sess(status=Status.SOLD_OUT)])], WATCH, T0)
    assert kinds(ev) == ["NEW_PLAY"]
    assert st["plays"]["p"]["ever_opened"] is True


def test_not_matching_target_no_alert():
    st, _ = diff(empty_state(), [play(sessions=[sess(start=WED_15)])], WATCH, T0)
    st, ev = diff(st, [play(sessions=[sess(start=WED_15, status=Status.ON_SALE)])], WATCH,
                  T0 + timedelta(minutes=5))
    assert ev == []

    # unwatched play: only NEW_PLAY, never session events
    near = OPENS - timedelta(minutes=5)
    st, ev = diff(empty_state(), [play(sessions=[sess(status=Status.UNKNOWN)])], NOWATCH, near)
    assert kinds(ev) == ["NEW_PLAY"]
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], NOWATCH, near + timedelta(minutes=1))
    assert ev == []

    # inactive watch behaves like unwatched
    inactive = cfg(p=PlayWatch(active=False, targets=[EVENING]))
    _, ev = diff(empty_state(), [play(sessions=[sess(status=Status.ON_SALE)])], inactive, T0)
    assert kinds(ev) == ["NEW_PLAY"]


def _seed():
    st, _ = diff(empty_state(), [play(sessions=[sess()])], WATCH, T0)
    return st


def test_opening_soon_window():
    p = [play(sessions=[sess()])]
    _, ev = diff(_seed(), p, WATCH, OPENS - timedelta(minutes=11))
    assert "OPENING_SOON" not in kinds(ev)

    st, ev = diff(_seed(), p, WATCH, OPENS - timedelta(minutes=9))
    assert kinds(ev) == ["OPENING_SOON"]
    assert ev[0].key == "OPENING_SOON:s1" and ev[0].target_ids == ["t1"]
    assert ev[0].payload["opens_at"] == OPENS.isoformat()
    st, ev = diff(st, p, WATCH, OPENS - timedelta(minutes=5))
    assert ev == []  # once

    _, ev = diff(_seed(), p, WATCH, OPENS)
    assert ev == []
    _, ev = diff(_seed(), p, WATCH, OPENS + timedelta(minutes=1))
    assert ev == []

    # exact lower bound fires
    _, ev = diff(_seed(), p, WATCH, OPENS - timedelta(minutes=10))
    assert kinds(ev) == ["OPENING_SOON"]


def test_day_before():
    p = [play(sessions=[sess()])]
    prev_10 = datetime(2026, 11, 15, 10, 0, tzinfo=ISTANBUL)
    _, ev = diff(_seed(), p, WATCH, prev_10 - timedelta(minutes=1))
    assert ev == []

    st, ev = diff(_seed(), p, WATCH, prev_10)
    assert kinds(ev) == ["DAY_BEFORE"] and ev[0].key == "DAY_BEFORE:s1"
    st, ev = diff(st, p, WATCH, prev_10 + timedelta(hours=5))
    assert ev == []  # once
    # later the 10-min window still fires OPENING_SOON
    st, ev = diff(st, p, WATCH, OPENS - timedelta(minutes=10))
    assert kinds(ev) == ["OPENING_SOON"]

    # first seen at 22:00 prev day (after 10:00) -> immediate DAY_BEFORE
    _, ev = diff(empty_state(), p, WATCH, datetime(2026, 11, 15, 22, 0, tzinfo=ISTANBUL))
    assert sorted(kinds(ev)) == ["DAY_BEFORE", "NEW_PLAY"]

    # first seen < 10 min before opening -> only OPENING_SOON
    _, ev = diff(empty_state(), p, WATCH, OPENS - timedelta(minutes=8))
    assert sorted(kinds(ev)) == ["NEW_PLAY", "OPENING_SOON"]


def test_day_before_cutoff_one_hour_before_opening():
    p = [play(sessions=[sess()])]
    _, ev = diff(_seed(), p, WATCH, OPENS - timedelta(minutes=61))
    assert kinds(ev) == ["DAY_BEFORE"]
    _, ev = diff(_seed(), p, WATCH, OPENS - timedelta(minutes=60))
    assert ev == []
    # first seen inside the last hour (outside the 10-min window): no DAY_BEFORE ever
    st, ev = diff(empty_state(), p, WATCH, OPENS - timedelta(minutes=30))
    assert kinds(ev) == ["NEW_PLAY"]
    st, ev = diff(st, p, WATCH, OPENS - timedelta(minutes=9))
    assert kinds(ev) == ["OPENING_SOON"]


def test_no_pre_open_events_after_leaving_upcoming():
    st, _ = diff(_seed(), [play(sessions=[sess(status=Status.SOLD_OUT)])], WATCH,
                 OPENS - timedelta(hours=30))
    _, ev = diff(st, [play(sessions=[sess(status=Status.SOLD_OUT)])], WATCH, OPENS - timedelta(minutes=5))
    assert ev == []


def test_unknown_status_event():
    unk = [play(sessions=[sess(start=WED_15, status=Status.UNKNOWN)])]
    st, ev = diff(_seed(), unk, WATCH, T0 + timedelta(minutes=5))
    # targets not required for UNKNOWN_STATUS on a watched play
    assert kinds(ev) == ["UNKNOWN_STATUS"]
    assert ev[0].key == "UNKNOWN_STATUS:s1" and ev[0].target_ids == []
    st, ev = diff(st, unk, WATCH, T0 + timedelta(minutes=10))
    assert ev == []


def test_diff_does_not_mutate_and_is_idempotent():
    old = _seed()
    snapshot = copy.deepcopy(old)
    p = [play(sessions=[sess(status=Status.ON_SALE)]), play("new")]
    st, ev = diff(old, p, WATCH, T0 + timedelta(minutes=5))
    assert old == snapshot
    assert sorted(kinds(ev)) == ["NEW_PLAY", "ON_SALE"]
    st2, ev2 = diff(st, p, WATCH, T0 + timedelta(minutes=5))
    assert ev2 == []
    # same scan instant with a missing play does not advance missing_count
    st3, _ = diff(st2, [play("new")], WATCH, T0 + timedelta(minutes=10))
    st4, ev4 = diff(st3, [play("new")], WATCH, T0 + timedelta(minutes=10))
    assert ev4 == [] and st4["plays"]["p"]["missing_count"] == 1
    json.dumps(st4)


def test_tags_stored_with_overrides():
    c = cfg(p=PlayWatch(active=False, tag_overrides={"çocuk": True}))
    st, _ = diff(empty_state(), [play(sessions=[sess(start=WED_15)])], c, T0)
    assert st["plays"]["p"]["tags"] == ["gündüz", "çocuk"]


def test_emitted_records_keys():
    st, _ = diff(empty_state(), [play()], NOWATCH, T0)
    assert st["emitted"] == {"NEW_PLAY:p": T0.isoformat()}


def _entry(first_seen, starts, ever_opened=False):
    return {
        "title": "x", "first_seen": first_seen.isoformat(), "last_seen": NOW_C.isoformat(),
        "missing_count": 0, "ever_opened": ever_opened, "archived": False, "removed": False,
        "tags": [], "sessions": {
            f"s{i}": {"status": "upcoming", "opens_at": None, "start": s.isoformat(),
                      "url": "u", "gone": False, "last_on_sale_notified": None,
                      "on_sale_count": 0, "on_sale_pending": False}
            for i, s in enumerate(starts)},
    }


NOW_C = datetime(2027, 6, 1, 12, 0, tzinfo=ISTANBUL)


def test_cleanup():
    future = NOW_C + timedelta(days=3)
    past = NOW_C - timedelta(days=3)
    st = empty_state()
    st["plays"] = {
        "old_unwatched": _entry(NOW_C - timedelta(days=181), [future]),
        "old_watched_future": _entry(NOW_C - timedelta(days=181), [future]),
        "all_past": _entry(NOW_C - timedelta(days=10), [past], ever_opened=True),
        "young": _entry(NOW_C - timedelta(days=179), [future]),
        "exactly_180": _entry(NOW_C - timedelta(days=180), [future]),
        "opened_old": _entry(NOW_C - timedelta(days=400), [future], ever_opened=True),
    }
    st["emitted"] = {"NEW_PLAY:x": (NOW_C - timedelta(days=61)).isoformat(),
                     "NEW_PLAY:y": (NOW_C - timedelta(days=59)).isoformat()}
    c = cfg(old_watched_future=PlayWatch(active=True, targets=[EVENING]))
    snapshot = copy.deepcopy(st)
    out = cleanup(st, c, NOW_C)
    assert st == snapshot
    assert "old_unwatched" not in out["plays"]
    assert out["plays"]["old_watched_future"]["archived"] is False
    assert out["plays"]["all_past"]["archived"] is True
    assert "young" in out["plays"] and "exactly_180" in out["plays"]
    assert "opened_old" in out["plays"]
    assert "NEW_PLAY:x" not in out["emitted"] and "NEW_PLAY:y" in out["emitted"]
    # deleted play leaves a NEW_PLAY tombstone so a still-listed play is not re-announced
    assert "NEW_PLAY:old_unwatched" in out["emitted"]


def test_cleanup_watched_all_past_archived_not_deleted():
    st = empty_state()
    st["plays"] = {"w": _entry(NOW_C - timedelta(days=300), [NOW_C - timedelta(days=1)])}
    out = cleanup(st, cfg(w=PlayWatch(active=True, targets=[EVENING])), NOW_C)
    assert out["plays"]["w"]["archived"] is True


def test_next_opening():
    later = OPENS + timedelta(days=1)
    plays = [
        play("p", sessions=[sess("a", opens_at=later), sess("b", opens_at=OPENS),
                            sess("c", start=WED_15, opens_at=OPENS - timedelta(hours=1)),
                            sess("d", status=Status.SOLD_OUT, opens_at=OPENS - timedelta(hours=2))]),
        play("off", sessions=[sess("e", opens_at=OPENS - timedelta(hours=3))]),
    ]
    c = cfg(p=PlayWatch(active=True, targets=[EVENING]),
            off=PlayWatch(active=False, targets=[EVENING]))
    st, _ = diff(empty_state(), plays, c, T0)
    assert next_opening(st, c, T0) == OPENS
    assert next_opening(st, c, OPENS) == later
    assert next_opening(st, c, later) is None
    assert next_opening(empty_state(), c, T0) is None


def test_on_sale_after_session_vanished_and_returned():  # C1a
    st, _ = diff(empty_state(), [play(sessions=[sess()])], WATCH, T0)
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0 + timedelta(minutes=5))
    assert [e.key for e in ev] == ["ON_SALE:s1:1"]
    st, ev = diff(st, [play(sessions=[])], WATCH, T0 + timedelta(minutes=15))
    assert ev == []
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0 + timedelta(minutes=65))
    assert [e.key for e in ev] == ["ON_SALE:s1:2"]


def test_pending_on_sale_survives_vanish_cycle():  # C1b
    st, _ = diff(empty_state(), [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0)
    st, _ = diff(st, [play(sessions=[sess(status=Status.SOLD_OUT)])], WATCH, T0 + timedelta(minutes=5))
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0 + timedelta(minutes=20))
    assert ev == []  # held by the 30-min gap
    st, ev = diff(st, [play(sessions=[])], WATCH, T0 + timedelta(minutes=31))
    assert ev == []  # gone: nothing emitted
    st, ev = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0 + timedelta(minutes=40))
    assert [e.key for e in ev] == ["ON_SALE:s1:2"]


def test_session_gone_from_listed_play():
    past = T0 - timedelta(days=1)
    s_up = sess("s1")
    s_past = sess("s2", status=Status.SOLD_OUT, start=past, opens_at=None)
    st, _ = diff(empty_state(), [play(sessions=[s_up, s_past])], WATCH, T0)
    assert next_opening(st, WATCH, T0) == OPENS
    st, ev = diff(st, [play(sessions=[s_past])], WATCH, OPENS - timedelta(hours=30))
    assert ev == []
    assert st["plays"]["p"]["sessions"]["s1"]["gone"] is True
    st, ev = diff(st, [play(sessions=[s_past])], WATCH, OPENS - timedelta(minutes=5))
    assert ev == []  # no DAY_BEFORE / OPENING_SOON for a gone session
    assert next_opening(st, WATCH, T0) is None
    # archive: the gone session still counts as history
    assert cleanup(st, WATCH, SAT_20 - timedelta(hours=1))["plays"]["p"]["archived"] is False
    assert cleanup(st, WATCH, SAT_20 + timedelta(hours=1))["plays"]["p"]["archived"] is True


def test_watch_added_while_already_on_sale_alerts_once():  # I1
    p = [play(sessions=[sess(status=Status.ON_SALE)])]
    st, ev = diff(empty_state(), p, NOWATCH, T0)
    assert kinds(ev) == ["NEW_PLAY"]
    later = T0 + timedelta(days=5)
    st, ev = diff(st, p, WATCH, later)
    assert [e.key for e in ev] == ["ON_SALE:s1:1"]
    st, ev = diff(st, p, WATCH, later + timedelta(minutes=5))
    assert ev == []
