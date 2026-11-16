"""Özet / Oyuncular / Yapım (+ Süre / Yaş / Ücret) of source events (wave 15).

Only what the source page really states is extracted; nothing is derived or invented.
What the two sources provide (checked on the live pages, 2026-11-16; see the wave 15
report): VKV event pages carry descriptive paragraphs (no cast / crew / duration / age /
price lines on any of the 13 pages read); Başiskele program articles carry per-event
schedule sentences or table lines only. The labelled-line extraction (``Oyuncular:``,
``Yönetmen:`` ...) is therefore ready for pages that have such lines, but is exercised
on fixtures only.

A record stores ``info`` (dict, keys only when found: ``summary``, ``cast``, ``credits``,
``duration_min``, ``age_limit``, ``price``) and ``info_hash`` (sha1 of the normalised info);
the info is rewritten only when the hash changes.
"""
from __future__ import annotations

import hashlib
import json
import re

from tiyatro.parser import SUMMARY_MAX, _cap_summary, _cast_name, tr_title
from tiyatro.tags import clean_summary, genre_label

_CAST_LABELS = ("oyuncular", "oyuncu kadrosu", "oynayanlar", "roller", "rol dagilimi",
                "rollerde", "kadro", "sahnede")
_CREDIT_LABELS = ("yonetmen", "yoneten", "yazan", "yazar", "oyun yazari", "metin", "reji",
                  "rejisor", "uyarlama", "ceviri", "ceviren", "dramaturg", "dramaturji",
                  "muzik", "koreografi", "dekor", "sahne tasarimi", "kostum", "isik",
                  "isik tasarimi", "yapimci", "yapim", "yardimci yonetmen", "yazan ve yoneten")
_TR = str.maketrans({"ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i", "ö": "o",
                     "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"})


def _fold(text: str) -> str:
    return text.translate(_TR).lower()


_LABEL_LINE = re.compile(r"^\s*([^:：\n]{2,32}?)\s*[:：]\s*(.+)$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÇĞİÖŞÜ“‘\"'0-9])")
_BOILER = re.compile(r"rezervasyon|iletisim|telefon|\(0\d{3}\)|@|fotograf galeri|^paylas$")
_DURATION = re.compile(r"(?<![\d.])(\d{2,3})\s*(?:dk\b|dk\.|dakika)", re.I)
_AGE = re.compile(r"(?:yaş\s*sınırı|yaş\s*aralığı|yaş)\s*[:：]?\s*(\d{1,2})\s*\+"
                  r"|(?<![\d.])(\d{1,2})\s*\+\s*yaş|(?<![\d.])(\d{1,2})\s*yaş(?:\s*ve\s*üzeri| üstü)",
                  re.I)
_PRICE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-Za-z])", re.I)


def facts_of(text: str) -> dict:
    """Süre / Yaş / Ücret only when the text states them explicitly ("90 dk", "7+ yaş",
    "250 TL", "ücretsiz"). Values are quoted, never guessed."""
    out: dict = {}
    if not isinstance(text, str):
        return out
    m = _DURATION.search(text)
    if m and 10 <= int(m.group(1)) <= 400:
        out["duration_min"] = int(m.group(1))
    m = _AGE.search(text)
    if m:
        out["age_limit"] = int(next(g for g in m.groups() if g))
    m = _PRICE.search(text)
    if m:
        out["price"] = f"{m.group(1)} TL"
    elif re.search(r"(?<![\w])ücretsiz(?![\w])", text, re.I):
        out["price"] = "Ücretsiz"
    return out


def _names(text: str) -> list[str]:
    parts = re.split(r"\s*[,;]\s*|\s+&\s+|\s+ve\s+", text)
    return [_cast_name(p) for p in parts if p.strip()]


def split_labelled(lines: list[str]) -> tuple[list[str], list[str], dict[str, str]]:
    """(prose lines, cast, credits): a line ``Oyuncular: A, B`` / ``Yönetmen: C`` is cast /
    credits (label whitelist; unknown labels stay prose, nothing is guessed)."""
    prose, cast, credits = [], [], {}
    for line in lines:
        m = _LABEL_LINE.match(line)
        if m:
            label, rest = _fold(m.group(1)).strip(), m.group(2).strip()
            if label in _CAST_LABELS:
                cast.extend(_names(rest))
                continue
            if label in _CREDIT_LABELS:
                credits.setdefault(m.group(1).strip().rstrip(":"), tr_title(rest))
                continue
        prose.append(line)
    return prose, cast, credits


def _drop_boilerplate(line: str) -> str:
    """The line without reservation / contact sentences ("" when nothing is left)."""
    kept = [s for s in _SENTENCE_SPLIT.split(line) if not _BOILER.search(_fold(s).strip())]
    return " ".join(kept).strip()


def info_from_lines(lines: list[str]) -> dict:
    """Info of the descriptive lines of an event page (already cut to the content block)."""
    lines = [ln for ln in (_drop_boilerplate(x.strip()) for x in lines) if ln]
    prose, cast, credits = split_labelled(lines)
    info: dict = {}
    if prose:
        info["summary"] = _cap_summary("\n\n".join(prose))
    seen: set[str] = set()
    cast = [n for n in cast if not (n.casefold() in seen or seen.add(n.casefold()))]
    if cast:
        info["cast"] = cast
    if credits:
        info["credits"] = credits
    info.update(facts_of("\n".join(prose) + "\n" + "\n".join(
        f"{k}: {v}" for k, v in credits.items())))
    return info


def info_hash(info: dict) -> str:
    return hashlib.sha1(json.dumps(info, sort_keys=True, ensure_ascii=False).encode("utf-8")
                        ).hexdigest()[:16]


def info_view(rec: dict) -> dict:
    """Normalised, type-checked info of a stored event record (hand-edited state safe):
    ``summary`` (str), ``cast`` (list[str]), ``credits`` (list[(label, value)]),
    ``duration`` ("90 dk"), ``age`` ("7+"), ``price`` (str); empty values when unknown."""
    raw = rec.get("info") if isinstance(rec, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    cast = raw.get("cast")
    credits = raw.get("credits")
    dur, age = raw.get("duration_min"), raw.get("age_limit")
    return {
        "summary": raw["summary"] if isinstance(raw.get("summary"), str) else "",
        "cast": [c.strip() for c in cast if isinstance(c, str) and c.strip()]
        if isinstance(cast, list) else [],
        "credits": [(k, v.strip()) for k, v in credits.items()
                    if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()]
        if isinstance(credits, dict) else [],
        "duration": f"{dur} dk" if isinstance(dur, int) and not isinstance(dur, bool) else "",
        "age": f"{age}+" if isinstance(age, int) and not isinstance(age, bool) else "",
        "price": raw["price"].strip() if isinstance(raw.get("price"), str) else "",
    }


# ---- borrowing from the same-named Biletinial play (wave 17) -------------------------------------
BORROWABLE = ("summary", "cast", "credits", "duration", "age", "genre")


def norm_title(text) -> str:
    """Title for an exact-name comparison: Turkish letters folded, lower case, punctuation
    dropped, spaces collapsed ("UNUTMAK!" == "Unutmak"). Empty when nothing alphanumeric is left."""
    folded = _fold(str(text or "")).replace("İ", "i")
    return " ".join(re.sub(r"[^0-9a-z]+", " ", folded).split())


def play_match(state: dict | None, title) -> tuple[str, dict] | None:
    """(slug, entry) of the ONE stored play whose normalised title equals ``title``'s; None when
    there is none, the title is empty, or more than one play has that title (ambiguous)."""
    key = norm_title(title)
    plays = (state or {}).get("plays")
    if not key or not isinstance(plays, dict):
        return None
    found = [(slug, e) for slug, e in plays.items()
             if isinstance(e, dict) and norm_title(e.get("title")) == key]
    return found[0] if len(found) == 1 else None


def play_info_view(entry: dict) -> dict:
    """``info_view`` shape of a stored play (type-checked): summary, cast, credits, duration, age
    and ``genre`` (only a genre the play itself states)."""
    cast, credits = entry.get("cast"), entry.get("credits")
    dur, age = entry.get("duration_min"), entry.get("age_limit")
    return {
        "summary": clean_summary(entry.get("summary")),
        "cast": [c.strip() for c in cast if isinstance(c, str) and c.strip()]
        if isinstance(cast, list) else [],
        "credits": [(k, v.strip()) for k, v in credits.items()
                    if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()]
        if isinstance(credits, dict) else [],
        "duration": f"{dur} dk" if isinstance(dur, int) and not isinstance(dur, bool) and dur > 0 else "",
        "age": f"{age}+" if isinstance(age, int) and not isinstance(age, bool) else "",
        "genre": genre_label(entry.get("genre")),
    }


def borrow(own: dict, own_genre: str, state: dict | None, title) -> dict:
    """Fill what the event's own page does not state from the same-named play.

    Returns ``{"info": merged info_view, "genre": str, "borrowed": [field...],
    "borrowed_credit_labels": [labels], "borrowed_from": {"slug", "title"} | None}``. An own
    value always wins; credits are merged per label (own entries first). Nothing is borrowed
    when the match is missing or ambiguous."""
    out = {"info": own, "genre": own_genre, "borrowed": [], "borrowed_credit_labels": [],
           "borrowed_from": None}
    hit = play_match(state, title)
    if hit is None:
        return out
    slug, entry = hit
    play = play_info_view(entry)
    info = dict(own)
    borrowed: list[str] = []
    for key in ("summary", "cast", "duration", "age"):
        if not info.get(key) and play[key]:
            info[key] = play[key]
            borrowed.append(key)
    labels = {k.casefold() for k, _v in own["credits"]}
    extra = [(k, v) for k, v in play["credits"] if k.casefold() not in labels]
    if extra:
        info["credits"] = list(own["credits"]) + extra
        borrowed.append("credits")
        out["borrowed_credit_labels"] = [k for k, _v in extra]
    if not own_genre and play["genre"]:
        out["genre"] = play["genre"]
        borrowed.append("genre")
    if borrowed:
        out["borrowed_from"] = {"slug": slug, "title": str(entry.get("title") or slug)}
    order = {k: i for i, k in enumerate(BORROWABLE)}
    out["info"], out["borrowed"] = info, sorted(borrowed, key=order.get)
    return out


__all__ = ["facts_of", "split_labelled", "info_from_lines", "info_hash", "info_view",
           "SUMMARY_MAX", "norm_title", "play_match", "play_info_view", "borrow", "BORROWABLE"]
