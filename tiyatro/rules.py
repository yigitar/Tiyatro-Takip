from __future__ import annotations

from datetime import datetime, time

from tiyatro.models import ISTANBUL, PlayWatch, Rule, Session, Target


def _parse(value: str | None) -> time | None:
    if value is None:
        return None
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _in_range(t: time, lo: str | None, hi: str | None) -> bool:
    """``from`` and ``to`` are both inclusive (minute precision): "<= 20:00" keeps the
    20:00 sessions."""
    start, end = _parse(lo), _parse(hi)
    if start is not None and t < start:
        return False
    if end is not None and t > end:
        return False
    return True


def rule_allows(rule: Rule, start: datetime) -> bool:
    local = start.astimezone(ISTANBUL)
    if local.date().isoformat() in rule.exclude_dates:
        return False
    weekday = local.weekday()
    t = local.time().replace(second=0, microsecond=0)
    if weekday in rule.overrides:
        ov = rule.overrides[weekday] or {}
        return _in_range(t, ov.get("from"), ov.get("to"))
    if rule.days is None or weekday in rule.days:
        return _in_range(t, rule.from_time, rule.to_time)
    return False


def matching_targets(watch: PlayWatch, session: Session) -> list[Target]:
    if not watch.active:
        return []
    return [t for t in watch.targets if rule_allows(t.rule, session.start)]
