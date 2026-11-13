from datetime import datetime, timezone

from tiyatro.models import ISTANBUL, PlayWatch, Rule, Session, Status, Target
from tiyatro.rules import matching_targets, rule_allows


def dt(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ISTANBUL)


def test_days_weekend():
    r = Rule(days=[5, 6])
    assert rule_allows(r, dt(2026, 11, 14, 20))  # Sat
    assert rule_allows(r, dt(2026, 11, 15, 20))  # Sun
    assert not rule_allows(r, dt(2026, 11, 13, 20))  # Fri


def test_from_time_inclusive():
    r = Rule(days=None, from_time="18:00")
    assert not rule_allows(r, dt(2026, 11, 13, 17, 59))
    assert rule_allows(r, dt(2026, 11, 13, 18, 0))


def test_to_time_exclusive():
    r = Rule(to_time="21:00")
    assert not rule_allows(r, dt(2026, 11, 13, 21, 0))
    assert rule_allows(r, dt(2026, 11, 13, 20, 59))


def test_overrides_replace_general_range():
    r = Rule(days=None, from_time=None,
             overrides={2: {"from": "18:00", "to": None}, 3: {"from": "20:00", "to": None}})
    assert rule_allows(r, dt(2026, 11, 11, 18, 30))  # Wed
    assert not rule_allows(r, dt(2026, 11, 12, 19, 0))  # Thu
    assert rule_allows(r, dt(2026, 11, 12, 20, 0))
    assert rule_allows(r, dt(2026, 11, 13, 10, 0))  # Fri general


def test_override_day_allowed_even_if_not_in_days():
    r = Rule(days=[2], overrides={3: {"from": "20:00"}})
    assert rule_allows(r, dt(2026, 11, 12, 20, 0))  # Thu via override
    assert not rule_allows(r, dt(2026, 11, 10, 20, 0))  # Tue
    r2 = Rule(days=[2, 3], overrides={3: {"from": "20:00"}})
    assert not rule_allows(r2, dt(2026, 11, 10, 20, 0))  # Tue not listed


def test_override_replaces_general_to_time():
    r = Rule(days=None, from_time="10:00", to_time="12:00",
             overrides={4: {"from": "20:00", "to": None}})
    assert rule_allows(r, dt(2026, 11, 13, 22, 0))
    assert not rule_allows(r, dt(2026, 11, 13, 11, 0))


def test_exclude_dates_win():
    r = Rule(exclude_dates=["2026-12-18"], overrides={4: {"from": None, "to": None}})
    assert not rule_allows(r, dt(2026, 12, 18, 20))
    assert rule_allows(r, dt(2026, 12, 19, 20))


def test_other_timezone_converted_to_istanbul():
    r = Rule(from_time="18:00")
    utc = datetime(2026, 11, 13, 15, 30, tzinfo=timezone.utc)  # 18:30 Istanbul
    assert rule_allows(r, utc)
    utc2 = datetime(2026, 11, 13, 14, 30, tzinfo=timezone.utc)  # 17:30 Istanbul
    assert not rule_allows(r, utc2)
    # date crossing: 22:30 UTC Dec 17 = 01:30 Dec 18 Istanbul
    r3 = Rule(exclude_dates=["2026-12-18"])
    assert not rule_allows(r3, datetime(2026, 12, 17, 22, 30, tzinfo=timezone.utc))


def _sess(start):
    return Session("s", start, None, Status.UPCOMING, None, "u", "v")


def test_matching_targets():
    t1 = Target("a", "A", 2, Rule(days=[5]))
    t2 = Target("b", "B", 1, Rule(from_time="18:00"))
    t3 = Target("c", "C", 1, Rule(days=[0]))
    w = PlayWatch(True, [t1, t2, t3])
    s = _sess(dt(2026, 11, 14, 20))
    assert matching_targets(w, s) == [t1, t2]
    w.active = False
    assert matching_targets(w, s) == []
