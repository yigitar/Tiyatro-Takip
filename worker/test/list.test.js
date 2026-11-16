// Wave 7: unified play/event list, derived fields, link buttons.
import { test } from "node:test";
import assert from "node:assert/strict";
import { h, render, apply, listItems, sourceLinks, isMutating, viewOf, HELP, LEGEND } from "../src/menu.js";
import {
  resolveAudience, resolveGenre, genreFromText, timeOfDay, audienceIsStated, genreIsStated,
  rezWhen, eventFields, playFields, eventGenre,
} from "../src/derive.js";
import { makeConfig, makeState } from "./_fixtures.js";

const EXPECTED_ORDER = ["bk:20300206-1400-cop-atlas", "bk:evlilik", "vkv:541", "kusursuz-bir-aile", "kucuk-prens", "yeni-oyun"];
const NOW = Date.parse("2030-02-01T09:00:00+03:00");
const KB = h("kusursuz-bir-aile");
const flat = (r) => r.keyboard.flat();
const bytes = (s) => new TextEncoder().encode(s).length;
const iso = (days, hh = 12) => new Date(NOW + days * 86400e3 + (hh - 9) * 3600e3).toISOString();

function evState() {
  const s = makeState();
  s.source_events = {
    "vkv:541": { id: "vkv:541", source: "vkv", title: "Ceviz Adam <Müzikali>", kind: "Çocuk Tiyatrosu", start: iso(10, 11),
      opens_at: iso(2, 12), opens_pending: true, full: false, url: "https://www.vkvfordkultur.org.tr/Etkinlik/541", follow_default: false },
    "bk:20300206-1400-cop-atlas": { id: "bk:20300206-1400-cop-atlas", source: "basiskele", title: "Çöp Atlas", kind: "Tiyatro",
      start: iso(5, 14), opens_at: null, follow_default: false, url: "https://www.basiskele.bel.tr/x" },
    "bk:old": { id: "bk:old", source: "basiskele", title: "Geçmiş", start: iso(-3), follow_default: false },
    "bk:evlilik": { id: "bk:evlilik", source: "basiskele", title: "Evlilik Okulu", kind: "", start: iso(7, 19), follow_default: false },
  };
  return s;
}

test("one list: plays and events together, each labelled with its source and a follow box", () => {
  const r = render("pl:0", makeConfig(), evState(), NOW);
  for (const t of ["Kusursuz Bir Aile &amp; Şükran", "Küçük Prens", "Ceviz Adam &lt;Müzikali&gt;", "Çöp Atlas"]) {
    assert.ok(r.text.includes(t), `${t}\n${r.text}`);
  }
  assert.ok(!r.text.includes("Geçmiş")); // events more than 3 h in the past are dropped
  assert.ok(r.text.includes("Biletinial") && r.text.includes("Başiskele") && r.text.includes("VKV"));
  assert.ok(r.text.includes("☑ <b>Kusursuz") && r.text.includes("☐ <b>Küçük Prens"));
  assert.ok(r.text.includes("☐ <b>Çöp Atlas"));
  assert.ok(!/Diğer kaynaklar/.test(r.text));
  const m = render("m", makeConfig(), evState(), NOW);
  assert.ok(!/Diğer kaynaklar/.test(m.text) && !flat(m).some((b) => /Diğer kaynaklar/.test(b.text) || /^eo:/.test(b.callback_data ?? "")));
  assert.ok(!Object.values(HELP).flat().some((l) => /Diğer kaynaklar/.test(l)));
});

test("rows carry Kitle / Tür / Zaman / Hedef / Rez. açılış; derived values are italic, stated ones are not", () => {
  const items = listItems(makeConfig(), evState(), NOW);
  const by = (id) => items.find((i) => i.id === id);
  const kb = by("kusursuz-bir-aile");
  assert.deepEqual([kb.kitle, kb.tur, kb.zaman, kb.hedef, kb.rez],
    ["Yetişkin", "Komedi", "gündüz+gece", "var (1)", "20 Şub Çar 10:00 (19 gün sonra)"]);
  assert.deepEqual(kb.derived, ["audience", "zaman"]);
  const kp = by("kucuk-prens");
  assert.deepEqual([kp.tur, kp.zaman, kp.hedef], ["", "gündüz", "yok → tüm seanslar"]);
  assert.deepEqual(kp.derived, ["audience", "genre", "rez", "target", "zaman"].filter((k) => kp.derived.includes(k)));
  assert.ok(kp.derived.includes("target") && kp.derived.includes("genre"));
  const cz = by("vkv:541");
  assert.deepEqual([cz.kitle, cz.tur, cz.zaman, cz.hedef], ["Çocuk", "Müzikal", "gündüz", "—"]);
  assert.equal(cz.rez, "3 Şub Paz 12:00 (2 gün sonra)");
  assert.ok(!cz.derived.includes("audience") && cz.derived.includes("genre") && cz.derived.includes("target"));
  const ev = by("bk:evlilik");
  assert.deepEqual([ev.kitle, ev.tur, ev.zaman, ev.rez], ["Yetişkin", "", "gece", "duyuru bekleniyor"]);
  const text = render("pl:0", makeConfig(), evState(), NOW).text;
  assert.ok(text.includes("Kitle: <i>Yetişkin</i> · Tür: Komedi · Zaman: <i>gündüz+gece</i>"), text); // Tür stated, Kitle (age) derived
  assert.ok(text.includes("Hedef: var (1) · Rez. açılış: 20 Şub Çar 10:00 (19 gün sonra)"), text);
  assert.ok(text.includes("Hedef: <i>yok → tüm seanslar</i>"), text);
  assert.ok(text.includes("Tür: <i>—</i>"), text); // blank Tür of a non-theatre event is shown as a derived dash
  assert.ok(text.includes("Rez. açılış: <i>duyuru bekleniyor</i>"), text);
});

test("one legend line explains the italic marker (list, play and event screens)", () => {
  assert.ok(LEGEND.includes("<i>italik = türetilmiş</i>"));
  for (const route of ["pl:0", `p:${KB}`, `e:${h("vkv:541")}`]) {
    const t = render(route, makeConfig(), evState(), NOW).text;
    assert.equal(t.split("\n").filter((l) => l === LEGEND).length, 1, route);
  }
});

test("derivation rules (port of tiyatro/tags.py)", () => {
  assert.equal(resolveAudience("Komedi", [], null), "Yetişkin"); // no child marker -> Yetişkin
  assert.equal(resolveAudience("Komedi", [], 7), "Çocuk");
  assert.equal(resolveAudience("", [], null, "Çocuk Tiyatrosu Ceviz"), "Çocuk");
  assert.equal(resolveGenre("Yetişkin Oyunu", ["X", ""]), ""); // no "Tiyatro" filler (wave 9)
  assert.equal(resolveGenre("Tiyatro", ["X"]), "");
  assert.equal(resolveGenre("Çocuk Oyunu", ["Ceviz Adam Müzikali"]), "Müzikal");
  assert.equal(resolveGenre("", ["Söyleşi"]), "");
  assert.equal(genreFromText("Dramaturji ve program"), ""); // no substring accidents
  assert.equal(genreFromText("Bir komedi ve dram"), "Komedi"); // earliest keyword
  assert.equal(genreFromText("STAND-UP gecesi"), "Stand-up");
  assert.equal(genreFromText("Müzikal"), "Müzikal");
  assert.equal(timeOfDay([11, 20]), "gündüz+gece");
  assert.equal(timeOfDay([16]), "gündüz");
  assert.equal(timeOfDay([17]), "gece"); // 17:00 is the boundary
  assert.equal(timeOfDay([]), "");
  assert.ok(audienceIsStated("Çocuk Oyunu") && !audienceIsStated("Komedi") && !audienceIsStated("Çocuk Yetişkin"));
  assert.ok(genreIsStated("Komedi") && !genreIsStated("Çocuk Oyunu"));
});

test("Rez. açılış ladder for plays and events", () => {
  const sess = (status, opens, days = 10) => ({ status, opens_at: opens, start: iso(days, 20) });
  const f = (sessions) => playFields({ title: "X", sessions }, undefined, NOW);
  assert.equal(f({ a: sess("upcoming", iso(1, 10)) }).rez, "2 Şub Cmt 10:00 (yarın)");
  assert.equal(f({ a: sess("upcoming", iso(0, 12)) }).rez, "1 Şub Cum 12:00 (bugün)");
  // a play with one session on sale and another opening tomorrow shows the opening (ladder order)
  assert.equal(f({ a: sess("on_sale", null), b: sess("upcoming", iso(1, 10)) }).rez, "2 Şub Cmt 10:00 (yarın)");
  assert.equal(f({ a: sess("on_sale", null) }).rez, "açık");
  assert.equal(f({ a: sess("sold_out", null), b: sess("sold_out", null) }).rez, "tükendi");
  assert.equal(f({ a: sess("sold_out", null), b: sess("unknown", null) }).rez, "—");
  assert.equal(f({ a: sess("upcoming", iso(-1, 10)) }).rez, "—"); // a past opening time is no opening
  assert.equal(f({}).rez, "—");
  assert.ok(f({}).derived.includes("rez"));
  const e = (rec) => eventFields({ title: "T", kind: "Tiyatro", start: iso(9), ...rec }, "x", NOW);
  assert.equal(e({ full: true }).rez, "doldu");
  assert.equal(e({ opens_at: null }).rez, "duyuru bekleniyor");
  assert.equal(e({ opens_at: iso(1, 10) }).rez, "2 Şub Cmt 10:00 (yarın)");
  assert.equal(e({ opens_at: iso(-1), opens_pending: false }).rez, "açık");
  assert.equal(e({ opens_at: iso(-1), opens_pending: true }).rez, "açılmış olabilir");
  assert.ok(!e({ full: true }).derived.includes("rez") && e({ opens_at: iso(-1) }).derived.includes("rez"));
  assert.equal(rezWhen(Date.parse("2030-02-01T00:30:00+03:00"), NOW), "1 Şub Cum 00:30 (bugün)");
});

test("default follow is OFF: new plays and events are unticked and the text says so", () => {
  const cfg = makeConfig();
  delete cfg.plays["kucuk-prens"];
  const items = listItems(cfg, evState(), NOW);
  assert.equal(items.find((i) => i.id === "kucuk-prens").followed, false);
  assert.ok(items.filter((i) => i.kind === "event").every((i) => !i.followed));
  assert.ok(HELP.list.join(" ").includes("takipte DEĞİL başlar"));
  assert.ok(HELP.event.join(" ").includes("takipte DEĞİL başlar"));
  assert.ok(render(`p:${h("kucuk-prens")}`, cfg, evState(), NOW).text.includes("varsayılan olarak takipte değildir"));
  assert.ok(render(`e:${h("vkv:541")}`, cfg, evState(), NOW).text.includes("varsayılan olarak takipte değildir"));
  // an explicit choice still wins over the stored default
  const on = listItems({ ...cfg, event_follow: { "vkv:541": true } }, evState(), NOW).find((i) => i.id === "vkv:541");
  assert.equal(on.followed, true);
  // a record without the field counts as followed (it was ON then), as tiyatro/srcevents.event_views
  const old = evState();
  delete old.source_events["vkv:541"].follow_default;
  assert.equal(listItems(cfg, old, NOW).find((i) => i.id === "vkv:541").followed, true);
});

test("toggles use the idempotent ta / ef routes and lead back to the page they came from", () => {
  const s = evState();
  const cfg = makeConfig();
  const evh = h("vkv:541");
  const r = render("pl:0", cfg, s, NOW);
  const cds = flat(r).map((b) => b.callback_data).filter(Boolean);
  assert.ok(cds.includes(`ta:${KB}:0:0`) && cds.includes(`ta:${h("kucuk-prens")}:1:0`));
  assert.ok(cds.includes(`ef:${evh}:1:0`) && cds.includes(`p:${KB}:0:0`) && cds.includes(`e:${evh}:0`));
  for (const route of [`ef:${evh}:1:0`, `ef:${evh}:0:3`, `ta:${KB}:0:0`, `ta:${KB}:0`, `ef:${evh}:1:i2`]) {
    assert.equal(isMutating(route), true);
    const once = apply(route, cfg, s);
    assert.deepEqual(apply(route, once, s), once, route);
  }
  assert.equal(apply(`ef:${evh}:1:0`, cfg, s).event_follow["vkv:541"], true);
  assert.equal(apply(`ef:${evh}:0:0`, cfg, s).event_follow["vkv:541"], false);
  assert.deepEqual(apply("ef:deadbeef:1:0", cfg, s), cfg);
  assert.equal(viewOf(`ef:${evh}:1:2`), "pl:2");
  assert.equal(viewOf(`ta:${KB}:0:4`), "pl:4");
  assert.equal(viewOf(`ef:${evh}:1:i2`), `e:${evh}:2`);
  assert.equal(viewOf(`ta:${KB}:1:i3`), `p:${KB}:0:3`);
  assert.equal(viewOf(`ta:${KB}:1`), `ta:${KB}:1`);
  assert.equal(viewOf(`ef:${evh}:1`), "pl:0");
  assert.equal(viewOf("eo:2"), "eo:2"); // legacy alias renders the list, never mutates
  // after the switch the list shows the new state
  const after = render(viewOf(`ef:${evh}:1:0`), apply(`ef:${evh}:1:0`, cfg, s), s, NOW);
  assert.ok(after.text.includes("☑ <b>Ceviz Adam"), after.text);
  assert.ok(render("eo:3", cfg, s, NOW).text.includes("Oyunlar ve etkinlikler")); // old message button still works
  assert.ok(render(`ef:${evh}:1:0`, cfg, s, NOW).text.includes("Oyunlar ve etkinlikler")); // old ef button too
});

test("paging: many items -> 6 per page, every page <= 4096 chars and every callback <= 64 bytes", () => {
  const s = makeState();
  for (let i = 0; i < 30; i++) {
    s.plays[`oyun-${i}-${"uzun-slug-".repeat(5)}`] = { title: "Çok uzun oyun adı ".repeat(8), genre: "Dram", tags: [],
      sessions: { a: { status: "upcoming", opens_at: iso(2), start: iso(9 + i, 20) } } };
  }
  s.source_events = {};
  for (let i = 0; i < 40; i++) {
    const id = `bk:2030020${i}-1400-uzun-etkinlik-adi-${i}`;
    s.source_events[id] = { id, source: "basiskele", title: "Çok uzun bir etkinlik adı ".repeat(8), kind: "Yetişkin Tiyatrosu", start: iso(1 + i), follow_default: false };
  }
  const cfg = makeConfig();
  const total = 3 + 30 + 40;
  const pages = Math.ceil(total / 6);
  const seen = new Set();
  for (let p = 0; p < pages; p++) {
    const r = render(`pl:${p}`, cfg, s, NOW);
    assert.ok(r.text.length <= 4096, `page ${p}: ${r.text.length}`);
    assert.ok(r.text.includes(`sayfa ${p + 1}/${pages}`));
    for (const b of flat(r)) {
      if (b.callback_data) assert.ok(bytes(b.callback_data) <= 64, b.callback_data);
      if (b.callback_data?.startsWith("p:") || b.callback_data?.startsWith("e:")) seen.add(b.callback_data.split(":")[1]);
    }
    assert.equal(flat(r).some((b) => b.text === "Sonraki ›"), p < pages - 1);
    assert.equal(flat(r).some((b) => b.text === "‹ Önceki"), p > 0);
  }
  assert.equal(seen.size, total); // every item is reachable exactly once
  assert.ok(render("pl:999", cfg, s, NOW).text.includes(`sayfa ${pages}/${pages}`));
  assert.ok(render("m", cfg, s, NOW).text.length <= 4096);
});

test("order: nearest start, then title, independent of follow state (as the PC list)", () => {
  const cfg = makeConfig();
  const ids = listItems(cfg, evState(), NOW).map((i) => i.id);
  assert.deepEqual(ids, EXPECTED_ORDER);
  // following another item moves nothing
  const cfg2 = makeConfig();
  cfg2.plays["yeni-oyun"] = { active: true, targets: [], tag_overrides: {} };
  cfg2.event_follow = { "vkv:541": true };
  assert.deepEqual(listItems(cfg2, evState(), NOW).map((i) => i.id), ids);
});

test("archived unfollowed plays stay out of the list (as the PC), watched ones stay", () => {
  const s = makeState();
  s.plays["kucuk-prens"].archived = true;
  s.plays["yeni-oyun"].archived = true;
  const cfg = makeConfig();
  cfg.plays["yeni-oyun"] = { active: true, targets: [], tag_overrides: {} };
  const ids = listItems(cfg, s, NOW).map((i) => i.id);
  assert.ok(!ids.includes("kucuk-prens") && ids.includes("yeni-oyun"));
});

// ---- no `seen` list / 🆕 marker (wave 9: discovery is a Telegram notice from the scanner) ----

test("no 🆕 marker or seen bookkeeping: list/main render without it, browsing routes are not mutating", () => {
  const s = evState();
  const cfg = { ...makeConfig(), seen: ["kusursuz-bir-aile"] }; // a leftover key from wave 7 is ignored
  for (const route of ["pl:0", "m"]) {
    const text = render(route, cfg, s, NOW).text;
    assert.ok(!text.includes("🆕 <b>") && !text.includes("Henüz açmadığın"), text);
  }
  assert.ok(!flat(render("pl:0", cfg, s, NOW)).some((b) => b.text.includes("🆕")));
  assert.ok(!LEGEND.includes("🆕"));
  for (const route of ["pl:0", `p:${KB}:0:0`, `e:${h("vkv:541")}:0`, "m", "tl"]) {
    assert.equal(isMutating(route), false, route);
    assert.deepEqual(apply(route, cfg, s), cfg, route); // nothing to write
  }
  assert.ok(listItems(cfg, s, NOW).every((i) => !("isNew" in i)));
});

// ---- links ----

function manySessions(n, withUrl = true) {
  const s = makeState();
  const sessions = {};
  for (let i = 0; i < n; i++) {
    sessions[`s${String(i).padStart(2, "0")}`] = { status: "on_sale", opens_at: null, start: iso(3 + i, 20),
      ...(withUrl ? { url: `https://biletinial.com/oturum/${i}` } : {}) };
  }
  s.plays["kusursuz-bir-aile"].sessions = sessions;
  return s;
}

test("play screen: Oyun sayfası + one Seans URL button per session, paged 8 per page", () => {
  const s = manySessions(19);
  const cfg = makeConfig();
  const r0 = render(`p:${KB}:0:2`, cfg, s, NOW);
  const urls = (r) => flat(r).filter((b) => b.url);
  assert.equal(urls(r0).length, 1 + 8);
  assert.ok(urls(r0)[0].text.includes("Oyun sayfası") && urls(r0)[0].url === "https://biletinial.com/tr-tr/tiyatro/kusursuz-bir-aile");
  assert.ok(urls(r0).slice(1).every((b) => b.text.startsWith("🔗 Seans ") && /biletinial\.com\/oturum\/\d+$/.test(b.url)));
  assert.ok(r0.text.includes("sayfa 1/3, 19 seans"));
  const next = flat(r0).find((b) => b.text === "Seanslar ›");
  assert.equal(next.callback_data, `p:${KB}:1:2`);
  assert.ok(!flat(r0).some((b) => b.text === "‹ Seanslar"));
  assert.ok(flat(r0).some((b) => b.callback_data === "pl:2")); // back to the list page we came from
  const r2 = render(`p:${KB}:2:2`, cfg, s, NOW);
  assert.equal(urls(r2).length, 1 + 3);
  assert.ok(flat(r2).some((b) => b.text === "‹ Seanslar") && !flat(r2).some((b) => b.text === "Seanslar ›"));
  assert.ok(render(`p:${KB}:99`, cfg, s, NOW).text.includes("sayfa 3/3"));
  const all = new Set([0, 1, 2].flatMap((p) => urls(render(`p:${KB}:${p}`, cfg, s, NOW)).slice(1).map((b) => b.url)));
  assert.equal(all.size, 19); // every session reachable
  for (const p of [0, 1, 2]) {
    const r = render(`p:${KB}:${p}`, cfg, s, NOW);
    assert.ok(r.text.length <= 4096);
    assert.ok(flat(r).length <= 100);
    for (const b of flat(r)) if (b.callback_data) assert.ok(bytes(b.callback_data) <= 64);
  }
});

test("play screen: sessions without a page get no button and a hint; unsafe URLs never become buttons", () => {
  const s = manySessions(3, false);
  const r = render(`p:${KB}`, makeConfig(), s, NOW);
  assert.equal(flat(r).filter((b) => b.url).length, 1); // only Oyun sayfası
  assert.ok(r.text.includes("Kendi sayfası olmayan seanslar"));
  const bad = manySessions(2);
  const recs = bad.plays["kusursuz-bir-aile"].sessions;
  recs.s00.url = "javascript:alert(1)";
  recs.s01.url = "https://biletinial.com/a b";
  bad.plays["kusursuz-bir-aile"].url = "ftp://x/y";
  const rb = render(`p:${KB}`, makeConfig(), bad, NOW);
  assert.equal(flat(rb).filter((b) => b.url).length, 0);
  assert.ok(rb.text.length > 0);
  // a play without any state page / sessions still renders
  assert.ok(render(`p:${h("yeni-oyun")}`, makeConfig(), makeState(), NOW).text.includes("Seans bilgisi yok."));
});

test("past sessions are not listed (as the PC detail table)", () => {
  const s = manySessions(2);
  s.plays["kusursuz-bir-aile"].sessions.old = { status: "sold_out", start: iso(-5, 20), url: "https://biletinial.com/eski" };
  const r = render(`p:${KB}`, makeConfig(), s, NOW);
  assert.ok(!flat(r).some((b) => b.url === "https://biletinial.com/eski"));
});

test("event screen: fields, follow switch, Etkinlik sayfası button, legend", () => {
  const s = evState();
  const evh = h("vkv:541");
  const r = render(`e:${evh}:2`, makeConfig(), s, NOW);
  for (const t of ["Ceviz Adam &lt;Müzikali&gt;", "Kaynak: VKV", "Kitle: Çocuk · Tür: <i>Müzikal</i> · Zaman: <i>gündüz</i>",
    "Hedef: <i>—</i> · Rez. açılış: 3 Şub Paz 12:00 (2 gün sonra)", "Takip: ☐ takipte değil"]) {
    assert.ok(r.text.includes(t), `${t}\n${r.text}`);
  }
  const b = flat(r);
  assert.ok(b.some((x) => x.callback_data === `ef:${evh}:1:i2`));
  assert.ok(b.some((x) => x.url === "https://www.vkvfordkultur.org.tr/Etkinlik/541"));
  assert.ok(b.some((x) => x.callback_data === "pl:2"));
  const followed = render(`e:${evh}`, { ...makeConfig(), event_follow: { "vkv:541": true } }, s, NOW);
  assert.ok(flat(followed).some((x) => x.callback_data === `ef:${evh}:0:i0`) && followed.text.includes("Takip: ☑ takipte"));
  assert.ok(render("e:00000000", makeConfig(), s, NOW).text.includes("Etkinlik bulunamadı"));
  // an event that disappeared from the state (stale button) -> not found, with a way back
  assert.ok(render(`e:${evh}`, makeConfig(), makeState(), NOW).text.includes("bulunamadı"));
});

test("Kaynaklar screen: an 'aç' URL button for each source", () => {
  const s = makeState();
  s.sources = {
    biletinial: { name: "Biletinial", ok: true, last_check_at: new Date().toISOString(), items_count: 3 },
    basiskele: { name: "Başiskele Belediyesi", ok: true, last_check_at: new Date().toISOString(), items_count: 4 },
    vkv: { name: "VKV Ford Otosan Gölcük", ok: true, last_check_at: new Date().toISOString(), items_count: 2 },
  };
  s.src_cache = { basiskele: { articles: {
    "https://www.basiskele.bel.tr/a-eski": { pub: "2030-01-05T10:00:00+03:00" },
    "https://www.basiskele.bel.tr/b-yeni": { pub: "2030-01-25T10:00:00+03:00" },
    "javascript:x": { pub: "2031-01-01T00:00:00+03:00" },
  } } };
  for (const route of ["src", "rf"]) {
    const r = render(route, makeConfig(), s, NOW);
    const urls = Object.fromEntries(flat(r).filter((b) => b.url).map((b) => [b.text, b.url]));
    assert.deepEqual(urls, {
      "🔗 Biletinial aç": "https://biletinial.com/tr-tr/mekan/ornek-mekan",
      "🔗 Başiskele aç": "https://www.basiskele.bel.tr/b-yeni",
      "🔗 VKV aç": "https://www.vkvfordkultur.org.tr/Etkinlikler",
    });
    assert.ok(flat(r).some((b) => b.callback_data === "src"));
  }
  // same fallbacks as pc/app_logic.source_links
  assert.deepEqual(sourceLinks({ sources: ["not a url"] }, {}), {
    basiskele: "https://www.basiskele.bel.tr", vkv: "https://www.vkvfordkultur.org.tr/Etkinlikler" });
  // without any source record the three known sources still get a button
  assert.equal(flat(render("src", makeConfig(), makeState(), NOW)).filter((b) => b.url).length, 3);
});

test("texts no longer mention the old behaviour (default-ON, Diğer kaynaklar)", () => {
  const all = [...Object.values(HELP).flat(), LEGEND].join("\n");
  assert.ok(!/Diğer kaynaklar|varsayılan olarak takipte olur|default.?ON/i.test(all));
  assert.ok(/Yenile: tüm kaynakları/.test(HELP.sources[0]) && /Yenile: tüm kaynakları \(Biletinial, Başiskele, VKV\)/.test(HELP.main[1]));
});

test("wave 10: non-theatre source events get Diğer – tür only when certain", () => {
  for (const [t, want] of [["Seramik Atölyesi", "Diğer – Atölye"], ["Yazar Söyleşisi", "Diğer – Söyleşi"],
    ["Konser: Bahar", "Diğer – Konser"], ["Resim Sergisi", "Diğer – Sergi"],
    ["Girişimcilik Eğitimi", "Diğer – Eğitim"], ["Kısa Film Gösterimi", "Diğer – Film"],
    ["Açık Hava Sineması", "Diğer – Film"], ["Örgü Kursu", "Diğer – Eğitim"],
    ["Enerji Semineri", "Diğer – Seminer/Panel"], ["Gençlik Paneli", "Diğer – Seminer/Panel"],
    ["Design Workshop", "Diğer – Atölye"]]) {
    assert.equal(eventGenre("", t), want, t);
  }
  assert.equal(eventGenre("Atölye", "Çocuk Etkinliği"), "Atölye"); // stated category wins
  assert.equal(eventGenre("Söyleşi", "X"), "Söyleşi");
  assert.equal(eventGenre("", "Satır Arası Ebeveynlik"), "");
  assert.equal(eventGenre("Tiyatro", "Atölye Gösterisi"), "");
  assert.equal(eventGenre("", "Oyunculuk Atölyesi Müzikali"), "Müzikal");
  assert.deepEqual(eventFields({ title: "Seramik Atölyesi", kind: "" }, "bk:1", Date.now()).derived.includes("genre"), true);
});

// Wave 15: Özet / Oyuncular / Yapım of source events, as the source states them.
test("event screen shows Özet / Oyuncular / Yapım from the record, escaped and cut safely", () => {
  const s = evState();
  s.source_events["vkv:541"].info = {
    summary: "Ceviz Adam <sahnede>.\r\n\r\n\r\n  İkinci & paragraf", cast: ["Ayşe Yılmaz", "Mehmet Kaya"],
    credits: { "Yönetmen": "Can <Özkan>", "Yazan": "Zeynep Demir" }, duration_min: 60, age_limit: 4, price: "Ücretsiz",
  };
  const r = render(`e:${h("vkv:541")}`, makeConfig(), s, NOW);
  assert.ok(r.text.includes("Süre: 60 dk · Yaş: 4+ · Ücret: Ücretsiz"), r.text);
  assert.ok(r.text.includes("📝 Özet: Ceviz Adam &lt;sahnede&gt;. İkinci &amp; paragraf"), r.text);
  assert.ok(r.text.includes("Oyuncular: Ayşe Yılmaz, Mehmet Kaya"), r.text);
  assert.ok(r.text.includes("Yapım: Yönetmen: Can &lt;Özkan&gt; · Yazan: Zeynep Demir"), r.text);
  assert.ok(!r.text.includes("Kaynakta bilgi yok"));
  assert.ok(bytes(r.text) < 4096);
});

test("event screen says Kaynakta bilgi yok for every section the source did not provide", () => {
  const r = render(`e:${h("bk:20300206-1400-cop-atlas")}`, makeConfig(), evState(), NOW);
  assert.ok(r.text.includes("📝 Özet: Kaynakta bilgi yok"), r.text);
  assert.ok(r.text.includes("Oyuncular: Kaynakta bilgi yok"));
  assert.ok(r.text.includes("Yapım: Kaynakta bilgi yok"));
  assert.ok(!r.text.includes("Süre:") && !r.text.includes("Ücret:"));
});

test("a huge summary and cast cannot push the event screen over the Telegram limit", () => {
  const s = evState();
  s.source_events["vkv:541"].info = { summary: "çok uzun cümle ".repeat(2000),
    cast: Array.from({ length: 200 }, (_, i) => `Oyuncu Numara${i} Soyad`),
    credits: Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`Rol${i}`, "Çok Uzun Bir Kişi Adı ".repeat(5)])) };
  const r = render(`e:${h("vkv:541")}`, makeConfig(), s, NOW);
  assert.ok(bytes(r.text) < 4096, bytes(r.text));
  assert.ok(r.text.includes("…") && r.text.includes("(+194)"));
});

// Wave 17: ONE template for the play (p:) and event (e:) screens.
function sectionLabels(text) {
  return text.split("\n").map((l) => /^(?:📝 )?(Kaynak|Etiketler|Etkinlik|Mekan|Kitle|Hedef|Özet|Oyuncular|Yapım|Takip|Seanslar|Hedefler)\b/.exec(l.replace(/<[^>]+>/g, "").replace(/^🏷 /, ""))?.[1])
    .filter(Boolean);
}

test("wave 17: p: and e: screens use the same sections in the same order and the same placeholders", () => {
  const s = evState();
  s.plays["kusursuz-bir-aile"].tags = [];
  const play = render(`p:${KB}`, makeConfig(), s, NOW).text;
  const ev = render(`e:${h("bk:evlilik")}`, makeConfig(), s, NOW).text;
  const common = ["Kaynak", "Mekan", "Kitle", "Hedef", "Özet", "Oyuncular", "Yapım", "Takip"];
  const pick = (labels) => labels.slice(labels.indexOf("Kaynak")).filter((l) => common.includes(l));
  assert.deepEqual(pick(sectionLabels(play)), common.slice(0, 5).concat(["Oyuncular", "Yapım", "Takip"]));
  assert.deepEqual(pick(sectionLabels(play)), pick(sectionLabels(ev)));
  for (const t of [play, ev]) {
    assert.ok(t.includes("Mekan: <i>Bilgi yok</i>"), t);
    assert.ok(/Kitle: .* · Tür: .* · Zaman: /.test(t) && /Hedef: .* · Rez\. açılış: /.test(t));
  }
  // the event has no stated Özet / cast / credits: the same grey placeholder text as a play without them
  const bare = render(`p:${h("yeni-oyun")}`, makeConfig(), s, NOW).text;
  for (const line of ["📝 Özet: Kaynakta bilgi yok", "Oyuncular: Kaynakta bilgi yok", "Yapım: Kaynakta bilgi yok"]) {
    assert.ok(ev.includes(line) && bare.includes(line), line);
  }
  // a play shows its stated Süre / Yaş / Ücret in the same meta line as an event does
  assert.ok(/Zaman: [^\n]* · Süre: 90 dk · Yaş: 7\+ · Ücret: 150 TL/.test(render(`p:${h("kucuk-prens")}`, makeConfig(), s, NOW).text)
    || /Süre: /.test(play));
});

test("wave 17: a borrowed Özet / cast / credits are italic with one provenance line (p: and e:)", () => {
  const s = evState();
  s.plays["unutmak-kocaeli-bb"] = { title: "Unutmak", genre: "Dram", age_limit: 16, duration_min: 95, summary: "Bir kayıp.",
    cast: ["Ali"], credits: { Yazan: "A B" }, sessions: {} };
  s.source_events["bk:unutmak"] = { id: "bk:unutmak", source: "basiskele", title: "UNUTMAK", kind: "Tiyatro Oyunu", start: iso(4, 20),
    venue: "Türkiye Yüzyılı Gençlik Merkezi", venue_derived: true, follow_default: false, info: { credits: { Sahneleyen: "Kocaeli Şehir Tiyatroları" } } };
  const t = render(`e:${h("bk:unutmak")}`, makeConfig(), s, NOW).text;
  assert.ok(t.includes("📝 Özet: <i>Bir kayıp.</i>") && t.includes("Oyuncular: <i>Ali</i>"));
  assert.ok(t.includes("Yapım: Sahneleyen: Kocaeli Şehir Tiyatroları · <i>Yazan: A B</i>"), t);
  assert.ok(t.includes("Mekan: <i>Türkiye Yüzyılı Gençlik Merkezi</i>"));
  assert.ok(t.includes("<i>ℹ️ Biletinial kaydından (aynı ad): Özet, Oyuncular, Yapım, Süre, Yaş, Tür — “Unutmak”</i>"), t);
  assert.ok(t.includes("Süre: <i>95 dk</i> · Yaş: <i>16+</i>"));
  const play = render(`p:${h("unutmak-kocaeli-bb")}`, makeConfig(), s, NOW).text;
  assert.ok(!play.includes("ℹ️ Biletinial kaydından") && play.includes("📝 Özet: Bir kayıp."));
  assert.ok(bytes(t) < 4096);
});
