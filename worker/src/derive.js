// Derived list fields (Kitle, Tür, Zaman, Hedef, Rez. açılış) shared by the Telegram list and
// the item screens. Faithful port of tiyatro/tags.py (resolve_audience, resolve_genre,
// genre_from_text, time_of_day, audience_is_stated, genre_is_stated), tiyatro/srcevents.py
// (genre_of, audience_for, is_theatre, event_views) and pc/viewmodel.py (_row, event_row,
// _play_rez, _event_rez). tests/test_wave7_parity.py feeds the same state to both sides.
// Pure: no I/O, `now` is always passed in (ms since epoch).

const ISTANBUL_OFFSET_MS = 3 * 3600 * 1000; // Turkey has no DST (fixed UTC+3)
const DAYTIME_BEFORE_HOUR = 17;
const CHILD_MAX_AGE = 12;
const MONTHS_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
const DAY_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];

export const NO_TARGET_SHORT = "yok → tüm seanslar"; // pc/viewmodel.py NO_TARGET_SHORT

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// ---------------------------------------------------------------- text rules (tags.py)

export const trLower = (s) => String(s ?? "").replace(/İ/g, "i").replace(/I/g, "ı").toLowerCase();
const foldTr = (s) => trLower(s).replace(/ç/g, "c").replace(/ğ/g, "g").replace(/ş/g, "s")
  .replace(/ö/g, "o").replace(/ü/g, "u");

const AUDIENCE_WORDS = new Set(["çocuk", "yetişkin", "yetiskin"]);
const GENERIC_WORDS = new Set(["oyunu", "oyun", "oyunları", "tiyatro", "tiyatrosu", "etkinlik",
  "etkinliği", "gösteri", "gösterisi", "ve", "-"]);

const isInt = (v) => typeof v === "number" && Number.isInteger(v);

/** "Çocuk" / "Yetişkin" / "" (unknown); page wording, then "çocuk" tag / age, as tags.audience_of. */
export function audienceOf(genre, tags, age) {
  const low = trLower(genre);
  const child = low.includes("çocuk");
  const adult = low.includes("yetişkin") || low.includes("yetiskin");
  if (child !== adult) return child ? "Çocuk" : "Yetişkin";
  if (Array.isArray(tags) && tags.map(String).includes("çocuk")) return "Çocuk";
  if (isInt(age)) return age <= CHILD_MAX_AGE ? "Çocuk" : "Yetişkin";
  return "";
}

/** The genre proper: audience / generic words removed (tags.genre_label). */
export function genreLabel(genre) {
  return String(genre ?? "").split(/\s+/)
    .filter((w) => w && !AUDIENCE_WORDS.has(trLower(w)) && !GENERIC_WORDS.has(trLower(w))).join(" ");
}

/** The page's own genre/category wording names the audience (tags.audience_is_stated). */
export function audienceIsStated(genre) {
  const low = trLower(genre);
  return low.includes("çocuk") !== (low.includes("yetişkin") || low.includes("yetiskin"));
}

export const genreIsStated = (genre) => genreLabel(genre) !== "";

// Python's \b and \w are Unicode-aware, JS's are ASCII: spell them out. Patterns are the
// folded forms of tags._GENRE_KEYWORDS.
const W = "[\\p{L}\\p{N}_]";
const GENRE_KEYWORDS = [
  ["Müzikal", `m[uu]zikal${W}*`], ["Komedi", `komedi${W}*`], ["Dram", "dram(?:a|atik|ı)?"],
  ["Trajedi", `trajedi${W}*`], ["Monolog", `monolog${W}*`], ["Stand-up", "stand[\\s-]?up"],
  ["Operet", `operet${W}*`], ["Kabare", `kabare${W}*`],
].map(([label, pattern]) => [label, new RegExp(`(?<!${W})${pattern}(?!${W})`, "u")]);

/** Earliest genre keyword in the given texts (title, description…) or "". */
export function genreFromText(...texts) {
  const blob = foldTr(texts.map((t) => String(t ?? "")).join(" "));
  let best = "";
  let bestPos = null;
  for (const [label, re] of GENRE_KEYWORDS) {
    const m = re.exec(blob);
    if (m && (bestPos === null || m.index < bestPos)) {
      best = label;
      bestPos = m.index;
    }
  }
  return best;
}

// Source events that are clearly not theatre (tags.py other_event_label).
export const OTHER_EVENT_PREFIX = "Diğer";
const OTHER_EVENT_KINDS = [
  ["Diğer – Atölye", ["atölye", "workshop"]],
  ["Diğer – Söyleşi", ["söyleşi"]],
  ["Diğer – Film", ["film", "sinema"]],
  ["Diğer – Konser", ["konser"]],
  ["Diğer – Sergi", ["sergi"]],
  ["Diğer – Eğitim", ["eğitim", "kurs"]],
  ["Diğer – Seminer/Panel", ["seminer", "panel"]],
].map(([label, words]) => [label, new RegExp(`(?<!${W})(?:${words.map(foldTr).join("|")})`, "u")]);

/** "Diğer – <tür>" for the earliest non-theatre word starting a word of `texts`, else "". */
export function otherEventLabel(...texts) {
  const blob = foldTr(texts.map((t) => String(t ?? "")).join(" "));
  let best = "";
  let bestPos = null;
  for (const [label, re] of OTHER_EVENT_KINDS) {
    const m = re.exec(blob);
    if (m && (bestPos === null || m.index < bestPos)) {
      best = label;
      bestPos = m.index;
    }
  }
  return best;
}

export const hasNonTheatreWord = (...texts) => otherEventLabel(...texts) !== "";

/** Kitle with the stated fallback: unknown -> "Yetişkin" unless a child marker exists. */
export function resolveAudience(genre = "", tags = [], age = null, text = "") {
  return audienceOf(genre, tags, age) || (trLower(text).includes("çocuk") ? "Çocuk" : "Yetişkin");
}

/** Tür: the genre field, else an unambiguous keyword from `texts`, else "" (shown "—"; never "Tiyatro"). */
export function resolveGenre(genre, texts = []) {
  return genreLabel(genre) || genreFromText(...texts);
}

/** "gündüz" (a session before 17:00), "gece" (17:00 or later), both joined, "" without sessions. */
export function timeOfDay(hours) {
  const hrs = (hours ?? []).filter(isInt);
  const parts = [];
  if (hrs.some((x) => x < DAYTIME_BEFORE_HOUR)) parts.push("gündüz");
  if (hrs.some((x) => x >= DAYTIME_BEFORE_HOUR)) parts.push("gece");
  return parts.join("+");
}

export function isTheatre(kind, title) {
  const low = trLower(`${kind} ${title}`);
  return low.includes("tiyatro") || low.includes("müzikal");
}

/** Tür of a Başiskele/VKV event (srcevents.genre_of): genre, else "Diğer – <tür>" when clearly not theatre, else "". */
export function eventGenre(kind, title) {
  return resolveGenre(kind, [title])
    || (!isTheatre(kind, title) ? otherEventLabel(kind, title) : "");
}

// ---------------------------------------------------------------- time helpers

const parseMs = (iso) => (typeof iso === "string" ? Date.parse(iso) : NaN);
const local = (ms) => new Date(ms + ISTANBUL_OFFSET_MS); // read with the getUTC* methods
const pad2 = (n) => String(n).padStart(2, "0");
const hhmm = (ms) => `${pad2(local(ms).getUTCHours())}:${pad2(local(ms).getUTCMinutes())}`;
const dayNumber = (ms) => Math.floor((ms + ISTANBUL_OFFSET_MS) / 86400e3);

/** "bugün" / "yarın" / "3 gün sonra" / "geçti" by calendar day in Istanbul time. */
function relativeDay(ms, now) {
  const diff = dayNumber(ms) - dayNumber(now);
  if (diff < 0) return "geçti";
  return diff === 0 ? "bugün" : diff === 1 ? "yarın" : `${diff} gün sonra`;
}

/** "16 Kas Pzt 10:00 (yarın)" */
export function rezWhen(ms, now) {
  const d = local(ms);
  return `${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]} ${DAY_SHORT[(d.getUTCDay() + 6) % 7]} ${hhmm(ms)} (${relativeDay(ms, now)})`;
}

// ---------------------------------------------------------------- play / event fields

/** Upcoming (start >= now), non-gone sessions of a stored play: [{rec, start, opens}], by start. */
function upcomingSessions(entry, now) {
  const out = [];
  const sessions = isObj(entry?.sessions) ? entry.sessions : {};
  for (const rec of Object.values(sessions)) {
    if (!isObj(rec) || rec.gone) continue;
    const start = parseMs(rec.start);
    if (!Number.isFinite(start) || start < now) continue;
    out.push({ rec, start, opens: parseMs(rec.opens_at) });
  }
  return out.sort((a, b) => a.start - b.start);
}

/** ("Rez. açılış" text, derived?) of a play; ladder as pc/viewmodel.py _play_rez. */
function playRez(sessions, now) {
  let opening = null;
  let onSale = 0;
  let soldOut = 0;
  for (const { rec, opens } of sessions) {
    if (rec.status === "on_sale") onSale++;
    if (rec.status === "sold_out") soldOut++;
    if (rec.status === "upcoming" && Number.isFinite(opens) && opens > now) {
      opening = opening === null || opens < opening ? opens : opening;
    }
  }
  if (opening !== null) return { text: rezWhen(opening, now), derived: false, opening };
  if (onSale) return { text: "açık", derived: false, opening: null };
  if (sessions.length && soldOut === sessions.length) return { text: "tükendi", derived: false, opening: null };
  return { text: "—", derived: true, opening: null };
}

/** ("Rez. açılış" text, derived?) of a Başiskele/VKV event; pc/viewmodel.py _event_rez. */
function eventRez(rec, now) {
  const opens = parseMs(rec.opens_at);
  if (rec.full) return { text: "doldu", derived: false };
  if (!Number.isFinite(opens)) return { text: "duyuru bekleniyor", derived: true };
  if (now < opens) return { text: rezWhen(opens, now), derived: false };
  if (rec.opens_pending === false) return { text: "açık", derived: true };
  return { text: "açılmış olabilir", derived: true };
}

/**
 * Kitle / Tür / Zaman / Hedef / Rez. açılış of a stored play. `derived` lists the field keys
 * (audience, genre, zaman, target, rez) whose value is our rule, not page data.
 */
export function playFields(entry, watch, now) {
  const e = isObj(entry) ? entry : {};
  const sessions = upcomingSessions(e, now);
  const age = isInt(e.age_limit) ? e.age_limit : null;
  const explicit = Array.isArray(watch?.targets) ? watch.targets.filter(isObj).length : 0;
  const rez = playRez(sessions, now);
  const derived = [];
  if (sessions.length) derived.push("zaman");
  if (!audienceIsStated(e.genre)) derived.push("audience");
  if (!genreIsStated(e.genre)) derived.push("genre");
  if (!explicit) derived.push("target");
  if (rez.derived) derived.push("rez");
  return {
    kitle: resolveAudience(e.genre, e.tags ?? [], age),
    tur: resolveGenre(e.genre, [e.title, typeof e.summary === "string" ? e.summary.trim() : ""]),
    zaman: timeOfDay(sessions.map((s) => local(s.start).getUTCHours())),
    hedef: explicit ? `var (${explicit})` : NO_TARGET_SHORT,
    rez: rez.text,
    derived: derived.sort(),
    nearest: sessions.length ? sessions[0].start : null,
  };
}

/** The same five fields for a Başiskele/VKV event record (event_row). */
export function eventFields(rec, id, now, state) {
  const kind = String(rec.kind || "");
  const title = String(rec.title || id);
  const start = parseMs(rec.start);
  const rez = eventRez(rec, now);
  const derived = ["target"];
  const merged = borrow(eventInfo(rec), eventGenre(kind, title), state, title);
  if (Number.isFinite(start)) derived.push("zaman");
  if (!audienceIsStated(kind)) derived.push("audience");
  if (!genreIsStated(kind) || merged.borrowed.includes("genre")) derived.push("genre");
  if (rez.derived) derived.push("rez");
  return {
    kitle: resolveAudience(kind, [], null, `${kind} ${title}`),
    tur: merged.genre,
    zaman: Number.isFinite(start) ? timeOfDay([local(start).getUTCHours()]) : "",
    hedef: "—",
    rez: rez.text,
    derived: derived.sort(),
    nearest: Number.isFinite(start) ? start : null,
    ...merged.info,
    borrowed: merged.borrowed,
    borrowedCredits: merged.borrowedCredits,
    borrowedFrom: merged.borrowedFrom,
  };
}

/** pc/tiyatro/tags.clean_summary: LF only, odd spaces mapped, blank-line runs collapsed to one, trimmed. */
export function cleanSummary(text) {
  if (typeof text !== "string") return "";
  const t = text.replace(/\r\n?/g, "\n").replace(/[      ⁠]/g, " ")
    .replace(/[​‌‍﻿]/g, "");
  const out = [];
  for (const raw of t.split("\n")) {
    const line = raw.trim();
    if (!line && (!out.length || !out[out.length - 1])) continue;
    out.push(line);
  }
  while (out.length && !out[out.length - 1]) out.pop();
  return out.join("\n");
}

/**
 * Özet / Oyuncular / Yapım / Süre / Yaş / Ücret a source event record states (tiyatro/srcinfo.info_view):
 * type-checked, empty values when unknown. Never derived.
 */
export function eventInfo(rec) {
  const raw = isObj(rec?.info) ? rec.info : {};
  const str = (v) => (typeof v === "string" ? v.trim() : "");
  const int = (v) => (Number.isInteger(v) ? v : null);
  const dur = int(raw.duration_min);
  const age = int(raw.age_limit);
  return {
    ozet: cleanSummary(typeof raw.summary === "string" ? raw.summary : ""),
    oyuncular: (Array.isArray(raw.cast) ? raw.cast : []).map(str).filter(Boolean),
    yapim: isObj(raw.credits)
      ? Object.entries(raw.credits).map(([k, v]) => [str(k), str(v)]).filter(([k, v]) => k && v) : [],
    sure: dur === null ? "" : `${dur} dk`,
    yas: age === null ? "" : `${age}+`,
    ucret: str(raw.price),
  };
}

// ---------------------------------------------------------------- borrowing (tiyatro/srcinfo.py)

const BORROWABLE = ["summary", "cast", "credits", "duration", "age", "genre"];

/** srcinfo.norm_title: Turkish letters folded, lower case, punctuation dropped, spaces collapsed. */
export function normTitle(text) {
  return foldTr(text).replace(/ı/g, "i").replace(/[^0-9a-z]+/g, " ").trim().split(" ").filter(Boolean).join(" ");
}

/** [slug, entry] of the ONE stored play with the same normalised title; null when none / ambiguous. */
export function playMatch(state, title) {
  const key = normTitle(title);
  const plays = isObj(state?.plays) ? state.plays : {};
  if (!key) return null;
  const found = Object.entries(plays).filter(([, e]) => isObj(e) && normTitle(e.title) === key);
  return found.length === 1 ? found[0] : null;
}

/** pc/viewmodel.price_text. */
function priceText(v) {
  if (typeof v !== "number" || !Number.isFinite(v)) return "";
  return Number.isInteger(v) ? `${v} TL` : `${v.toFixed(2).replace(".", ",")} TL`;
}

/** Özet / Oyuncular / Yapım / Süre / Yaş / Ücret of a stored play (pc/viewmodel._row), same keys as an event item. */
export function playInfoFields(entry) {
  const e = isObj(entry) ? entry : {};
  const p = playInfo(e);
  return {
    ozet: p.ozet, oyuncular: p.oyuncular, yapim: p.yapim, sure: p.sure, yas: p.yas,
    ucret: priceText(e.price_from), venue: "", venueDerived: false,
    borrowed: [], borrowedCredits: [], borrowedFrom: "",
  };
}

/** srcinfo.play_info_view: the play's own Özet / Oyuncular / Yapım / Süre / Yaş / Tür. */
function playInfo(entry) {
  const str = (v) => (typeof v === "string" ? v.trim() : "");
  const dur = isInt(entry.duration_min) && entry.duration_min > 0 ? entry.duration_min : null;
  return {
    ozet: cleanSummary(entry.summary),
    oyuncular: (Array.isArray(entry.cast) ? entry.cast : []).map(str).filter(Boolean),
    yapim: isObj(entry.credits)
      ? Object.entries(entry.credits).map(([k, v]) => [str(k), str(v)]).filter(([k, v]) => k && v) : [],
    sure: dur === null ? "" : `${dur} dk`,
    yas: isInt(entry.age_limit) ? `${entry.age_limit}+` : "",
    genre: genreLabel(entry.genre),
  };
}

/**
 * srcinfo.borrow: fill what the event's own record does not state from the same-named play.
 * Own values win; credits merge per label (own first); nothing when missing / ambiguous.
 * Returns {info, genre, borrowed, borrowedCredits, borrowedFrom}.
 */
export function borrow(own, ownGenre, state, title) {
  const out = { info: own, genre: ownGenre, borrowed: [], borrowedCredits: [], borrowedFrom: "" };
  const hit = playMatch(state, title);
  if (!hit) return out;
  const [slug, entry] = hit;
  const play = playInfo(entry);
  const info = { ...own };
  const borrowed = [];
  for (const [key, field] of [["ozet", "summary"], ["oyuncular", "cast"], ["sure", "duration"], ["yas", "age"]]) {
    const empty = Array.isArray(own[key]) ? own[key].length === 0 : !own[key];
    const have = Array.isArray(play[key]) ? play[key].length > 0 : Boolean(play[key]);
    if (empty && have) { info[key] = play[key]; borrowed.push(field); }
  }
  const labels = new Set(own.yapim.map(([k]) => k.toLowerCase()));
  const extra = play.yapim.filter(([k]) => !labels.has(k.toLowerCase()));
  if (extra.length) {
    info.yapim = [...own.yapim, ...extra];
    borrowed.push("credits");
    out.borrowedCredits = extra.map(([k]) => k);
  }
  if (!ownGenre && play.genre) { out.genre = play.genre; borrowed.push("genre"); }
  if (borrowed.length) out.borrowedFrom = String(entry.title || slug);
  out.info = info;
  out.borrowed = borrowed.sort((a, b) => BORROWABLE.indexOf(a) - BORROWABLE.indexOf(b));
  return out;
}
