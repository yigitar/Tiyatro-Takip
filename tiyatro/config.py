from __future__ import annotations

import copy
import re
from urllib.parse import urlsplit

from .models import Config, PlayWatch, Rule, Target

DEFAULT_SOURCE = (
    "https://biletinial.com/tr-tr/mekan/kocaeli-buyuksehir-belediyesi-sehir-tiyatrolari"
)
MIN_PEOPLE, MAX_PEOPLE = 1, 4
MAX_PORTALS = 5
MAX_EVENT_FOLLOW = 500
DEFAULT_PORTALS = [{"id": "basiskele", "name": "Başiskele Belediyesi rezervasyon",
                    "url": "https://rezervasyon.basiskele.bel.tr/etkinlikler",
                    "enabled": True}]

_PORTAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

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


def _portals_from(raw) -> list[dict]:
    """Validated copy of ``config.portals``; an absent key means the default portal."""
    if raw is None:
        return copy.deepcopy(DEFAULT_PORTALS)
    if not isinstance(raw, list):
        raise ValueError("portals bir liste olmali")
    if len(raw) > MAX_PORTALS:
        raise ValueError(f"en fazla {MAX_PORTALS} portal olabilir")
    out: list[dict] = []
    seen: set[str] = set()
    for p in raw:
        if not isinstance(p, dict):
            raise ValueError("portal bir nesne olmali")
        pid, name, url = p.get("id"), p.get("name"), p.get("url")
        enabled = p.get("enabled", True)
        if not isinstance(pid, str) or not _PORTAL_ID_RE.match(pid):
            raise ValueError(f"portal id gecersiz: {pid!r}")
        if pid in seen:
            raise ValueError(f"portal id tekrar ediyor: {pid}")
        seen.add(pid)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"portal adi bos olamaz: {pid}")
        parts = urlsplit(url) if isinstance(url, str) else None
        if parts is None or parts.scheme != "https" or not parts.netloc or not parts.hostname:
            raise ValueError(f"portal url https olmali: {pid}")
        if not isinstance(enabled, bool):
            raise ValueError(f"portal enabled true/false olmali: {pid}")
        out.append({"id": pid, "name": name, "url": url, "enabled": enabled})
    return out


def _event_follow_from(raw) -> dict[str, bool]:
    """Validated copy of ``config.event_follow`` (explicit per-event choices); absent = {}."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > MAX_EVENT_FOLLOW:
        raise ValueError("event_follow gecersiz")
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k or len(k) > 120 or not isinstance(v, bool):
            raise ValueError("event_follow girdisi gecersiz")
        out[k] = v
    return out


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
        portals=_portals_from(d.get("portals")),
        event_follow=_event_follow_from(d.get("event_follow")),
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
        "portals": [dict(p) for p in c.portals],
        "event_follow": dict(c.event_follow),
    }


def default_config() -> Config:
    return Config(plays={}, sources=[DEFAULT_SOURCE], paused=False, version=1,
                  portals=copy.deepcopy(DEFAULT_PORTALS))
