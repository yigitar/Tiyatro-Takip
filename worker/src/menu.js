// Pure menu logic for the Telegram bot: routing, rendering and config mutation.
// No I/O here. `apply` and `render` never throw and never mutate their inputs.
//
// Rule semantics mirror tiyatro/rules.py: days 0=Monday..6=Sunday (null = every day),
// from_time and to_time both inclusive ("≤20:00" keeps 20:00 sessions), exclude_dates are
// Europe/Istanbul ISO dates.
// Routes (callback_data, each <= 64 bytes):
//   m | pl:<page> | p:<h> | ts:<h>:<id> (target screen) | ta:<h>:<0|1> | tg:<h>:<y|c>
//   hp:<h>:<id>:<n> | hd:<h>:<id>:<wd|we|all|d0..d6>[:<0|1>] | hf:<h>:<id>:<HH|none>
//   ht:<h>:<id>:<HH|none> | hx:<h>:<id>:<yymmdd>:<0|1> (date, stateless) | hs:<h>:<id>:rm | tl | up | rf | pz:<0|1>

const PAGE_SIZE = 6;
const MAX_CALLBACK_BYTES = 64;
const ISTANBUL_OFFSET_MS = 3 * 3600 * 1000; // Turkey has no DST (fixed UTC+3)
const DAY_NAMES = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const FROM_HOURS = [12, 14, 16, 18, 19, 20];
const TO_HOURS = [16, 18, 20, 22];
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const NO_TARGET_TEXT = "⚠️ hedef yok → bildirim gelmez"; // as pc/viewmodel.py
const KINDS = { y: ["y", "Yetişkin"], c: ["c", "Çocuk"] };

const encoder = new TextEncoder();

// ---------------------------------------------------------------- hashing

/** FNV-1a 32-bit over the UTF-8 bytes of `slug`, as 8 lowercase hex chars. */
export function h(slug) {
  let hash = 0x811c9dc5;
  for (const byte of encoder.encode(String(slug))) {
    hash ^= byte;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function hex8(n) {
  return (n >>> 0).toString(16).padStart(8, "0");
}

/**
 * Builds the slug <-> hash maps for the current slug set. Slugs are visited in sorted
 * order; the first one keeps its plain hash, a later slug whose hash is already taken is
 * linearly probed (hash + 1, + 2, ... modulo 2^32). The result depends only on the set of
 * slugs, so it is identical in render() and apply(). (A new play colliding with an old one
 * could in theory shift the old play's hash; at 2^-32 per pair this is accepted.)
 */
export function buildIndex(slugs, hashFn = h) {
  const hashOf = new Map();
  const slugOf = new Map();
  for (const slug of [...new Set(slugs)].sort()) {
    let hash = hashFn(slug);
    while (slugOf.has(hash)) hash = hex8(parseInt(hash, 16) + 1);
    hashOf.set(slug, hash);
    slugOf.set(hash, slug);
  }
  return { hashOf, slugOf };
}

// ---------------------------------------------------------------- helpers

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const cfgPlays = (config) => (isObj(config) && isObj(config.plays) ? config.plays : {});
const statePlays = (state) => (isObj(state) && isObj(state.plays) ? state.plays : {});

function indexFor(config, state) {
  return buildIndex([...Object.keys(cfgPlays(config)), ...Object.keys(statePlays(state))]);
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;");
}

function trunc(s, n) {
  const chars = [...String(s)];
  return chars.length > n ? `${chars.slice(0, n - 1).join("")}…` : String(s);
}

function istanbul(iso) {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : new Date(ms + ISTANBUL_OFFSET_MS);
}

function isoDate(iso) {
  const d = istanbul(iso);
  return d ? d.toISOString().slice(0, 10) : null;
}

function fmt(iso) {
  const d = istanbul(iso);
  if (!d) return "?";
  const s = d.toISOString(); // YYYY-MM-DDTHH:MM...
  return `${s.slice(8, 10)}.${s.slice(5, 7)}.${s.slice(0, 4)} ${s.slice(11, 16)}`;
}

function fmtShort(iso) {
  const d = istanbul(iso);
  if (!d) return "?";
  const s = d.toISOString();
  return `${s.slice(8, 10)}.${s.slice(5, 7)} ${s.slice(11, 16)}`;
}

/** Non-gone sessions of a play with a parseable start, sorted by start (then id). */
function sessionList(state, slug) {
  const sessions = statePlays(state)[slug]?.sessions;
  if (!isObj(sessions)) return [];
  return Object.entries(sessions)
    .filter(([, rec]) => isObj(rec) && !rec.gone && Number.isFinite(Date.parse(rec.start)))
    .sort(([ia, a], [ib, b]) => Date.parse(a.start) - Date.parse(b.start) || (ia < ib ? -1 : ia > ib ? 1 : 0));
}

function playTitle(state, slug) {
  return statePlays(state)[slug]?.title || slug;
}

function findTarget(config, slug, id) {
  const targets = cfgPlays(config)[slug]?.targets;
  return Array.isArray(targets) ? targets.find((t) => isObj(t) && t.id === id) : undefined;
}

function daysText(days) {
  if (days == null) return "her gün";
  const set = [...days].sort((a, b) => a - b);
  if (set.length === 0) return "hiçbir gün";
  if (set.join() === "0,1,2,3,4") return "hafta içi";
  if (set.join() === "5,6") return "hafta sonu";
  return set.map((d) => DAY_NAMES[d]).join(", ");
}

// Turkish dative suffix of a clock time as read aloud ("20:00" -> "yirmi" -> 'ye); minute
// 0 reads the hour. Same table as until_text in pc/viewmodel.py.
const UNIT_SUFFIX = ["", "e", "ye", "e", "e", "e", "ya", "ye", "e", "a"];
const TENS_SUFFIX = ["a", "a", "ye", "a", "a", "ye"];

/** "20:00" -> "20:00'ye kadar" (inclusive upper bound). */
function untilText(hhmm) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm));
  let suffix = "e";
  if (m) {
    const n = Number(m[2]) || Number(m[1]);
    suffix = (n % 10 ? UNIT_SUFFIX[n % 10] : TENS_SUFFIX[Math.floor(n / 10)]) ?? "e";
  }
  return `${esc(hhmm)}'${suffix} kadar`;
}

function targetSummary(t) {
  const rule = isObj(t.rule) ? t.rule : {};
  const parts = [`${esc(t.name ?? t.id)}: ${esc(t.people)} kişi`, daysText(rule.days)];
  if (rule.from_time) parts.push(`${esc(rule.from_time)} sonrası`);
  if (rule.to_time) parts.push(untilText(rule.to_time));
  const ov = isObj(rule.overrides) ? Object.keys(rule.overrides).length : 0;
  if (ov) parts.push(`${ov} gün istisnası`);
  const ex = Array.isArray(rule.exclude_dates) ? rule.exclude_dates.length : 0;
  if (ex) parts.push(`${ex} hariç tarih`);
  return parts.join(" · ");
}

function btn(text, data) {
  return encoder.encode(data).length <= MAX_CALLBACK_BYTES ? { text, callback_data: data } : null;
}

/** Drops oversize/null buttons and empty rows. */
function rows(...rs) {
  return rs.map((r) => r.filter(Boolean)).filter((r) => r.length > 0);
}

const BACK_MAIN = () => btn("🏠 Ana menü", "m");

function notFound(what, backExtra) {
  return { text: `${what} bulunamadı.`, keyboard: rows([backExtra, BACK_MAIN()]) };
}

// ---------------------------------------------------------------- apply

const MUTATING = new Set(["ta", "tg", "hp", "hd", "hf", "ht", "hx", "hs", "pz"]);

function validHour(s) {
  if (s === "none") return null;
  if (!/^\d{1,2}$/.test(s ?? "")) return undefined;
  const n = Number(s);
  return n >= 0 && n <= 23 ? `${String(n).padStart(2, "0")}:00` : undefined;
}

/** "261128" -> "2026-11-28"; null unless it is a real calendar date. */
function dateFromYymmdd(s) {
  const m = /^(\d{2})(\d{2})(\d{2})$/.exec(s ?? "");
  if (!m) return null;
  const [y, mo, d] = [2000 + Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  return dt.toISOString().slice(0, 10);
}

function ensurePlay(out, slug) {
  if (!isObj(out.plays)) out.plays = {};
  if (!isObj(out.plays[slug])) out.plays[slug] = { active: true, targets: [], tag_overrides: {} };
  const w = out.plays[slug];
  if (!Array.isArray(w.targets)) w.targets = [];
  return w;
}

function addTarget(out, slug, code) {
  const kind = KINDS[code];
  if (!kind || findTarget(out, slug, kind[0])) return;
  ensurePlay(out, slug).targets.push({
    id: kind[0], name: kind[1], people: 2,
    rule: { days: null, from_time: null, to_time: null, overrides: {}, exclude_dates: [] },
  });
}

function ruleOf(t) {
  if (!isObj(t.rule)) t.rule = { days: null, from_time: null, to_time: null, overrides: {}, exclude_dates: [] };
  return t.rule;
}

/** Pure: returns a new config (deep copy) with the route applied; unchanged copy on any problem. */
export function apply(route, config, state) {
  let out;
  try {
    out = structuredClone(config);
  } catch {
    return config;
  }
  if (!isObj(out)) return out;
  try {
    const parts = String(route ?? "").split(":");
    const op = parts[0];
    if (!MUTATING.has(op)) return out;

    if (op === "pz") {
      if (parts[1] === "0" || parts[1] === "1") out.paused = parts[1] === "1";
      return out;
    }

    const slug = indexFor(out, state).slugOf.get(parts[1]);
    if (slug === undefined) return out;

    if (op === "ta") {
      if (parts[2] === "0" || parts[2] === "1") {
        const w = ensurePlay(out, slug);
        w.active = parts[2] === "1";
        // An active play without a target never alerts: add the default target
        // (child if the play's state tags say "çocuk"). Same as pc/configedit.set_active.
        if (w.active && w.targets.length === 0) {
          const tags = statePlays(state)[slug]?.tags;
          addTarget(out, slug, Array.isArray(tags) && tags.includes("çocuk") ? "c" : "y");
        }
      }
      return out;
    }
    if (op === "tg") {
      addTarget(out, slug, parts[2]);
      return out;
    }

    const t = findTarget(out, slug, parts[2]);
    if (!t) return out;

    if (op === "hs") {
      if (parts[3] === "rm") out.plays[slug].targets = out.plays[slug].targets.filter((x) => x !== t);
    } else if (op === "hp") {
      if (/^[1-4]$/.test(parts[3] ?? "")) t.people = Number(parts[3]);
    } else if (op === "hd") {
      const code = parts[3];
      const flag = parts[4];
      if (code === "wd") ruleOf(t).days = [0, 1, 2, 3, 4];
      else if (code === "we") ruleOf(t).days = [5, 6];
      else if (code === "all") ruleOf(t).days = null;
      else if (/^d[0-6]$/.test(code ?? "") && (flag === "0" || flag === "1")) {
        const rule = ruleOf(t);
        const set = new Set(rule.days == null ? ALL_DAYS : rule.days);
        if (flag === "1") set.add(Number(code[1]));
        else set.delete(Number(code[1]));
        rule.days = set.size === 7 ? null : [...set].sort((a, b) => a - b);
      }
    } else if (op === "hf" || op === "ht") {
      const value = validHour(parts[3]);
      if (value !== undefined) ruleOf(t)[op === "hf" ? "from_time" : "to_time"] = value;
    } else if (op === "hx") {
      const flag = parts[4];
      const date = dateFromYymmdd(parts[3]);
      if (date && (flag === "0" || flag === "1")) {
        {
          const rule = ruleOf(t);
          const cur = Array.isArray(rule.exclude_dates) ? rule.exclude_dates : [];
          rule.exclude_dates = flag === "1"
            ? [...new Set([...cur, date])].sort()
            : cur.filter((d) => d !== date);
        }
      }
    }
    return out;
  } catch {
    return out;
  }
}

// ---------------------------------------------------------------- render

function allSlugs(config, state) {
  return [...new Set([...Object.keys(cfgPlays(config)), ...Object.keys(statePlays(state))])];
}

function isWatched(config, state, slug) {
  return cfgPlays(config)[slug]?.active === true && !statePlays(state)[slug]?.removed;
}

function mainScreen(config, state) {
  const watched = allSlugs(config, state).filter((s) => isWatched(config, state, s)).length;
  const paused = isObj(config) && config.paused === true;
  const lastScan = state?.meta?.last_scan;
  const text = [
    "🎭 <b>Tiyatro Takip</b>",
    `Durum: ${paused ? "⏸ duraklatıldı" : "▶️ çalışıyor"}`,
    `Takip edilen oyun: ${watched}`,
    lastScan ? `Son tarama: ${esc(fmt(lastScan))}` : "Son tarama: bilinmiyor",
  ].join("\n");
  return {
    text,
    keyboard: rows(
      [btn("🎭 Oyunlar", "pl:0")],
      [btn("📋 Takip listem", "tl")],
      [btn("⏳ Yaklaşan açılışlar", "up")],
      [btn("🔄 Yenile", "rf")],
      [paused ? btn("▶️ Devam", "pz:0") : btn("⏸ Duraklat", "pz:1")],
    ),
  };
}

function listScreen(config, state, pageArg, idx) {
  const slugs = allSlugs(config, state).sort((a, b) =>
    playTitle(state, a).localeCompare(playTitle(state, b), "tr"));
  const pages = Math.max(1, Math.ceil(slugs.length / PAGE_SIZE));
  let page = /^\d+$/.test(pageArg ?? "") ? Number(pageArg) : 0;
  page = Math.min(page, pages - 1);
  const slice = slugs.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const nav = [
    page > 0 ? btn("‹ Önceki", `pl:${page - 1}`) : null,
    page < pages - 1 ? btn("Sonraki ›", `pl:${page + 1}`) : null,
  ];
  return {
    text: `🎭 <b>Oyunlar</b> (sayfa ${page + 1}/${pages})\n🟢 takipte · ⚪ takipte değil`,
    keyboard: rows(
      ...slice.map((s) => [btn(
        `${cfgPlays(config)[s]?.active === true ? "🟢" : "⚪"} ${trunc(playTitle(state, s), 38)}`,
        `p:${idx.hashOf.get(s)}`)]),
      nav,
      [BACK_MAIN()],
    ),
  };
}

const STATUS_TEXT = {
  on_sale: (rec) => ["✅", "satışta", rec],
  upcoming: (rec) => ["⏳", rec.opens_at ? `yakında (açılış ${fmt(rec.opens_at)})` : "yakında", rec],
  sold_out: (rec) => ["❌", "tükendi", rec],
};

function sessionLine(rec) {
  const f = STATUS_TEXT[rec.status];
  const [icon, label] = f ? f(rec) : ["❔", "belirsiz"];
  return `${icon} ${esc(fmt(rec.start))} — ${esc(label)}`;
}

function playScreen(config, state, slug, idx) {
  const hh = idx.hashOf.get(slug);
  const entry = statePlays(state)[slug] ?? {};
  const watch = cfgPlays(config)[slug];
  const active = watch?.active === true;
  const lines = [`🎭 <b>${esc(playTitle(state, slug))}</b>`];
  if (Array.isArray(entry.tags) && entry.tags.length) lines.push(`🏷 Etiketler: ${esc(entry.tags.join(", "))}`);
  const meta = [];
  if (entry.genre) meta.push(`Tür: ${esc(entry.genre)}`);
  if (entry.duration_min) meta.push(`Süre: ${esc(entry.duration_min)} dk`);
  if (entry.age_limit != null) meta.push(`Yaş: ${esc(entry.age_limit)}+`);
  if (meta.length) lines.push(meta.join(" · "));
  lines.push(`Takip: ${active ? "🟢 aktif" : "⚪ pasif"}`);

  const sessions = sessionList(state, slug);
  lines.push("", "<b>Seanslar</b>");
  if (sessions.length === 0) lines.push("Seans bilgisi yok.");
  for (const [, rec] of sessions.slice(0, 10)) lines.push(sessionLine(rec));
  if (sessions.length > 10) lines.push(`… +${sessions.length - 10} seans daha`);

  const targets = Array.isArray(watch?.targets) ? watch.targets.filter(isObj) : [];
  lines.push("", "<b>Hedefler</b>");
  if (targets.length === 0) lines.push(active ? NO_TARGET_TEXT : "Hedef yok.");
  for (const t of targets) lines.push(`• ${targetSummary(t)}`);
  if (entry.url) lines.push("", `<a href="${escAttr(entry.url)}">Biletinial sayfası</a>`);

  return {
    text: lines.join("\n"),
    keyboard: rows(
      [active ? btn("⚪ Pasif yap", `ta:${hh}:0`) : btn("🟢 Aktif yap", `ta:${hh}:1`)],
      ...targets.map((t) => [btn(`✏️ ${trunc(t.name ?? t.id, 30)}`, `ts:${hh}:${t.id}`)]),
      [
        targets.some((t) => t.id === "y") ? null : btn("➕ Yetişkin ekle", `tg:${hh}:y`),
        targets.some((t) => t.id === "c") ? null : btn("➕ Çocuk ekle", `tg:${hh}:c`),
      ],
      [btn("‹ Oyunlar", "pl:0"), BACK_MAIN()],
    ),
  };
}

function targetScreen(config, state, slug, t, idx) {
  const hh = idx.hashOf.get(slug);
  const id = t.id;
  const rule = isObj(t.rule) ? t.rule : {};
  const days = rule.days == null ? ALL_DAYS : rule.days;
  const lines = [
    `🎯 <b>${esc(playTitle(state, slug))}</b>`,
    targetSummary(t),
  ];
  const ex = Array.isArray(rule.exclude_dates) ? rule.exclude_dates : [];
  if (ex.length) lines.push(`Hariç tarihler: ${esc(ex.join(", "))}`);

  const people = [1, 2, 3, 4].map((n) => btn(`${n}${t.people === n ? " ✓" : ""}`, `hp:${hh}:${id}:${n}`));
  const dayBtns = DAY_NAMES.map((name, d) => {
    const on = days.includes(d);
    return btn(`${on ? "✓ " : ""}${name}`, `hd:${hh}:${id}:d${d}:${on ? 0 : 1}`);
  });
  const fromBtns = FROM_HOURS.map((x) =>
    btn(`${rule.from_time === `${x}:00` ? "✓ " : ""}${x}:00+`, `hf:${hh}:${id}:${x}`));
  const toBtns = TO_HOURS.map((x) =>
    btn(`${rule.to_time === `${x}:00` ? "✓ " : ""}≤${x}:00`, `ht:${hh}:${id}:${x}`));

  const now = Date.now();
  const seenDates = new Set();
  const exclBtns = sessionList(state, slug)
    .filter(([, rec]) => Date.parse(rec.start) >= now - 24 * 3600 * 1000)
    .filter(([, rec]) => {
      const d = isoDate(rec.start);
      if (seenDates.has(d)) return false;
      seenDates.add(d);
      return true;
    })
    .slice(0, 8)
    .map(([, rec]) => {
      const date = isoDate(rec.start);
      const excluded = ex.includes(date);
      const code = date.slice(2, 4) + date.slice(5, 7) + date.slice(8, 10);
      return btn(`${excluded ? "🚫 " : ""}${fmtShort(rec.start)}`, `hx:${hh}:${id}:${code}:${excluded ? 0 : 1}`);
    });

  const chunk = (arr, n) => Array.from({ length: Math.ceil(arr.length / n) }, (_, k) => arr.slice(k * n, k * n + n));
  return {
    text: lines.join("\n"),
    keyboard: rows(
      people,
      [btn("Hafta içi", `hd:${hh}:${id}:wd:1`), btn("Hafta sonu", `hd:${hh}:${id}:we:1`), btn("Her gün", `hd:${hh}:${id}:all:1`)],
      dayBtns.slice(0, 4),
      dayBtns.slice(4),
      ...chunk([...fromBtns, btn("Sınırsız", `hf:${hh}:${id}:none`)], 4),
      ...chunk([...toBtns, btn("Sınırsız", `ht:${hh}:${id}:none`)], 3),
      ...chunk(exclBtns, 2),
      [btn("🗑 Hedefi sil", `hs:${hh}:${id}:rm`)],
      [btn("‹ Oyun", `p:${hh}`), BACK_MAIN()],
    ),
  };
}

const TL_PAGE = 8;

function watchlistScreen(config, state, idx, pageArg) {
  const slugs = allSlugs(config, state)
    .filter((s) => isWatched(config, state, s))
    .sort((a, b) => playTitle(state, a).localeCompare(playTitle(state, b), "tr"));
  const pages = Math.max(1, Math.ceil(slugs.length / TL_PAGE));
  const page = Math.min(/^\d+$/.test(pageArg ?? "") ? Number(pageArg) : 0, pages - 1);
  const shown = slugs.slice(page * TL_PAGE, (page + 1) * TL_PAGE);
  const lines = [`📋 <b>Takip listem</b>${pages > 1 ? ` (sayfa ${page + 1}/${pages})` : ""}`];
  if (slugs.length === 0) lines.push("Takip edilen oyun yok.");
  for (const s of shown) {
    const targets = (cfgPlays(config)[s].targets ?? []).filter(isObj);
    lines.push("", `🎭 <b>${esc(playTitle(state, s))}</b>`);
    if (targets.length === 0) lines.push(`• ${NO_TARGET_TEXT}`);
    for (const t of targets.slice(0, 4)) lines.push(`• ${targetSummary(t)}`);
    if (targets.length > 4) lines.push(`… +${targets.length - 4} hedef daha`);
  }
  if (pages > 1) lines.push("", `… toplam ${slugs.length} oyun`);
  return {
    text: lines.join("\n"),
    keyboard: rows(
      ...shown.map((s) => [btn(`🎭 ${trunc(playTitle(state, s), 38)}`, `p:${idx.hashOf.get(s)}`)]),
      [
        page > 0 ? btn("‹ Önceki", `tl:${page - 1}`) : null,
        page < pages - 1 ? btn("Sonraki ›", `tl:${page + 1}`) : null,
      ],
      [BACK_MAIN()],
    ),
  };
}

function upcomingScreen(config, state) {
  const items = [];
  for (const slug of allSlugs(config, state)) {
    if (!isWatched(config, state, slug)) continue;
    for (const [, rec] of sessionList(state, slug)) {
      if (rec.status === "upcoming" && rec.opens_at && Number.isFinite(Date.parse(rec.opens_at))) {
        items.push({ slug, rec });
      }
    }
  }
  items.sort((a, b) => Date.parse(a.rec.opens_at) - Date.parse(b.rec.opens_at));
  const lines = ["⏳ <b>Yaklaşan açılışlar</b>"];
  if (items.length === 0) lines.push("Takip ettiğin oyunlarda yaklaşan açılış yok.");
  for (const { slug, rec } of items.slice(0, 15)) {
    lines.push("", `<b>${esc(playTitle(state, slug))}</b>`,
      `Seans: ${esc(fmt(rec.start))}`, `Açılış: ${esc(fmt(rec.opens_at))}`);
  }
  if (items.length > 15) lines.push("", `… +${items.length - 15} açılış daha`);
  return { text: lines.join("\n"), keyboard: rows([BACK_MAIN()]) };
}

/** Renders the screen for a route. Mutating routes render the screen they lead back to. */
export function render(route, config, state) {
  return guardLength(renderRoute(route, config, state));
}

const MAX_TEXT = 4096;

/** Telegram rejects text > 4096 chars: cut on a line boundary and close open HTML tags. */
export function guardLength(screen) {
  if (screen.text.length <= MAX_TEXT) return screen;
  let cut = screen.text.slice(0, MAX_TEXT - 200);
  const nl = cut.lastIndexOf("\n");
  if (nl > 0) cut = cut.slice(0, nl); // newlines never sit inside a tag or entity
  const open = [];
  for (const m of cut.matchAll(/<(\/?)([a-z]+)[^>]*>/g)) {
    if (m[1]) open.pop();
    else open.push(m[2]);
  }
  const tail = open.reverse().map((t) => `</${t}>`).join("");
  return { ...screen, text: `${cut}\n…${tail}` };
}

function renderRoute(route, config, state) {
  try {
    const parts = String(route ?? "").split(":");
    const op = parts[0];
    if (op === "m" || op === "pz") return mainScreen(config, state);
    if (op === "tl") return watchlistScreen(config, state, indexFor(config, state), parts[1]);
    if (op === "up") return upcomingScreen(config, state);
    if (op === "rf") {
      return { text: "🔄 Tarama istendi. Sonuçlar birkaç dakika içinde güncellenir.", keyboard: rows([BACK_MAIN()]) };
    }
    const idx = indexFor(config, state);
    if (op === "pl") return listScreen(config, state, parts[1], idx);

    const playOps = new Set(["p", "ta", "tg", "ts", "hp", "hd", "hf", "ht", "hx", "hs"]);
    if (!playOps.has(op)) return notFound("Ekran");
    const slug = idx.slugOf.get(parts[1]);
    if (slug === undefined) return notFound("Oyun", btn("🎭 Oyunlar", "pl:0"));

    if (op === "p" || op === "ta" || op === "tg") return playScreen(config, state, slug, idx);
    const t = findTarget(config, slug, parts[2]);
    if (!t) return notFound("Hedef", btn("‹ Oyun", `p:${parts[1]}`));
    return targetScreen(config, state, slug, t, idx);
  } catch {
    return notFound("Ekran");
  }
}

export function isMutating(route) {
  return MUTATING.has(String(route ?? "").split(":")[0]);
}

/**
 * The route whose screen should be shown after a route was handled: deleting a target
 * (hs) returns to the play screen, target edits return to the target screen, pz to the
 * main menu. Other routes show themselves.
 */
export function viewOf(route) {
  const parts = String(route ?? "").split(":");
  if (parts[0] === "hs") return `p:${parts[1]}`;
  if (parts[0] === "pz") return "m";
  if (["hp", "hd", "hf", "ht", "hx"].includes(parts[0])) return `ts:${parts[1]}:${parts[2]}`;
  return route;
}
