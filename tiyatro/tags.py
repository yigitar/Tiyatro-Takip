from __future__ import annotations

from tiyatro.models import ISTANBUL, Play, PlayWatch

_DAYTIME_BEFORE_HOUR = 17
_CHILD_MAX_AGE = 12
_CHILD_MAX_PRICE = 20


def _tr_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def derive_tags(play: Play) -> set[str]:
    tags: set[str] = set()
    haystack = _tr_lower(f"{play.title or ''} {play.genre or ''}")
    if (
        (play.age_limit is not None and play.age_limit <= _CHILD_MAX_AGE)
        or (play.price_from is not None and play.price_from <= _CHILD_MAX_PRICE)
        or "çocuk" in haystack
    ):
        tags.add("çocuk")
    if any(s.start.astimezone(ISTANBUL).hour < _DAYTIME_BEFORE_HOUR for s in play.sessions):
        tags.add("gündüz")
    return tags


def apply_overrides(tags: set[str], watch: PlayWatch | None) -> set[str]:
    result = set(tags)
    if watch is None:
        return result
    for tag, enabled in watch.tag_overrides.items():
        if enabled is True:
            result.add(tag)
        elif enabled is False:
            result.discard(tag)
    return result
