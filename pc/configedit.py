"""In-place ``config.json`` mutations with the same semantics as worker/src/menu.js.

Each function edits a raw config dict (as stored, unknown fields untouched) and raises
``ValueError`` for invalid input; ``Service`` runs them inside ``Store.update_json`` and
validates the result with ``tiyatro.config.config_from_dict``. All are set operations,
so applying one twice equals applying it once.

Parity with the Telegram menu: ``ta`` -> set_active (activating a play without targets
adds the default target: ``c``/"Çocuk" if the play's state tags contain "çocuk", else
``y``/"Yetişkin"; 2 people, any time), ``tg`` -> ensure_target,
``hp`` -> set_people, ``hd wd/we/all`` -> set_days_group, ``hd d<n>`` -> set_day,
``hf``/``ht`` -> set_hours, ``hx`` -> set_excluded_date, ``hs rm`` -> remove_target,
``pz`` -> set_paused. ``set_days`` and ``set_day_override`` are PC-only (rule
``overrides``); seven selected days normalise to ``null`` (= every day) as in the menu.
"""
from __future__ import annotations

import re
from datetime import date

from tiyatro.config import MAX_PEOPLE, MIN_PEOPLE

KEEP = object()  # set_hours: leave this side unchanged
KINDS = {"y": "Yetişkin", "c": "Çocuk"}
DAY_GROUPS = {"wd": [0, 1, 2, 3, 4], "we": [5, 6], "all": None,
              "hafta içi": [0, 1, 2, 3, 4], "hafta sonu": [5, 6], "hepsi": None}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---- argument checks -------------------------------------------------------------

def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} boş olamaz")
    return value


def _int(value, lo: int, hi: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{label} {lo}-{hi} arasında bir tam sayı olmalı: {value!r}")
    return value


def check_time(value, label: str = "saat") -> str | None:
    """None, an hour 0-23 (-> "HH:00", like the menu's ``hf 18``) or "HH:MM"."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{_int(value, 0, 23, label):02d}:00"
    if isinstance(value, str) and _TIME.match(value):
        return value
    raise ValueError(f"{label}: geçersiz saat {value!r} (SS:DD bekleniyor)")


def check_date(value) -> str:
    try:
        if isinstance(value, str) and len(value) == 10:
            return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    raise ValueError(f"geçersiz tarih {value!r} (YYYY-AA-GG bekleniyor)")


def _days(days) -> list[int] | None:
    if days is None:
        return None
    if not isinstance(days, (list, tuple, set)):
        raise ValueError(f"günler liste olmalı: {days!r}")
    out = sorted({_int(d, 0, 6, "gün") for d in days})
    return None if len(out) == 7 else out


# ---- structure helpers (mirror ensurePlay / ruleOf / findTarget) ---------------------

def _empty_rule() -> dict:
    return {"days": None, "from_time": None, "to_time": None, "overrides": {},
            "exclude_dates": []}


def ensure_play(cfg: dict, slug: str) -> dict:
    _text(slug, "oyun")
    if not isinstance(cfg.get("plays"), dict):
        cfg["plays"] = {}
    if not isinstance(cfg["plays"].get(slug), dict):
        cfg["plays"][slug] = {"active": True, "targets": [], "tag_overrides": {}}
    watch = cfg["plays"][slug]
    if not isinstance(watch.get("targets"), list):
        watch["targets"] = []
    return watch


def find_target(cfg: dict, slug: str, tid: str) -> dict:
    _text(slug, "oyun")
    _text(tid, "hedef")
    watch = (cfg.get("plays") or {}).get(slug)
    targets = watch.get("targets") if isinstance(watch, dict) else None
    for t in targets if isinstance(targets, list) else []:
        if isinstance(t, dict) and t.get("id") == tid:
            return t
    raise ValueError(f"hedef bulunamadı: {slug}/{tid}")


def rule_of(t: dict) -> dict:
    if not isinstance(t.get("rule"), dict):
        t["rule"] = _empty_rule()
    return t["rule"]


# ---- operations ------------------------------------------------------------------

def set_active(cfg: dict, slug: str, active: bool, tags=()) -> None:
    """``tags``: the play's tags from state.json (picks the default target kind)."""
    watch = ensure_play(cfg, slug)
    watch["active"] = bool(active)
    if active and not watch["targets"]:  # an active play without a target never alerts
        ensure_target(cfg, slug, "c" if "çocuk" in (tags or ()) else "y")


def ensure_target(cfg: dict, slug: str, kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"hedef türü 'y' ya da 'c' olmalı: {kind!r}")
    try:
        find_target(cfg, slug, kind)
        return  # already there: no-op
    except ValueError:
        pass
    ensure_play(cfg, slug)["targets"].append(
        {"id": kind, "name": KINDS[kind], "people": 2, "rule": _empty_rule()})


def set_people(cfg: dict, slug: str, tid: str, n: int) -> None:
    n = _int(n, MIN_PEOPLE, MAX_PEOPLE, "kişi sayısı")
    find_target(cfg, slug, tid)["people"] = n


def set_days(cfg: dict, slug: str, tid: str, days) -> None:
    days = _days(days)
    rule_of(find_target(cfg, slug, tid))["days"] = days


def set_days_group(cfg: dict, slug: str, tid: str, group: str) -> None:
    if group not in DAY_GROUPS:
        raise ValueError(f"gün grubu wd/we/all olmalı: {group!r}")
    days = DAY_GROUPS[group]
    rule_of(find_target(cfg, slug, tid))["days"] = None if days is None else list(days)


def set_day(cfg: dict, slug: str, tid: str, weekday: int, on: bool) -> None:
    weekday = _int(weekday, 0, 6, "gün")
    rule = rule_of(find_target(cfg, slug, tid))
    current = rule.get("days")
    days = set(range(7) if current is None else current)
    if on:
        days.add(weekday)
    else:
        days.discard(weekday)
    rule["days"] = _days(days)


def set_hours(cfg: dict, slug: str, tid: str, from_time=KEEP, to_time=KEEP) -> None:
    values = {}
    if from_time is not KEEP:
        values["from_time"] = check_time(from_time, "başlangıç saati")
    if to_time is not KEEP:
        values["to_time"] = check_time(to_time, "bitiş saati")
    rule_of(find_target(cfg, slug, tid)).update(values)


def set_day_override(cfg: dict, slug: str, tid: str, weekday: int, from_time,
                     to_time) -> None:
    """Own hour window for one weekday; both None removes the override."""
    weekday = _int(weekday, 0, 6, "gün")
    lo, hi = check_time(from_time, "başlangıç saati"), check_time(to_time, "bitiş saati")
    rule = rule_of(find_target(cfg, slug, tid))
    overrides = rule.get("overrides") if isinstance(rule.get("overrides"), dict) else {}
    overrides = dict(overrides)
    if lo is None and hi is None:
        overrides.pop(str(weekday), None)
    else:
        overrides[str(weekday)] = {"from": lo, "to": hi}
    rule["overrides"] = overrides


def set_excluded_date(cfg: dict, slug: str, tid: str, iso_date: str,
                      excluded: bool) -> None:
    day = check_date(iso_date)
    rule = rule_of(find_target(cfg, slug, tid))
    cur = rule.get("exclude_dates") if isinstance(rule.get("exclude_dates"), list) else []
    rule["exclude_dates"] = (sorted(set(cur) | {day}) if excluded
                             else [d for d in cur if d != day])


def remove_target(cfg: dict, slug: str, tid: str) -> None:
    _text(slug, "oyun")
    _text(tid, "hedef")
    watch = (cfg.get("plays") or {}).get(slug)
    if isinstance(watch, dict) and isinstance(watch.get("targets"), list):
        kept = [t for t in watch["targets"] if not (isinstance(t, dict) and t.get("id") == tid)]
        if len(kept) != len(watch["targets"]):
            watch["targets"] = kept


def set_paused(cfg: dict, paused: bool) -> None:
    cfg["paused"] = bool(paused)
