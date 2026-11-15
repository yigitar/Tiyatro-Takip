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


# ---- filling blanks (wave 5) ------------------------------------------------------------
# Rule (stated, documented in the README): a Kitle is "Çocuk" when the page/category says
# so (genre or kind wording, derived "çocuk" tag, age limit <= 12); otherwise "Yetişkin" --
# either because the page says so, the age limit is 13+, or there is no child marker at
# all. Tür is the genre proper; when the genre field holds only the audience, the title /
# description is searched for a genre keyword. With no genre word at all Tür stays blank
# ("—"): "Tiyatro" is never filled in (everything listed is theatre; it is not a genre).

_GENRE_KEYWORDS = (
    ("Müzikal", r"m[uü]zikal\w*"), ("Komedi", r"komedi\w*"), ("Dram", r"dram(?:a|atik|ı)?"),
    ("Trajedi", r"trajedi\w*"), ("Monolog", r"monolog\w*"), ("Stand-up", r"stand[\s-]?up"),
    ("Operet", r"operet\w*"), ("Kabare", r"kabare\w*"),
)  # only unambiguous genre words: plot words ("gerilim", "macera") are not genres


def _fold_tr(text: str) -> str:
    return _tr_lower(str(text or "")).translate(str.maketrans("çğşöü", "cgsou"))


def genre_from_text(*texts) -> str:
    """Earliest genre keyword in the given texts (title, description…) or ""."""
    import re
    blob = _fold_tr(" ".join(str(t or "") for t in texts))
    best, best_pos = "", None
    for label, pattern in _GENRE_KEYWORDS:
        m = re.search(rf"\b{_fold_tr(pattern)}\b", blob)
        if m and (best_pos is None or m.start() < best_pos):
            best, best_pos = label, m.start()
    return best


# Source events (Başiskele, VKV) that are clearly not theatre (wave 10): the category/title
# carries a plain non-theatre word. Only then is Tür "Diğer etkinlik" (always derived); an
# item without such a word keeps "—". Words that also name theatre ("tiyatro", "müzikal")
# veto it in srcevents.genre_of via is_theatre.
OTHER_EVENT_LABEL = "Diğer etkinlik"
_NON_THEATRE_WORDS = ("atölye", "söyleşi", "film", "sinema", "konser", "sergi", "eğitim",
                      "kurs", "seminer", "workshop", "panel")


def has_non_theatre_word(*texts) -> bool:
    """A non-theatre word (atölye, söyleşi, film, konser, sergi, eğitim, ...) starts a word
    of the given texts (Turkish-folded, prefix match: "Filmi", "Konseri")."""
    import re
    blob = _fold_tr(" ".join(str(t or "") for t in texts))
    words = "|".join(_fold_tr(w) for w in _NON_THEATRE_WORDS)
    return re.search(rf"(?<!\w)(?:{words})", blob) is not None


def resolve_audience(genre="", tags=(), age_limit=None, text="") -> str:
    """Kitle with the stated fallback: unknown -> "Yetişkin" unless a child marker exists.
    ``text`` (title/category) counts as a child marker only when it says "çocuk"."""
    found = audience_of(genre, tags, age_limit)
    if found:
        return found
    return CHILD_LABEL if "çocuk" in _tr_lower(str(text or "")) else ADULT_LABEL


def resolve_genre(genre="", *texts) -> str:
    """Tür: the genre field, else an unambiguous keyword from ``texts``, else "" (shown
    as "—"); never the generic "Tiyatro"."""
    return genre_label(genre) or genre_from_text(*texts)


def derived_tags(tags, audience: str, start_hour: int | None = None) -> list[str]:
    """Etiketler for display: the stored tags plus the audience ("çocuk"/"yetişkin") and
    "gündüz" (session before 17:00) when missing, so no row is blank."""
    out = [str(t) for t in tags or ()]
    for extra in (_tr_lower(audience or ""),
                  "gündüz" if start_hour is not None and start_hour < _DAYTIME_BEFORE_HOUR
                  else ""):
        if extra and extra not in out:
            out.append(extra)
    return out


# ---- provenance (wave 6) ------------------------------------------------------------------
# Which displayed values are read from the source page and which are produced by our own
# rules. "Verified" = the source states it in the field itself; "derived" = a rule, a
# fallback or a computation of ours (the PC list renders those italic, grey-blue).

def audience_is_stated(genre) -> bool:
    """True when the page's own genre/category wording names the audience ("Çocuk Oyunu",
    "Yetişkin Tiyatrosu"). Anything else (tag, age limit, title word, "no child marker ->
    Yetişkin") is our rule, i.e. derived."""
    low = _tr_lower(str(genre or ""))
    child, adult = "çocuk" in low, "yetişkin" in low or "yetiskin" in low
    return child != adult


def genre_is_stated(genre) -> bool:
    """True when the genre field itself holds a genre word after the audience/generic
    words are removed. A keyword found in the title/description is derived."""
    return bool(genre_label(genre))


def time_of_day(hours) -> str:
    """"gündüz" (session starting before 17:00), "gece" (17:00 or later) or both joined
    ("gündüz+gece"); "" without sessions. Computed from the session times, never read."""
    hrs = [h for h in hours or () if isinstance(h, int) and not isinstance(h, bool)]
    parts = []
    if any(h < _DAYTIME_BEFORE_HOUR for h in hrs):
        parts.append("gündüz")
    if any(h >= _DAYTIME_BEFORE_HOUR for h in hrs):
        parts.append("gece")
    return "+".join(parts)
