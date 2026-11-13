from __future__ import annotations

import re

from .models import Config, PlayWatch, Rule, Target

DEFAULT_SOURCE = (
    "https://biletinial.com/tr-tr/mekan/kocaeli-buyuksehir-belediyesi-sehir-tiyatrolari"
)
MIN_PEOPLE, MAX_PEOPLE = 1, 4

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _check_time(value, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TIME_RE.match(value):
        raise ValueError(f"{label}: gecersiz saat {value!r} (HH:MM bekleniyor)")
    return value


def _rule_from_dict(d: dict) -> Rule:
    days = d.get("days")
    if days is not None:
        days = [int(x) for x in days]
        if any(x < 0 or x > 6 for x in days):
            raise ValueError(f"gun 0-6 araliginda olmali: {days}")
    overrides: dict[int, dict] = {}
    for k, v in (d.get("overrides") or {}).items():
        wd = int(k)
        if wd < 0 or wd > 6:
            raise ValueError(f"override gunu 0-6 araliginda olmali: {k}")
        overrides[wd] = {
            "from": _check_time(v.get("from"), "override from"),
            "to": _check_time(v.get("to"), "override to"),
        }
    return Rule(
        days=days,
        from_time=_check_time(d.get("from_time"), "from_time"),
        to_time=_check_time(d.get("to_time"), "to_time"),
        overrides=overrides,
        exclude_dates=list(d.get("exclude_dates") or []),
    )


def _rule_to_dict(r: Rule) -> dict:
    return {
        "days": None if r.days is None else list(r.days),
        "from_time": r.from_time,
        "to_time": r.to_time,
        "overrides": {str(k): dict(v) for k, v in r.overrides.items()},
        "exclude_dates": list(r.exclude_dates),
    }


def _target_from_dict(d: dict) -> Target:
    people = int(d["people"])
    if not MIN_PEOPLE <= people <= MAX_PEOPLE:
        raise ValueError(f"people {MIN_PEOPLE}-{MAX_PEOPLE} araliginda olmali: {people}")
    return Target(
        id=str(d["id"]),
        name=str(d["name"]),
        people=people,
        rule=_rule_from_dict(d.get("rule") or {}),
    )


def config_from_dict(d: dict) -> Config:
    plays: dict[str, PlayWatch] = {}
    for slug, w in (d.get("plays") or {}).items():
        plays[slug] = PlayWatch(
            active=bool(w.get("active", True)),
            targets=[_target_from_dict(t) for t in w.get("targets") or []],
            tag_overrides={k: bool(v) for k, v in (w.get("tag_overrides") or {}).items()},
        )
    return Config(
        plays=plays,
        sources=list(d.get("sources") or [DEFAULT_SOURCE]),
        paused=bool(d.get("paused", False)),
        version=int(d.get("version", 1)),
    )


def config_to_dict(c: Config) -> dict:
    return {
        "plays": {
            slug: {
                "active": w.active,
                "targets": [
                    {
                        "id": t.id,
                        "name": t.name,
                        "people": t.people,
                        "rule": _rule_to_dict(t.rule),
                    }
                    for t in w.targets
                ],
                "tag_overrides": dict(w.tag_overrides),
            }
            for slug, w in c.plays.items()
        },
        "sources": list(c.sources),
        "paused": c.paused,
        "version": c.version,
    }


def default_config() -> Config:
    return Config(plays={}, sources=[DEFAULT_SOURCE], paused=False, version=1)
