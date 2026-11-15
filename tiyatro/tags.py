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


# ---- audience (Kitle) vs genre (Tür) -------------------------------------------------
# Biletinial's "Etkinlik Türü" is sometimes the audience ("Çocuk Oyunu", "Yetişkin
# Oyunu") and sometimes the genre ("Komedi", "Fantastik"). The two are shown separately.

ADULT_LABEL = "Yetişkin"
CHILD_LABEL = "Çocuk"
_AUDIENCE_WORDS = {"çocuk", "yetişkin", "yetiskin"}
_GENERIC_WORDS = {"oyunu", "oyun", "oyunları", "tiyatro", "tiyatrosu", "etkinlik",
                  "etkinliği", "gösteri", "gösterisi", "ve", "-"}
_ADULT_MIN_AGE = 13


def audience_of(genre, tags=(), age_limit=None) -> str:
    """"Çocuk" / "Yetişkin" / "" (unknown). The page's own wording wins ("Çocuk Oyunu",
    "Yetişkin Oyunu"), then the derived "çocuk" tag / age limit <= 12 ("çocuk"), then an
    age limit of 13+ ("Yetişkin")."""
    low = _tr_lower(str(genre or ""))
    child, adult = "çocuk" in low, "yetişkin" in low or "yetiskin" in low
    if child != adult:
        return CHILD_LABEL if child else ADULT_LABEL
    if "çocuk" in {str(t) for t in tags or ()}:
        return CHILD_LABEL
    if isinstance(age_limit, int) and not isinstance(age_limit, bool):
        return CHILD_LABEL if age_limit <= _CHILD_MAX_AGE else ADULT_LABEL
    return ""


def genre_label(genre) -> str:
    """The genre proper ("Komedi", "Dram", "Müzikal"…): audience/generic words removed;
    "" when nothing is left ("Çocuk Oyunu")."""
    words = str(genre or "").split()
    kept = [w for w in words
            if _tr_lower(w) not in _AUDIENCE_WORDS and _tr_lower(w) not in _GENERIC_WORDS]
    return " ".join(kept)
