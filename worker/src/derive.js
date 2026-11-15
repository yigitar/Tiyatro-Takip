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
export function eventFields(rec, id, now) {
  const kind = String(rec.kind || "");
  const title = String(rec.title || id);
  const start = parseMs(rec.start);
  const rez = eventRez(rec, now);
  const derived = ["target"];
  if (Number.isFinite(start)) derived.push("zaman");
  if (!audienceIsStated(kind)) derived.push("audience");
  if (!genreIsStated(kind)) derived.push("genre");
  if (rez.derived) derived.push("rez");
  return {
    kitle: resolveAudience(kind, [], null, `${kind} ${title}`),
    tur: resolveGenre(kind, [title]),
    zaman: Number.isFinite(start) ? timeOfDay([local(start).getUTCHours()]) : "",
    hedef: "—",
    rez: rez.text,
    derived: derived.sort(),
    nearest: Number.isFinite(start) ? start : null,
  };
}
