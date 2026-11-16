// Pure menu logic for the Telegram bot: routing, rendering and config mutation.
// No I/O here. `apply` and `render` never throw and never mutate their inputs.
//
// Rule semantics mirror tiyatro/rules.py: days 0=Monday..6=Sunday (null = every day),
// from_time and to_time both inclusive ("≤20:00" keeps 20:00 sessions), exclude_dates are
// Europe/Istanbul ISO dates.
// Routes (callback_data, each <= 64 bytes). One unified list of Biletinial plays and
// Başiskele/VKV events (wave 7); the follow switches are idempotent set routes.
//   m | pl:<page> | p:<h>[:<sessionPage>[:<listPage>]] | e:<h>[:<listPage>] (event screen)
//   ta:<h>:<0|1>[:<ret>] (play follow) | ef:<h>:<0|1>[:<ret>] (event follow); ret = <listPage> |
//   i<listPage> (back to the item screen) | none (item screen of a play / the list for an event)
//   ts:<h>:<id> (target screen) | tg:<h>:<y|c> | hp:<h>:<id>:<n> | hd:<h>:<id>:<wd|we|all|d0..d6>[:<0|1>]
//   hf:<h>:<id>:<HH|none> | ht:<h>:<id>:<HH|none> | hx:<h>:<id>:<yymmdd>:<0|1> (date, stateless)
//   hs:<h>:<id>:rm | tl | up | rf | src | pz:<0|1>      (eo:<n> = legacy alias of pl:0)
// Only set routes write config.json; browsing (m, pl, p, e, tl, ...) never does. Newly found plays/events
// are announced by the scanner ("🆕 Yeni oyun bulundu" Telegram notice), not marked in these lists.

import { audienceOf, genreLabel, playFields, eventFields } from "./derive.js";

export { audienceOf, genreLabel };

const PAGE_SIZE = 6;
const SESSION_PAGE = 8;
const MAX_CALLBACK_BYTES = 64;
const ISTANBUL_OFFSET_MS = 3 * 3600 * 1000; // Turkey has no DST (fixed UTC+3)
const DAY_NAMES = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const FROM_HOURS = [12, 14, 16, 18, 19, 20];
const TO_HOURS = [16, 18, 20, 22];
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const NO_TARGET_TEXT = "ℹ️ hedef yok → tüm seanslar izleniyor"; // as pc/viewmodel.py
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

/**
 * 1-2 plain-language lines under a screen title: what the buttons below do. Plain text on
 * purpose: italic is reserved for derived values (see LEGEND).
 */
const help = (...lines) => lines;
export const HELP = {
  main: [
    "ℹ️ Oyunlar: Biletinial oyunları ile Başiskele/VKV etkinlikleri tek listede; takibi aç/kapat · Takip listem: takip ettiklerin ve hedefleri · Yaklaşan açılışlar: bilet satışı açılacak seanslar.",
    "🔄 Yenile: tüm kaynakları (Biletinial, Başiskele, VKV) şimdi tarar ve listeyi günceller · Kaynaklar: son kontrol zamanları ve kaynak sayfaları · Duraklat: bildirimleri geçici durdurur.",
  ],
  list: [
    "ℹ️ ☑/☐ düğmesi o öğenin takibini açar/kapatır; adı yazan düğme ayrıntıyı, oyun ve seans bağlantılarını açar.",
    "Yeni gelen oyun ve etkinlikler takipte DEĞİL başlar; bulunduklarında ayrıca “🆕 Yeni oyun bulundu” bildirimi gelir. Hedef eklemek oyunu otomatik takibe alır.",
  ],
  play: ["ℹ️ Aktif yap: bu oyunda satış açılınca bildirim gelir (hedef yoksa tüm seanslar için).", "Hedef: hangi gün/saat ve kaç kişilik biletle ilgilendiğini sınırlar; ✏️ ile düzenlenir. Oyun sayfası / Seans düğmeleri Biletinial'ı açar."],
  event: ["ℹ️ Takibi aç: rezervasyon açılışı duyurulunca ve açılınca bildirim gelir; açılış saati duyurulmadıysa bildirim gelmez.", "Yeni etkinlikler takipte DEĞİL başlar. Etkinlik sayfası kaynağın kendi sayfasını açar (seans bazlı sayfa yoktur)."],
  target: ["ℹ️ Kişi sayısı, gün ve saat seç: yalnızca bunlara uyan seanslar bildirilir.", "Tarih düğmesi (🚫) o günü atlar; Hedefi sil bu kısıtı kaldırır."],
  watchlist: ["ℹ️ Takip ettiğin oyunlar ve her birinin hedefleri.", "Bir oyuna dokununca hedeflerini değiştirebilir veya takibi kapatabilirsin."],
  upcoming: ["ℹ️ Takip ettiğin oyunlarda bilet satışının açılacağı zamanlar.", "Açılıştan 1 gün ve 10 dk önce ayrıca hatırlatma gelir."],
  sources: ["ℹ️ Yenile: tüm kaynakları şimdi tarar ve listeyi günceller; sonuç burada kaynak başına simgeyle görünür.", "Durumu göster: bu ekranı son sonuçlarla tazeler. 'aç' düğmeleri kaynağın kendi sayfasını açar."],
};

/** One legend line for the list and item screens: ☑/☐ and the italic marker of derived values. */
export const LEGEND = "☑ takipte · ☐ takipte değil · <i>italik = türetilmiş</i> (kural/varsayım), normal = kaynaktan alınan";

const BACK_MAIN = () => btn("🏠 Ana menü", "m");

function notFound(what, backExtra) {
  return { text: `${what} bulunamadı.`, keyboard: rows([backExtra, BACK_MAIN()]) };
}

// ---------------------------------------------------------------- apply

const MUTATING = new Set(["ta", "tg", "hp", "hd", "hf", "ht", "hx", "hs", "pz", "ef"]);

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
  const w = ensurePlay(out, slug);
  w.targets.push({
    id: kind[0], name: kind[1], people: 2,
    rule: { days: null, from_time: null, to_time: null, overrides: {}, exclude_dates: [] },
  });
  w.active = true; // adding a target to an unfollowed play follows it (pc/configedit.ensure_target)
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

    if (op === "ef") {
      // Per-event follow of Başiskele/VKV events: set semantics (ef:<h>:<0|1>[:<page>]).
      const eid = eventIndex(state).slugOf.get(parts[1]);
      if (eid !== undefined && (parts[2] === "0" || parts[2] === "1")) {
        if (!isObj(out.event_follow)) out.event_follow = {};
        out.event_follow[eid] = parts[2] === "1";
      }
      return out;
    }

    const slug = indexFor(out, state).slugOf.get(parts[1]);
    if (slug === undefined) return out;

    if (op === "ta") {
      if (parts[2] === "0" || parts[2] === "1") {
        const w = ensurePlay(out, slug);
        // A followed play without targets watches every session (tiyatro/rules.py);
        // no default target is created. Same as pc/configedit.set_active.
        w.active = parts[2] === "1";
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

const PORTAL_LINES = 5;

/** "🏛️ Başiskele portalı: boş | açık (N)" per portal in state.portals; unknown state -> none. */
function portalLines(state) {
  const portals = isObj(state) && isObj(state.portals) ? state.portals : {};
  const out = [];
  for (const id of Object.keys(portals).sort()) {
    const rec = portals[id];
    if (!isObj(rec) || typeof rec.empty !== "boolean") continue;
    const label = id === "basiskele" ? "Başiskele portalı" : esc(trunc(rec.name || id, 40));
    const n = Array.isArray(rec.items) ? rec.items.length : 0;
    out.push(`🏛️ ${label}: ${rec.empty ? "boş" : n > 0 ? `açık (${n})` : "açık"}`);
    if (out.length >= PORTAL_LINES) break;
  }
  return out;
}

const SOURCE_ORDER = ["biletinial", "basiskele", "vkv"];
const SOURCE_NAMES = { biletinial: "Biletinial", basiskele: "Başiskele Belediyesi", vkv: "VKV Ford Otosan Gölcük" };
const STALE_MS = 30 * 60 * 1000;
const SOURCE_LEGEND = "✅ güncel · ⚠️ 30 dakikadan eski (kontrol gecikti) · ❌ son kontrol başarısız\n"
  + "Son kontrol = kaynağa en son bakılma zamanı; Son güncelleme = kaynağın kendi içeriğinin en son değişme zamanı.";

/** Per-source health from state.sources (tiyatro/sources.py); sources without a record are skipped. */
export function sourceViews(state, now = Date.now()) {
  const recs = isObj(state) && isObj(state.sources) ? state.sources : {};
  const ids = [...SOURCE_ORDER.filter((i) => isObj(recs[i])),
    ...Object.keys(recs).filter((i) => !SOURCE_ORDER.includes(i) && isObj(recs[i])).sort()];
  return ids.map((id) => {
    const rec = recs[id];
    const checked = Date.parse(rec.last_check_at);
    const status = !Number.isFinite(checked) ? "unknown"
      : rec.ok === false ? "failed" : now - checked > STALE_MS ? "stale" : "ok";
    const icon = { ok: "✅", stale: "⚠️", failed: "❌", unknown: "⚪" }[status];
    return {
      id, status, icon, name: String(rec.name || SOURCE_NAMES[id] || id),
      lastCheck: rec.last_check_at ? fmtShort(rec.last_check_at) : "—",
      lastChange: rec.last_change_at ? fmtShort(rec.last_change_at) : "—",
      count: Number.isInteger(rec.items_count) ? rec.items_count : null,
      error: typeof rec.error === "string" && rec.error ? rec.error : null,
    };
  });
}

function sourceLines(state, now) {
  const out = [];
  for (const v of sourceViews(state, now)) {
    out.push(`${v.icon} <b>${esc(trunc(v.name, 40))}</b>`);
    const parts = [`Son kontrol: ${esc(v.lastCheck)}`, `Son güncelleme: ${esc(v.lastChange)}`];
    if (v.count !== null) parts.push(`${v.count} öğe`);
    out.push(parts.join(" · "));
    if (v.status === "failed" && v.error) out.push(`Hata: ${esc(trunc(v.error, 80))}`);
  }
  return out;
}

// ---- links

const SOURCE_SHORT = { biletinial: "Biletinial", basiskele: "Başiskele", vkv: "VKV" };
const BASISKELE_BASE = "https://www.basiskele.bel.tr"; // as tiyatro/srcevents.py
const VKV_LIST_URL = "https://www.vkvfordkultur.org.tr/Etkinlikler";
const MAX_URL_BYTES = 2000; // Telegram allows 2048

/** A URL Telegram accepts on a button (http/https, no whitespace, parseable) or null. */
function safeUrl(raw) {
  if (typeof raw !== "string") return null;
  const s = raw.trim();
  if (!/^https?:\/\//i.test(s) || /[\s\u0000-\u001f]/.test(s)) return null;
  try {
    const href = new URL(s).href;
    return encoder.encode(href).length <= MAX_URL_BYTES ? href : null;
  } catch {
    return null;
  }
}

/** URL button; null when the URL is unusable (the button is simply left out). */
function urlBtn(text, raw) {
  const url = safeUrl(raw);
  return url ? { text, url } : null;
}

/**
 * Page each source's "aç" button opens; mirrors pc/app_logic.source_links: Biletinial = the
 * venue page of config.sources[0], Başiskele = newest known program article (else the
 * municipality home page), VKV = the Etkinlikler list.
 */
export function sourceLinks(config, state) {
  const links = {};
  const first = isObj(config) && Array.isArray(config.sources) ? config.sources[0] : undefined;
  if (safeUrl(first)) links.biletinial = first.trim();
  const articles = state?.src_cache?.basiskele?.articles;
  let newest = "";
  if (isObj(articles)) {
    const ranked = Object.entries(articles)
      .filter(([link, r]) => isObj(r) && safeUrl(link))
      .map(([link, r]) => [String(r.pub ?? ""), link])
      .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
    newest = ranked.length ? ranked[ranked.length - 1][1].trim() : "";
  }
  links.basiskele = newest || BASISKELE_BASE;
  links.vkv = VKV_LIST_URL;
  return links;
}

/** "Kaynaklar" screen; ``refreshed`` adds the note shown right after the 🔄 Yenile button. */
function sourcesScreen(config, state, refreshed) {
  const lines = ["📡 <b>Kaynaklar</b>", ...help(...HELP.sources)];
  if (refreshed) lines.push("🔄 Tarama istendi: tüm kaynaklar birkaç dakika içinde yeniden taranır. Aşağıdaki sonuçlar son taramadan; yenisi için “Durumu göster”e bas.");
  const body = sourceLines(state);
  if (body.length === 0) lines.push("Henüz kaynak bilgisi yok.");
  lines.push("", ...body, "", SOURCE_LEGEND);
  const links = sourceLinks(config, state);
  const views = sourceViews(state);
  const ids = views.length ? views.map((v) => v.id) : SOURCE_ORDER;
  const openers = ids.map((id) => {
    const name = SOURCE_SHORT[id] ?? trunc(views.find((v) => v.id === id)?.name ?? id, 20);
    return [urlBtn(`🔗 ${name} aç`, links[id])];
  });
  return { text: lines.join("\n"), keyboard: rows(...openers, [btn("🔄 Durumu göster", "src")], [BACK_MAIN()]) };
}

// ---- unified list: Biletinial plays + Başiskele/VKV events

function sourceEvents(state) {
  return isObj(state) && isObj(state.source_events) ? state.source_events : {};
}
function eventIndex(state) {
  return buildIndex(Object.keys(sourceEvents(state)));
}

/**
 * Future Başiskele/VKV events by start (same window as tiyatro/srcevents.event_views);
 * ``followed`` = explicit choice, else the stored default (missing field = followed, as there).
 */
export function eventViews(config, state, now = Date.now()) {
  const follow = isObj(config) && isObj(config.event_follow) ? config.event_follow : {};
  const out = [];
  for (const [id, rec] of Object.entries(sourceEvents(state))) {
    if (!isObj(rec)) continue;
    const start = Date.parse(rec.start);
    if (Number.isFinite(start) && start < now - 3 * 3600e3) continue;
    const dflt = rec.follow_default === undefined ? true : Boolean(rec.follow_default);
    const followed = typeof follow[id] === "boolean" ? follow[id] : dflt;
    out.push({ id, rec, start: Number.isFinite(start) ? start : Infinity, followed });
  }
  out.sort((a, b) => a.start - b.start || (a.id < b.id ? -1 : 1));
  return out;
}

/**
 * Every play and event as one list, ordered like the PC list: nearest upcoming session / event
 * start, then title, then id; never by follow state, so toggling a follow switch moves no row. `derived` holds the keys of the
 * values that are our rule (audience, genre, zaman, target, rez), see derive.js.
 */
export function listItems(config, state, now = Date.now()) {
  const plays = statePlays(state);
  const items = [];
  for (const slug of allSlugs(config, state)) {
    const entry = isObj(plays[slug]) ? plays[slug] : {};
    const watch = cfgPlays(config)[slug];
    const followed = watch?.active === true;
    const watched = followed && !entry.removed;
    if (entry.archived && !watched && !entry.removed) continue; // as pc/viewmodel.build_rows
    items.push({
      kind: "play", id: slug, title: playTitle(state, slug), source: SOURCE_SHORT.biletinial,
      followed, watched, removed: Boolean(entry.removed), url: entry.url,
      ...playFields(entry, watch, now),
    });
  }
  for (const { id, rec, followed } of eventViews(config, state, now)) {
    items.push({
      kind: "event", id, title: String(rec.title || id), source: SOURCE_SHORT[rec.source] ?? String(rec.source ?? ""),
      followed, watched: followed, removed: false, url: rec.url, venue: rec.venue,
      venueDerived: rec.venue_derived === true && Boolean(rec.venue),
      ...eventFields(rec, id, now, state),
    });
  }
  const far = Infinity;
  items.sort((a, b) => ((a.nearest ?? far) === (b.nearest ?? far) ? 0 : (a.nearest ?? far) < (b.nearest ?? far) ? -1 : 1)
    || a.title.localeCompare(b.title, "tr") || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return items;
}

/** Value text; derived values (our rule) and blanks are italic. */
function fieldText(item, key, text) {
  const value = text || "—";
  return item.derived.includes(key) || !text ? `<i>${esc(value)}</i>` : esc(value);
}

/** "Kitle · Tür · Zaman" and "Hedef · Rez. açılış" lines of the list rows and the event screen. */
function fieldLines(item) {
  return [
    `Kitle: ${fieldText(item, "audience", item.kitle)} · Tür: ${fieldText(item, "genre", item.tur)} · Zaman: ${fieldText(item, "zaman", item.zaman)}`,
    `Hedef: ${fieldText(item, "target", item.hedef)} · Rez. açılış: ${fieldText(item, "rez", item.rez)}`,
  ];
}

const whenSuffix = (item) => (item.nearest !== null && item.nearest !== undefined
  ? ` · ${esc(fmtShort(new Date(item.nearest).toISOString()))}` : "");

function listScreen(config, state, pageArg, now) {
  const idx = indexFor(config, state);
  const eidx = eventIndex(state);
  const all = listItems(config, state, now);
  const pages = Math.max(1, Math.ceil(all.length / PAGE_SIZE));
  const page = Math.min(/^\d+$/.test(pageArg ?? "") ? Number(pageArg) : 0, pages - 1);
  const shown = all.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const lines = [`🎭 <b>Oyunlar ve etkinlikler</b> (sayfa ${page + 1}/${pages}, ${all.length} öğe)`, ...help(...HELP.list), LEGEND];
  if (all.length === 0) lines.push("", "Henüz oyun veya etkinlik bilgisi yok.");
  for (const it of shown) {
    lines.push("", `${it.followed ? "☑" : "☐"} <b>${esc(trunc(it.title, 60))}</b>${it.removed ? " (kalktı)" : ""}`,
      `${esc(it.source)}${whenSuffix(it)}`, ...fieldLines(it));
  }
  const nav = [
    page > 0 ? btn("‹ Önceki", `pl:${page - 1}`) : null,
    page < pages - 1 ? btn("Sonraki ›", `pl:${page + 1}`) : null,
  ];
  return {
    text: lines.join("\n"),
    keyboard: rows(
      ...shown.map((it) => {
        const isPlay = it.kind === "play";
        const hh = isPlay ? idx.hashOf.get(it.id) : eidx.hashOf.get(it.id);
        const flag = it.followed ? 0 : 1;
        return [
          btn(`${it.followed ? "☑" : "☐"} ${trunc(it.title, 22)}`, `${isPlay ? "ta" : "ef"}:${hh}:${flag}:${page}`),
          btn("Ayrıntı ›", isPlay ? `p:${hh}:0:${page}` : `e:${hh}:${page}`),
        ];
      }),
      nav,
      [BACK_MAIN()],
    ),
  };
}

const NO_SOURCE_INFO = "Kaynakta bilgi yok";
const EVENT_SUMMARY_CHARS = 600;
const CREDITS_CHARS = 300;

/**
 * Süre/Yaş/Ücret (only when stated), Özet, Oyuncular, Yapım of a source event, as the page states them
 * (normal text, nothing derived); a section the source did not provide says "Kaynakta bilgi yok".
 */
function eventInfoLines(it) {
  const lines = [];
  const stated = [it.sure && `Süre: ${esc(it.sure)}`, it.yas && `Yaş: ${esc(it.yas)}`,
    it.ucret && `Ücret: ${esc(it.ucret)}`].filter(Boolean);
  if (stated.length) lines.push(stated.join(" · "));
  const ozet = trunc(it.ozet.replace(/\s+/g, " ").trim(), EVENT_SUMMARY_CHARS);
  lines.push(`📝 Özet: ${ozet ? esc(ozet) : NO_SOURCE_INFO}`);
  lines.push(castLine(it.oyuncular) || `Oyuncular: ${NO_SOURCE_INFO}`);
  const shown = [];
  let used = 0;
  for (const [k, v] of it.yapim) {            // whole entries only: never cut inside an escaped entity
    const raw = `${trunc(k, 30)}: ${trunc(v, 60)}`;
    if (used + raw.length > CREDITS_CHARS) break;
    used += raw.length;
    shown.push(esc(raw));
  }
  lines.push(`Yapım: ${shown.length ? shown.join(" · ") : NO_SOURCE_INFO}`);
  return lines;
}

/** Event screen: source, derived fields, follow switch and the page link. */
function eventScreen(config, state, id, now, pageArg) {
  const hh = eventIndex(state).hashOf.get(id);
  const lp = /^\d+$/.test(pageArg ?? "") ? Number(pageArg) : 0;
  const it = listItems(config, state, now).find((x) => x.kind === "event" && x.id === id);
  if (!it) return notFound("Etkinlik", btn("‹ Liste", `pl:${lp}`));
  const rec = sourceEvents(state)[id];
  const lines = [`📅 <b>${esc(trunc(it.title, 100))}</b>`, ...help(...HELP.event), LEGEND, `Kaynak: ${esc(it.source)}`];
  if (rec.start) lines.push(`Etkinlik: ${esc(fmt(rec.start))}`);
  if (it.venue) lines.push(`Mekan: ${esc(trunc(it.venue, 80))}`);
  lines.push(...fieldLines(it));
  lines.push(...eventInfoLines(it));
  lines.push(`Takip: ${it.followed ? "☑ takipte" : "☐ takipte değil (yeni etkinlikler varsayılan olarak takipte değildir)"}`);
  const flag = it.followed ? 0 : 1;
  return {
    text: lines.join("\n"),
    keyboard: rows(
      [btn(it.followed ? "☐ Takibi kapat" : "☑ Takibi aç", `ef:${hh}:${flag}:i${lp}`)],
      [urlBtn("🔗 Etkinlik sayfası", it.url)],
      [btn("‹ Liste", `pl:${lp}`), BACK_MAIN()],
    ),
  };
}

function mainScreen(config, state, now) {
  const items = listItems(config, state, now);
  const watched = allSlugs(config, state).filter((s) => isWatched(config, state, s)).length;
  const events = items.filter((i) => i.kind === "event" && i.followed).length;
  const paused = isObj(config) && config.paused === true;
  const lastScan = state?.meta?.last_scan;
  const text = [
    "🎭 <b>Tiyatro Takip</b>",
    ...help(...HELP.main),
    `Durum: ${paused ? "⏸ duraklatıldı" : "▶️ çalışıyor"}`,
    ...(paused ? ["Duraklatılmışken oluşan bildirimler atılır; devam edince gönderilmez."] : []),
    `Takip edilen oyun: ${watched}`,
    `Takip edilen etkinlik: ${events}`,
    lastScan ? `Son tarama: ${esc(fmt(lastScan))}` : "Son tarama: bilinmiyor",
    ...portalLines(state),
  ].join("\n");
  return {
    text,
    keyboard: rows(
      [btn("🎭 Oyunlar ve etkinlikler", "pl:0")],
      [btn("📋 Takip listem", "tl")],
      [btn("⏳ Yaklaşan açılışlar", "up")],
      [btn("🔄 Yenile", "rf"), btn("📡 Kaynaklar", "src")],
      [paused ? btn("▶️ Devam", "pz:0") : btn("⏸ Duraklat", "pz:1")],
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

const SUMMARY_CHARS = 250;
const CAST_SHOWN = 6;

function shortSummary(raw) {
  if (typeof raw !== "string") return "";
  return trunc(raw.replace(/\s+/g, " ").trim(), SUMMARY_CHARS);
}

function castLine(raw) {
  const names = (Array.isArray(raw) ? raw : []).filter((n) => typeof n === "string" && n.trim());
  if (names.length === 0) return "";
  const shown = names.slice(0, CAST_SHOWN).map((n) => esc(trunc(n.trim(), 40))).join(", ");
  const more = names.length > CAST_SHOWN ? ` (+${names.length - CAST_SHOWN})` : "";
  return `Oyuncular: ${shown}${more}`;
}

const chunk = (arr, n) => Array.from({ length: Math.ceil(arr.length / n) }, (_, k) => arr.slice(k * n, k * n + n));

function playScreen(config, state, slug, idx, now, spArg, lpArg) {
  const hh = idx.hashOf.get(slug);
  const entry = statePlays(state)[slug] ?? {};
  const watch = cfgPlays(config)[slug];
  const active = watch?.active === true;
  const lp = /^\d+$/.test(lpArg ?? "") ? Number(lpArg) : 0;
  const f = playFields(entry, watch, now);
  const lines = [`🎭 <b>${esc(playTitle(state, slug))}</b>`, ...help(...HELP.play), LEGEND, `Kaynak: ${SOURCE_SHORT.biletinial}`];
  if (Array.isArray(entry.tags) && entry.tags.length) lines.push(`🏷 Etiketler: ${esc(entry.tags.join(", "))}`);
  const meta = [`Kitle: ${fieldText(f, "audience", f.kitle)}`, `Tür: ${fieldText(f, "genre", f.tur)}`,
    `Zaman: ${fieldText(f, "zaman", f.zaman)}`];
  if (entry.duration_min) meta.push(`Süre: ${esc(entry.duration_min)} dk`);
  if (entry.age_limit != null) meta.push(`Yaş: ${esc(entry.age_limit)}+`);
  lines.push(meta.join(" · "));
  lines.push(`Hedef: ${fieldText(f, "target", f.hedef)} · Rez. açılış: ${fieldText(f, "rez", f.rez)}`);
  const summary = shortSummary(entry.summary);
  if (summary) lines.push(`📝 ${esc(summary)}`);
  const cast = castLine(entry.cast);
  if (cast) lines.push(cast);
  lines.push(`Takip: ${active ? "🟢 aktif" : "⚪ pasif (yeni oyunlar varsayılan olarak takipte değildir)"}`);

  const sessions = sessionList(state, slug).filter(([, rec]) => Date.parse(rec.start) >= now);
  const spages = Math.max(1, Math.ceil(sessions.length / SESSION_PAGE));
  const sp = Math.min(/^\d+$/.test(spArg ?? "") ? Number(spArg) : 0, spages - 1);
  const shown = sessions.slice(sp * SESSION_PAGE, (sp + 1) * SESSION_PAGE);
  lines.push("", `<b>Seanslar</b>${spages > 1 ? ` (sayfa ${sp + 1}/${spages}, ${sessions.length} seans)` : ""}`);
  if (sessions.length === 0) lines.push("Seans bilgisi yok.");
  for (const [, rec] of shown) lines.push(sessionLine(rec));
  const sessionBtns = shown.map(([, rec]) => urlBtn(`🔗 Seans ${fmtShort(rec.start)}`, rec.url));
  if (shown.length && sessionBtns.some((b) => !b)) {
    lines.push("Kendi sayfası olmayan seanslar için “Oyun sayfası” düğmesini kullan.");
  }

  const targets = Array.isArray(watch?.targets) ? watch.targets.filter(isObj) : [];
  lines.push("", "<b>Hedefler</b>");
  if (targets.length === 0) lines.push(active ? `<i>${NO_TARGET_TEXT}</i>` : "Hedef yok.");
  for (const t of targets) lines.push(`• ${targetSummary(t)}`);

  const sessionNav = [
    sp > 0 ? btn("‹ Seanslar", `p:${hh}:${sp - 1}:${lp}`) : null,
    sp < spages - 1 ? btn("Seanslar ›", `p:${hh}:${sp + 1}:${lp}`) : null,
  ];
  return {
    text: lines.join("\n"),
    keyboard: rows(
      [active ? btn("⚪ Pasif yap", `ta:${hh}:0:i${lp}`) : btn("🟢 Aktif yap", `ta:${hh}:1:i${lp}`)],
      ...targets.map((t) => [btn(`✏️ ${trunc(t.name ?? t.id, 30)}`, `ts:${hh}:${t.id}`)]),
      [
        targets.some((t) => t.id === "y") ? null : btn("➕ Yetişkin ekle", `tg:${hh}:y`),
        targets.some((t) => t.id === "c") ? null : btn("➕ Çocuk ekle", `tg:${hh}:c`),
      ],
      [urlBtn("🔗 Oyun sayfası", entry.url)],
      ...chunk(sessionBtns, 2),
      sessionNav,
      [btn("‹ Liste", `pl:${lp}`), BACK_MAIN()],
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
    ...help(...HELP.target),
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
  const lines = [`📋 <b>Takip listem</b>${pages > 1 ? ` (sayfa ${page + 1}/${pages})` : ""}`, ...help(...HELP.watchlist)];
  if (slugs.length === 0) lines.push("Takip edilen oyun yok.");
  for (const s of shown) {
    const targets = (cfgPlays(config)[s].targets ?? []).filter(isObj);
    lines.push("", `🎭 <b>${esc(playTitle(state, s))}</b>`);
    if (targets.length === 0) lines.push(`• <i>${NO_TARGET_TEXT}</i>`);
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
  const lines = ["⏳ <b>Yaklaşan açılışlar</b>", ...help(...HELP.upcoming), ...portalLines(state)];
  if (items.length === 0) lines.push("Takip ettiğin oyunlarda yaklaşan açılış yok.");
  for (const { slug, rec } of items.slice(0, 15)) {
    lines.push("", `<b>${esc(playTitle(state, slug))}</b>`,
      `Seans: ${esc(fmt(rec.start))}`, `Açılış: ${esc(fmt(rec.opens_at))}`);
  }
  if (items.length > 15) lines.push("", `… +${items.length - 15} açılış daha`);
  return { text: lines.join("\n"), keyboard: rows([BACK_MAIN()]) };
}

/** Renders the screen for a route. Mutating routes render the screen they lead back to. */
export function render(route, config, state, now = Date.now()) {
  return guardLength(renderRoute(route, config, state, now));
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

function renderRoute(route, config, state, now) {
  try {
    const parts = String(route ?? "").split(":");
    const op = parts[0];
    if (op === "m" || op === "pz") return mainScreen(config, state, now);
    if (op === "tl") return watchlistScreen(config, state, indexFor(config, state), parts[1]);
    if (op === "up") return upcomingScreen(config, state);
    if (op === "rf") return sourcesScreen(config, state, true);
    if (op === "src") return sourcesScreen(config, state, false);
    if (op === "eo") return listScreen(config, state, "0", now); // legacy button of an old message
    if (op === "pl") return listScreen(config, state, parts[1], now);
    if (op === "e") {
      const id = eventIndex(state).slugOf.get(parts[1]);
      if (id === undefined) return notFound("Etkinlik", btn("🎭 Liste", "pl:0"));
      return eventScreen(config, state, id, now, parts[2]);
    }
    if (op === "ef") {
      // a follow switch renders the screen it leads back to (see viewOf)
      const back = viewOf(route);
      return back === route ? notFound("Ekran") : renderRoute(back, config, state, now);
    }
    if (op === "ta" && parts[3] !== undefined) {
      const back = viewOf(route);
      if (back !== route) return renderRoute(back, config, state, now);
    }
    const idx = indexFor(config, state);

    const playOps = new Set(["p", "ta", "tg", "ts", "hp", "hd", "hf", "ht", "hx", "hs"]);
    if (!playOps.has(op)) return notFound("Ekran");
    const slug = idx.slugOf.get(parts[1]);
    if (slug === undefined) return notFound("Oyun", btn("🎭 Liste", "pl:0"));

    if (op === "p") return playScreen(config, state, slug, idx, now, parts[2], parts[3]);
    if (op === "ta" || op === "tg") return playScreen(config, state, slug, idx, now);
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
 * main menu, a follow switch (ta / ef) to the list page or item screen it came from.
 * Other routes show themselves.
 */
export function viewOf(route) {
  const parts = String(route ?? "").split(":");
  if (parts[0] === "hs") return `p:${parts[1]}`;
  if (parts[0] === "ta" || parts[0] === "ef") {
    // ret (4th part): <n> = list page n, i<n> = item screen opened from list page n
    const ret = parts[3] ?? "";
    if (/^\d+$/.test(ret)) return `pl:${ret}`;
    const item = /^i(\d+)$/.exec(ret);
    if (item) return parts[0] === "ta" ? `p:${parts[1]}:0:${item[1]}` : `e:${parts[1]}:${item[1]}`;
    return parts[0] === "ta" ? route : "pl:0";
  }
  if (parts[0] === "pz") return "m";
  if (["hp", "hd", "hf", "ht", "hx"].includes(parts[0])) return `ts:${parts[1]}:${parts[2]}`;
  return route;
}
