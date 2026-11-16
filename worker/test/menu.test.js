import { test } from "node:test";
import assert from "node:assert/strict";
import { h, render, apply, buildIndex, guardLength, audienceOf, genreLabel, isMutating, viewOf } from "../src/menu.js";
import { makeConfig, makeState } from "./_fixtures.js";

const cfg = makeConfig();
const st = makeState();
const KB = h("kusursuz-bir-aile");
const KP = h("kucuk-prens");
const YN = h("yeni-oyun");

const bytes = (s) => new TextEncoder().encode(s).length;
const flat = (kb) => kb.flat();
const labels = (r) => flat(r.keyboard).map((b) => b.text).join(" | ");
const play = (c, slug = "kusursuz-bir-aile") => c.plays[slug];
const target = (c, id = "y", slug = "kusursuz-bir-aile") => play(c, slug).targets.find((t) => t.id === id);

test("h: 8 hex, deterministic, differs per slug", () => {
  assert.match(h("kusursuz-bir-aile"), /^[0-9a-f]{8}$/);
  assert.equal(h("kusursuz-bir-aile"), h("kusursuz-bir-aile"));
  assert.notEqual(h("a"), h("b"));
  assert.equal(h(""), "811c9dc5"); // FNV-1a offset basis
  assert.equal(h("a"), "e40c292c"); // known FNV-1a 32 vector
});

test("buildIndex resolves hash collisions unambiguously and deterministically", () => {
  const idx = buildIndex(["b", "a", "c"], () => "deadbeef"); // force every slug to collide
  const hashes = [...idx.hashOf.values()];
  assert.equal(new Set(hashes).size, 3);
  assert.equal(idx.hashOf.get("a"), "deadbeef"); // first in sorted order keeps the plain hash
  for (const [slug, hash] of idx.hashOf) assert.equal(idx.slugOf.get(hash), slug);
  const idx2 = buildIndex(["c", "a", "b"], () => "deadbeef");
  assert.deepEqual([...idx2.hashOf], [...idx.hashOf]);
  for (const hash of hashes) assert.match(hash, /^[0-9a-f]{8}$/);
});

test("render m: main menu has the five buttons", () => {
  const r = render("m", cfg, st);
  const l = labels(r);
  for (const w of ["Oyunlar", "Takip listem", "Yaklaşan açılışlar", "Yenile", "Duraklat"]) {
    assert.ok(l.includes(w), w);
  }
  assert.ok(flat(r.keyboard).length >= 5);
  assert.ok(flat(r.keyboard).some((b) => b.callback_data === "pz:1"));
  const paused = render("m", { ...cfg, paused: true }, st);
  assert.ok(labels(paused).includes("Devam"));
  assert.ok(flat(paused.keyboard).some((b) => b.callback_data === "pz:0"));
});

test("render p: play screen has tags, statuses, active toggle, link, escaped title", () => {
  const r = render(`p:${KB}`, cfg, st);
  assert.ok(r.text.includes("Kusursuz Bir Aile &amp; Şükran"));
  assert.ok(r.text.includes("komedi"));
  assert.ok(r.text.includes("105"));
  assert.ok(r.text.includes("✅") && r.text.includes("⏳") && r.text.includes("❌"));
  // wave 7: the page link is a URL button (Oyun sayfası), not an inline text link
  assert.ok(flat(r.keyboard).some((b) => b.text.includes("Oyun sayfası") && b.url === "https://biletinial.com/tr-tr/tiyatro/kusursuz-bir-aile"));
  assert.ok(flat(r.keyboard).some((b) => b.callback_data === `ta:${KB}:0:i0`));
  assert.ok(labels(r).includes("Pasif yap"));
  const r2 = render(`p:${KP}`, cfg, st);
  assert.ok(flat(r2.keyboard).some((b) => b.callback_data === `ta:${KP}:1:i0`));
  assert.ok(r2.text.includes("yeni oyunlar varsayılan olarak takipte değildir"));
  assert.ok(flat(r2.keyboard).some((b) => b.callback_data === `tg:${KP}:y`));
  assert.ok(flat(r2.keyboard).some((b) => b.callback_data === `tg:${KP}:c`));
});

test("every callback_data is <= 64 bytes over all routes", () => {
  const routes = ["m", "pl:0", "pl:1", "tl", "up", "rf", "pz:0", "pz:1"];
  for (const slug of Object.keys(st.plays)) {
    const hh = h(slug);
    routes.push(`p:${hh}`, `ta:${hh}:1`, `tg:${hh}:y`, `tg:${hh}:c`);
    for (const t of play(cfg, slug)?.targets ?? []) routes.push(`ts:${hh}:${t.id}`);
  }
  routes.push(`ts:${KB}:y`, `hd:${KB}:y:wd:1`, `hf:${KB}:y:18`, `ht:${KB}:y:none`, `hx:${KB}:y:300301:1`);
  let n = 0;
  for (const route of routes) {
    const r = render(route, cfg, st);
    assert.equal(typeof r.text, "string");
    for (const b of flat(r.keyboard)) {
      n++;
      assert.ok(bytes(b.callback_data) <= 64, `${route} -> ${b.callback_data}`);
      assert.ok(b.text.length > 0);
    }
  }
  assert.ok(n > 40);
  const c2 = apply(`tg:${KB}:c`, cfg, st);
  for (const b of flat(render(`ts:${KB}:c`, c2, st).keyboard)) assert.ok(bytes(b.callback_data) <= 64);
  // an over-long target id coming from the PC app must not produce an oversize button
  const long = makeConfig();
  long.plays["kusursuz-bir-aile"].targets[0].id = "x".repeat(80);
  for (const route of [`p:${KB}`, `ts:${KB}:${"x".repeat(80)}`]) {
    for (const b of flat(render(route, long, st).keyboard)) assert.ok(bytes(b.callback_data) <= 64);
  }
});

test("render ts: target screen offers people/days/hours/exclusions/delete", () => {
  const r = render(`ts:${KB}:y`, cfg, st);
  const cds = flat(r.keyboard).map((b) => b.callback_data);
  for (const c of [`hp:${KB}:y:1`, `hp:${KB}:y:4`, `hd:${KB}:y:wd:1`, `hd:${KB}:y:we:1`, `hd:${KB}:y:all:1`,
    `hd:${KB}:y:d0:1`, `hd:${KB}:y:d4:0`, `hf:${KB}:y:20`, `hf:${KB}:y:none`, `ht:${KB}:y:20`,
    `hx:${KB}:y:300301:1`, `hx:${KB}:y:300302:0`, `hs:${KB}:y:rm`]) {
    assert.ok(cds.includes(c), c);
  }
});

test("render tl / up / pl", () => {
  const tl = render("tl", cfg, st);
  assert.ok(tl.text.includes("Kusursuz"));
  assert.ok(!tl.text.includes("Küçük Prens")); // inactive play is not on the watchlist
  const up = render("up", cfg, st);
  assert.ok(up.text.includes("Kusursuz"));
  assert.ok(!up.text.includes("Küçük Prens"));
  assert.ok(up.text.includes("20.02.2030 10:00"));
  const pl = render("pl:0", cfg, st);
  assert.ok(flat(pl.keyboard).some((b) => b.callback_data === `p:${KP}:0:0`));
  assert.ok(flat(pl.keyboard).some((b) => b.callback_data === "m"));
});

test("play list paginates 6 per page", () => {
  const big = makeState();
  for (let i = 0; i < 9; i++) big.plays[`oyun-${i}`] = { title: `Oyun ${i}`, sessions: {} };
  const ids = (page) => flat(render(`pl:${page}`, cfg, big).keyboard)
    .filter((b) => b.callback_data.startsWith("p:")).map((b) => b.callback_data);
  assert.equal(ids(0).length, 6);
  assert.equal(ids(1).length, 6);
  assert.equal(new Set([...ids(0), ...ids(1)]).size, 12);
  assert.deepEqual(ids(5), ids(1)); // past-the-end clamps to the last page
  assert.ok(flat(render("pl:0", cfg, big).keyboard).some((b) => b.callback_data === "pl:1"));
});

test("apply tg: creates child/adult target with defaults; no-op if exists", () => {
  const c = apply(`tg:${KP}:c`, cfg, st);
  const t = target(c, "c", "kucuk-prens");
  assert.deepEqual(t, { id: "c", name: "Çocuk", people: 2,
    rule: { days: null, from_time: null, to_time: null, overrides: {}, exclude_dates: [] } });
  assert.deepEqual(apply(`tg:${KP}:c`, c, st), c);
  assert.deepEqual(apply(`tg:${KB}:y`, cfg, st), cfg); // adult already exists
  const y2 = apply(`tg:${YN}:y`, cfg, st); // play only in state -> config entry created
  assert.equal(y2.plays["yeni-oyun"].targets[0].name, "Yetişkin");
  assert.equal(y2.plays["yeni-oyun"].active, true);
});

test("apply ta: set semantics", () => {
  const c = apply(`ta:${KB}:0`, cfg, st);
  assert.equal(play(c).active, false);
  assert.equal(play(apply(`ta:${KB}:1`, c, st)).active, true);
  assert.equal(apply(`ta:${YN}:1`, cfg, st).plays["yeni-oyun"].active, true);
  assert.deepEqual(apply(`ta:${KB}:2`, cfg, st), cfg);
});

test("apply hp: only 1-4", () => {
  assert.equal(target(apply(`hp:${KB}:y:4`, cfg, st)).people, 4);
  assert.equal(target(apply(`hp:${KB}:y:1`, cfg, st)).people, 1);
  for (const n of ["5", "0", "-1", "2.5", "x", ""]) {
    assert.deepEqual(apply(`hp:${KB}:y:${n}`, cfg, st), cfg, n);
  }
});

test("apply hd: day sets and single-day toggles (0=Mon..6=Sun)", () => {
  assert.deepEqual(target(apply(`hd:${KB}:y:wd`, cfg, st)).rule.days, [0, 1, 2, 3, 4]);
  assert.deepEqual(target(apply(`hd:${KB}:y:wd:1`, cfg, st)).rule.days, [0, 1, 2, 3, 4]);
  assert.deepEqual(target(apply(`hd:${KB}:y:we:1`, cfg, st)).rule.days, [5, 6]);
  assert.equal(target(apply(`hd:${KB}:y:all:1`, cfg, st)).rule.days, null);
  assert.deepEqual(target(apply(`hd:${KB}:y:d0:1`, cfg, st)).rule.days, [0, 4, 5]);
  assert.deepEqual(target(apply(`hd:${KB}:y:d4:0`, cfg, st)).rule.days, [5]);
  assert.deepEqual(target(apply(`hd:${KB}:y:d6:0`, cfg, st)).rule.days, [4, 5]);
  const all = apply(`hd:${KB}:y:all:1`, cfg, st);
  assert.deepEqual(target(apply(`hd:${KB}:y:d2:0`, all, st)).rule.days, [0, 1, 3, 4, 5, 6]);
  assert.equal(target(apply(`hd:${KB}:y:d2:1`, all, st)).rule.days, null);
  const six = apply(`hd:${KB}:y:d2:0`, all, st);
  assert.equal(target(apply(`hd:${KB}:y:d2:1`, six, st)).rule.days, null); // 7 days normalise to null
  assert.deepEqual(apply(`hd:${KB}:y:d7:1`, cfg, st), cfg);
  assert.deepEqual(apply(`hd:${KB}:y:d1:2`, cfg, st), cfg);
  assert.deepEqual(apply(`hd:${KB}:y:xx:1`, cfg, st), cfg);
  assert.deepEqual(target(apply(`hd:${KB}:y:wd`, cfg, st)).rule.overrides, { "5": { from: "14:00", to: null } });
});

test("apply hf / ht: from_time / to_time, overrides untouched", () => {
  const f = apply(`hf:${KB}:y:20`, cfg, st);
  assert.equal(target(f).rule.from_time, "20:00");
  assert.equal(target(apply(`hf:${KB}:y:none`, cfg, st)).rule.from_time, null);
  assert.equal(target(apply(`hf:${KB}:y:18`, cfg, st)).rule.from_time, "18:00");
  assert.equal(target(apply(`ht:${KB}:y:22`, cfg, st)).rule.to_time, "22:00");
  assert.equal(target(apply(`ht:${KB}:y:none`, apply(`ht:${KB}:y:22`, cfg, st), st)).rule.to_time, null);
  assert.deepEqual(target(f).rule.overrides, { "5": { from: "14:00", to: null } });
  for (const bad of ["25", "ab", "7x", ""]) {
    assert.deepEqual(apply(`hf:${KB}:y:${bad}`, cfg, st), cfg, bad);
  }
});

test("apply hx: stateless exclude/include of an explicit yymmdd date", () => {
  const c = apply(`hx:${KB}:y:300301:1`, cfg, null); // no state needed
  assert.deepEqual(target(c).rule.exclude_dates, ["2030-03-01", "2030-03-02"]);
  const c3 = apply(`hx:${KB}:y:300303:1`, c, st);
  assert.deepEqual(target(c3).rule.exclude_dates, ["2030-03-01", "2030-03-02", "2030-03-03"]);
  assert.deepEqual(target(apply(`hx:${KB}:y:300301:0`, c, st)).rule.exclude_dates, ["2030-03-02"]);
  assert.deepEqual(target(apply(`hx:${KB}:y:300302:0`, cfg, st)).rule.exclude_dates, []);
  // invalid dates / flags leave the config unchanged
  for (const bad of ["", "1", "30030", "3003011", "300230", "301301", "300332", "abcdef", "000000"]) {
    assert.deepEqual(apply(`hx:${KB}:y:${bad}:1`, cfg, st), cfg, bad);
  }
  assert.deepEqual(apply(`hx:${KB}:y:300301:2`, cfg, st), cfg);
  assert.deepEqual(apply(`hx:${KB}:y:300301`, cfg, st), cfg);
});

test("render hx buttons carry the date: a stale button still means the labelled date", () => {
  const before = flat(render(`ts:${KB}:y`, cfg, st).keyboard).filter((b) => b.callback_data.startsWith("hx:"));
  const mar3 = before.find((b) => b.text.includes("03.03"));
  assert.equal(mar3.callback_data, `hx:${KB}:y:300303:1`);
  // the session list changes after the screen was rendered (a session is removed/inserted)
  const st2 = makeState();
  delete st2.plays["kusursuz-bir-aile"].sessions.s1;
  st2.plays["kusursuz-bir-aile"].sessions.s9 = { status: "on_sale", start: "2030-03-02T10:00:00+03:00" };
  const out = apply(mar3.callback_data, cfg, st2);
  assert.deepEqual(target(out).rule.exclude_dates, ["2030-03-02", "2030-03-03"]);
});

test("watchlist is paged: 40 watched plays -> every page <= 4096 chars, valid HTML, buttons <= 64 bytes", () => {
  const c = makeConfig();
  const big = makeState();
  for (let i = 0; i < 40; i++) {
    const slug = `uzun-oyun-adi-${i}`;
    big.plays[slug] = { title: `Çok Uzun Bir Oyun Adı & Alt Başlık ${"x".repeat(60)} ${i}`, sessions: {} };
    c.plays[slug] = { active: true, tag_overrides: {}, targets: [
      { id: "y", name: "Yetişkin", people: 2, rule: { days: [0, 2, 4], from_time: "18:00", to_time: "22:00",
        overrides: { "5": { from: "14:00", to: null } }, exclude_dates: ["2030-01-01"] } },
      { id: "c", name: "Çocuk", people: 3, rule: { days: null, from_time: null, to_time: null, overrides: {}, exclude_dates: [] } },
    ] };
  }
  const first = render("tl", c, big);
  assert.ok(flat(first.keyboard).some((b) => b.callback_data === "tl:1"));
  const pageCount = 6; // 41 watched plays / 8 per page
  for (let p = 0; p < pageCount; p++) {
    const r = render(`tl:${p}`, c, big);
    assert.ok(r.text.length <= 4096, `page ${p}: ${r.text.length}`);
    assert.equal(r.text.match(/<b>/g)?.length, r.text.match(/<\/b>/g)?.length);
    for (const b of flat(r.keyboard)) assert.ok(bytes(b.callback_data) <= 64);
    assert.ok(flat(r.keyboard).filter((b) => b.callback_data.startsWith("p:")).length <= 8);
  }
  assert.ok(flat(render("tl:5", c, big).keyboard).every((b) => b.callback_data !== "tl:6"));
  assert.deepEqual(render("tl:99", c, big), render("tl:5", c, big)); // clamps
});

test("guardLength: render text is always <= 4096 and keeps HTML balanced", () => {
  const long = { text: `<b>Baslik</b>\n<a href="https://x.example/a">link</a> ` + "<b>" + "satir &amp; <i>egik</i>\n".repeat(600) + "</b>",
    keyboard: [] };
  const g = guardLength(long);
  assert.ok(g.text.length <= 4096);
  assert.ok(g.text.endsWith("</b>"));
  assert.equal(g.text.match(/<b>/g).length, g.text.match(/<\/b>/g).length);
  assert.equal(guardLength({ text: "kisa", keyboard: [] }).text, "kisa");
  // property: every route over a play with an enormous target list stays within the limit
  const c = makeConfig();
  c.plays["kusursuz-bir-aile"].targets = Array.from({ length: 200 }, (_, i) => ({
    id: `t${i}`, name: `Hedef ${"ü".repeat(30)} ${i}`, people: 2,
    rule: { days: [1], from_time: "10:00", to_time: null, overrides: {}, exclude_dates: [] } }));
  const s2 = makeState();
  for (let i = 0; i < 300; i++) s2.plays["kusursuz-bir-aile"].sessions[`z${i}`] = { status: "upcoming",
    opens_at: "2030-02-20T10:00:00+03:00", start: `2030-05-${String(1 + (i % 28)).padStart(2, "0")}T20:00:00+03:00` };
  for (const route of ["m", "pl:0", `p:${KB}`, `ts:${KB}:t5`, "tl", "up", "rf", `ta:${KB}:1`]) {
    assert.ok(render(route, c, s2).text.length <= 4096, route);
  }
});

test("apply hs rm / pz", () => {
  const c = apply(`hs:${KB}:y:rm`, cfg, st);
  assert.equal(play(c).targets.length, 0);
  assert.deepEqual(apply(`hs:${KB}:y:rm`, c, st), c);
  assert.equal(apply("pz:1", cfg, st).paused, true);
  assert.equal(apply("pz:0", { ...cfg, paused: true }, st).paused, false);
  assert.deepEqual(apply("pz", cfg, st), cfg);
});

test("every mutating route is idempotent and never mutates input", () => {
  const routes = [
    `ta:${KB}:0`, `ta:${KB}:1`, `ta:${KP}:1`, `tg:${KP}:y`, `tg:${KP}:c`, `tg:${YN}:c`, `tg:${KB}:y`,
    `hp:${KB}:y:3`, `hp:${KB}:y:9`, `hd:${KB}:y:wd`, `hd:${KB}:y:we:1`, `hd:${KB}:y:all:0`,
    `hd:${KB}:y:d0:1`, `hd:${KB}:y:d4:0`, `hf:${KB}:y:18`, `hf:${KB}:y:none`, `ht:${KB}:y:22`,
    `ht:${KB}:y:none`, `hx:${KB}:y:300301:1`, `hx:${KB}:y:300301:0`, `hx:${KB}:y:300302:1`, `hx:${KB}:y:300302:0`,
    `hs:${KB}:y:rm`, "pz:1", "pz:0",
  ];
  for (const route of routes) {
    const before = JSON.stringify(cfg);
    const once = apply(route, cfg, st);
    assert.equal(JSON.stringify(cfg), before, `${route} mutated input`);
    const twice = apply(route, once, st);
    assert.deepEqual(twice, once, route);
  }
});

test("unknown play hash / target id / malformed -> unchanged copy, bulunamadi screen, no throw", () => {
  const bad = [
    "p:00000000", "ta:00000000:1", "tg:00000000:y", `hp:${KB}:nope:2`, `hd:${KB}:nope:wd`,
    `hf:${KB}:nope:18`, `hx:${KB}:nope:300301:1`, `hs:${KB}:nope:rm`, `ts:${KB}:nope`,
    "", "zz", "p", "p:", `hp:${KB}`, "ta:", ":::",
  ];
  for (const route of bad) {
    const out = apply(route, cfg, st);
    assert.deepEqual(out, cfg, route);
    assert.notEqual(out, cfg, "must be a copy");
    const r = render(route, cfg, st);
    assert.ok(/bulunamadı/.test(r.text), route);
    assert.ok(flat(r.keyboard).some((b) => b.callback_data === "m"), route);
  }
  assert.deepEqual(apply("pz:2", cfg, st), cfg);
  assert.ok(/Oyun bulunamadı/.test(render("p:00000000", cfg, st).text));
  assert.doesNotThrow(() => render("p:x", null, null));
  assert.doesNotThrow(() => apply("p:x", {}, {}));
  assert.doesNotThrow(() => render("m", {}, {}));
  assert.doesNotThrow(() => apply("hd:aaaaaaaa:y:wd", null, null));
});

test("final review C2: to_time is inclusive and the summary says \"'e kadar\"", () => {
  const c = apply(`ht:${KB}:y:20`, cfg, st);
  const text = render(`ts:${KB}:y`, c, st).text;
  assert.ok(text.includes("20:00'ye kadar"), text);
  assert.ok(!text.includes("öncesi"));
  assert.ok(labels(render(`ts:${KB}:y`, c, st)).includes("✓ ≤20:00"));
  const c2 = apply(`ht:${KB}:y:18`, cfg, st);
  assert.ok(render(`ts:${KB}:y`, c2, st).text.includes("18:00'e kadar"));
});

// wave 4 A1: a followed play without targets watches every session; no default target
test("ta:<h>:1 on a play without targets creates no default target", () => {
  const st2 = structuredClone(st);
  st2.plays["kucuk-prens"].tags = ["çocuk"];
  const c = apply(`ta:${KP}:1`, cfg, st2);
  assert.deepEqual(play(c, "kucuk-prens"), { active: true, tag_overrides: {}, targets: [] });
  assert.deepEqual(apply(`ta:${KP}:1`, c, st2), c); // idempotent
  const y = apply(`ta:${YN}:1`, cfg, st2);
  assert.deepEqual(y.plays["yeni-oyun"].targets, []);
  assert.deepEqual(play(apply(`ta:${KP}:0`, cfg, st2), "kucuk-prens").targets, []);
  assert.deepEqual(play(apply(`ta:${KB}:1`, cfg, st2)).targets, play(cfg).targets);
});

test("active play without targets says all sessions are watched (play screen + watchlist)", () => {
  const c = structuredClone(cfg);
  c.plays["kucuk-prens"].active = true;
  const info = "ℹ️ hedef yok → tüm seanslar izleniyor";
  assert.ok(render(`p:${KP}`, c, st).text.includes(info));
  assert.ok(render("tl", c, st).text.includes(info));
  assert.ok(!render(`p:${KB}`, c, st).text.includes(info));
  assert.ok(!render(`p:${KP}`, cfg, st).text.includes(info)); // inactive: no info line
  assert.ok(!render(`p:${KP}`, c, st).text.includes("bildirim gelmez"));
});

test("final review M8: the paused screen says alerts raised while paused are discarded", () => {
  const text = render("pz:1", { ...cfg, paused: true }, st).text;
  assert.ok(text.includes("Duraklatılmışken oluşan bildirimler atılır"), text);
  assert.ok(!render("m", cfg, st).text.includes("atılır"));
});

// ---- wave 3a: summary + cast on the play screen ----
test("play screen shows a short summary and the first 6 cast names (+N)", () => {
  const s = makeState();
  const e = s.plays["kusursuz-bir-aile"];
  e.summary = "Bir <konu> & özet.\n\nİkinci paragraf " + "uzun ".repeat(100);
  e.cast = ["Ali <A>", "Ece", "Can", "Deniz", "Efe", "Fulya", "Gül", "Hakan"];
  const t = render(`p:${KB}`, cfg, s).text;
  assert.ok(t.includes("Bir &lt;konu&gt; &amp; özet. İkinci paragraf"));
  assert.ok(!t.includes("<konu>"));
  const line = t.split("\n").find((l) => l.startsWith("📝"));
  assert.ok(line.length <= 280 && line.endsWith("…"), line.length);
  assert.ok(t.includes("Oyuncular: Ali &lt;A&gt;, Ece, Can, Deniz, Efe, Fulya (+2)"));
  assert.ok(t.indexOf("Oyuncular:") < t.indexOf("Seanslar"));
});

test("wave 12: the play-screen summary is one clean line (CR, nbsp, blank-line runs collapse)", () => {
  const s = makeState();
  s.plays["kusursuz-bir-aile"].summary =
    "\r\n\r\n  Ilk satir  \r\n \r\n \r\n\r\n\r\nIkinci satir\r\n\n\n  ";
  const t = render(`p:${KB}`, cfg, s).text;
  const line = t.split("\n").find((l) => l.startsWith("📝"));
  assert.equal(line, "📝 Ilk satir Ikinci satir");
  assert.ok(!/\n\n\n/.test(t) && !t.includes("\r") && !t.includes(" "));
});

test("play screen without summary/cast (or malformed ones) shows neither line", () => {
  for (const bad of [{}, { summary: null, cast: [] }, { summary: 5, cast: "x" }, { summary: "  ", cast: [null, 3] }]) {
    const s = makeState();
    Object.assign(s.plays["kusursuz-bir-aile"], bad);
    const t = render(`p:${KB}`, cfg, s).text;
    assert.ok(!t.includes("Oyuncular:") && !t.includes("📝"), JSON.stringify(bad));
  }
});

test("play screen stays <= 4096 chars with huge summary and cast", () => {
  const s = makeState();
  const e = s.plays["kusursuz-bir-aile"];
  e.summary = "ü".repeat(20000);
  e.cast = Array.from({ length: 500 }, (_, i) => `Oyuncu ${"ş".repeat(60)} ${i}`);
  const r = render(`p:${KB}`, cfg, s);
  assert.ok(r.text.length <= 4096);
  assert.ok(r.text.includes("(+494)"));
});

// ---- wave 3c: reservation-portal line ---------------------------------------------

const withPortals = (portals) => ({ ...makeState(), portals });
const rec = (over = {}) => ({ signature: "s".repeat(40), empty: true, lines: [], items: [],
  first_seen: "2026-11-13T09:00:00+03:00", last_change: "2026-11-13T09:00:00+03:00",
  last_check: "2026-11-13T09:00:00+03:00", fail_streak: 0, ...over });

test("portal: main menu and upcoming screen show the Başiskele line (empty)", () => {
  const s = withPortals({ basiskele: rec() });
  for (const route of ["m", "up", "pz:1"]) {
    const r = render(route, cfg, s);
    assert.ok(r.text.includes("🏛️ Başiskele portalı: boş"), route);
  }
});

test("portal: open with N items", () => {
  const items = [{ title: "A", url: "u" }, { title: "B", url: "v" }, { title: "C", url: "w" }];
  const s = withPortals({ basiskele: rec({ empty: false, items }) });
  assert.ok(render("m", cfg, s).text.includes("🏛️ Başiskele portalı: açık (3)"));
  assert.ok(render("up", cfg, s).text.includes("🏛️ Başiskele portalı: açık (3)"));
  const none = withPortals({ basiskele: rec({ empty: false, items: [] }) });
  const t = render("m", cfg, none).text;
  assert.ok(t.includes("🏛️ Başiskele portalı: açık") && !t.includes("açık ("));
});

test("portal: unknown, missing or garbage state shows no line", () => {
  const garbage = [undefined, null, [], "x", { basiskele: null }, { basiskele: "x" },
    { basiskele: { empty: null } }, { basiskele: {} }, { basiskele: { empty: "yes" } }];
  for (const p of garbage) {
    for (const route of ["m", "up"]) {
      const r = render(route, cfg, { ...makeState(), portals: p });
      assert.ok(!r.text.includes("🏛️"), `${route} ${JSON.stringify(p)}`);
    }
  }
  assert.ok(!render("m", cfg, makeState()).text.includes("🏛️"));
});

test("portal: other ids use their (escaped, truncated) name; at most 5 lines; sorted", () => {
  const portals = {};
  for (let i = 0; i < 8; i++) portals[`p${i}`] = rec({ name: `Portal <${i}> & ${"x".repeat(100)}` });
  const t = render("m", cfg, withPortals(portals)).text;
  const lines = t.split("\n").filter((l) => l.startsWith("🏛️"));
  assert.equal(lines.length, 5);
  assert.ok(lines[0].includes("Portal &lt;0&gt; &amp;") && !lines[0].includes("<0>"));
  assert.ok(lines[0].length < 80);
});

test("portal: line does not break the 4096 guard on the up screen", () => {
  const s = withPortals({ basiskele: rec({ empty: false, items: [{ title: "x", url: "y" }] }) });
  for (let i = 0; i < 40; i++) {
    s.plays[`oyun-${i}`] = { title: `Oyun ${i} ` + "u".repeat(100), removed: false, tags: [],
      sessions: { a: { status: "upcoming", opens_at: "2030-02-20T10:00:00+03:00", start: "2030-03-02T20:00:00+03:00" } } };
  }
  const c = makeConfig();
  for (let i = 0; i < 40; i++) c.plays[`oyun-${i}`] = { active: true, targets: [], tag_overrides: {} };
  const r = render("up", c, s);
  assert.ok(r.text.length <= 4096);
  assert.ok(r.text.includes("🏛️ Başiskele portalı"));
});

test("portal: apply never touches config.portals", () => {
  const c = makeConfig();
  c.portals = [{ id: "basiskele", name: "Başiskele Belediyesi rezervasyon",
    url: "https://rezervasyon.basiskele.bel.tr/etkinlikler", enabled: true }];
  const before = JSON.stringify(c.portals);
  for (const route of ["pz:1", `ta:${KP}:1`, `tg:${KB}:c`, `hp:${KB}:y:3`, `hs:${KB}:y:rm`, `hd:${KB}:y:wd`]) {
    assert.equal(JSON.stringify(apply(route, c, st).portals), before, route);
  }
  const noPortals = apply("pz:1", makeConfig(), st);
  assert.ok(!("portals" in noPortals));
});

// ---- wave 4 A2: Kitle and Tür are separate lines on the play screen ----
test("audienceOf / genreLabel mirror tiyatro/tags.py", () => {
  assert.equal(audienceOf("Çocuk Oyunu", [], null), "Çocuk");
  assert.equal(audienceOf("YETİŞKİN OYUNU", ["çocuk"], 5), "Yetişkin");
  assert.equal(audienceOf("Komedi", ["çocuk"], null), "Çocuk");
  assert.equal(audienceOf("Komedi", [], 7), "Çocuk");
  assert.equal(audienceOf("Komedi", [], 13), "Yetişkin");
  assert.equal(audienceOf("Komedi", [], null), "");
  assert.equal(genreLabel("Yetişkin Oyunu"), "");
  assert.equal(genreLabel("Komedi"), "Komedi");
  assert.equal(genreLabel("Çocuk Müzikali"), "Müzikali");
});

test("play screen shows Kitle and Tür, never the audience as the genre", () => {
  const s = makeState();
  const e = s.plays["kusursuz-bir-aile"];
  e.genre = "Komedi"; e.tags = ["çocuk"]; e.age_limit = 7;
  let text = render(`p:${KB}`, cfg, s).text;
  assert.ok(text.includes("Kitle: <i>Çocuk</i> · Tür: Komedi"), text); // Kitle derived (tag/age), Tür stated
  e.genre = "Yetişkin Oyunu"; e.tags = []; e.age_limit = 16;
  text = render(`p:${KB}`, cfg, s).text;
  assert.ok(text.includes("Kitle: Yetişkin"), text); // stated by the page: not italic
  assert.ok(text.includes("Tür: <i>—</i>"), text); // wave 9: no "Tiyatro" filler, as the PC list
});

// ---- wave 4 B: Kaynaklar screen ----
import { sourceViews, eventViews, HELP } from "../src/menu.js";

function srcState(extra = {}) {
  const s = makeState();
  const now = Date.now();
  const iso = (msAgo) => new Date(now - msAgo).toISOString();
  s.sources = {
    biletinial: { name: "Biletinial", ok: true, last_check_at: iso(2 * 60e3), last_ok_at: iso(2 * 60e3), last_change_at: iso(3600e3), items_count: 6, error: null },
    basiskele: { name: "Başiskele Belediyesi", ok: true, last_check_at: iso(45 * 60e3), last_ok_at: iso(45 * 60e3), last_change_at: iso(86400e3), items_count: 4, error: null },
    vkv: { name: "VKV Ford Otosan Gölcük", ok: false, last_check_at: iso(60e3), last_ok_at: iso(7200e3), last_change_at: null, items_count: 3, error: "Timeout" },
    ...extra,
  };
  return s;
}

test("sourceViews: ok / stale (>30 min) / failed icons", () => {
  const v = sourceViews(srcState());
  assert.deepEqual(v.map((x) => [x.id, x.icon]), [["biletinial", "✅"], ["basiskele", "⚠️"], ["vkv", "❌"]]);
  assert.equal(v[2].error, "Timeout");
  assert.deepEqual(sourceViews(makeState()), []);
  assert.deepEqual(sourceViews({ sources: "x" }), []);
});

test("src screen shows icons, last check/update, counts, legend, explanation", () => {
  const r = render("src", cfg, srcState());
  for (const t of ["✅ <b>Biletinial</b>", "⚠️ <b>Başiskele Belediyesi</b>", "❌ <b>VKV Ford Otosan Gölcük</b>",
    "Son kontrol:", "Son güncelleme:", "6 öğe", "Hata: Timeout", "30 dakikadan eski"]) {
    assert.ok(r.text.includes(t), `${t}\n${r.text}`);
  }
  assert.ok(labels(r).includes("‹ Ana menü") || r.keyboard.flat().length > 0);
  assert.ok(flat(r.keyboard).some((b) => b.callback_data === "src"));
});

test("rf screen reports per-source results and says the scan was requested", () => {
  const r = render("rf", cfg, srcState());
  assert.ok(r.text.includes("Tarama istendi") && r.text.includes("✅ <b>Biletinial</b>"), r.text);
  assert.ok(render("rf", cfg, makeState()).text.includes("Henüz kaynak bilgisi yok"));
});

test("src screen escapes HTML in names/errors and stays under 4096 chars", () => {
  const s = srcState({ evil: { name: "<b>x</b>&", ok: false, last_check_at: new Date().toISOString(), error: "<script>" } });
  const r = render("src", cfg, s);
  assert.ok(!r.text.includes("<script>") && r.text.includes("&lt;script&gt;"));
  assert.ok(r.text.length <= 4096);
});

test("main menu has the Kaynaklar button", () => {
  assert.ok(flat(render("m", cfg, st).keyboard).some((b) => b.callback_data === "src"));
});

// ---- wave 4 E: Diğer kaynaklar + per-event follow switch ----
function evState() {
  const s = makeState();
  const fut = (days, hh = 20) => new Date(Date.now() + days * 86400e3 + hh * 0).toISOString();
  s.source_events = {
    "vkv:541": { id: "vkv:541", source: "vkv", title: "Ceviz Adam <Müzikali>", kind: "Çocuk Tiyatrosu", start: fut(10),
      opens_at: fut(2), opens_pending: true, full: false, audience: "Çocuk", genre: "Müzikal", follow_default: true },
    "bk:20301104-1400-cop-atlas": { id: "bk:20301104-1400-cop-atlas", source: "basiskele", title: "Çöp Atlas",
      kind: "Çocuk Tiyatrosu", start: fut(5), opens_at: null, full: null, audience: "Çocuk", genre: "", follow_default: true },
    "bk:old": { id: "bk:old", source: "basiskele", title: "Geçmiş", start: fut(-3), follow_default: true },
    "bk:ws": { id: "bk:ws", source: "basiskele", title: "Atölye", kind: "", start: fut(7), follow_default: false },
  };
  return s;
}
const EVID = h; // route hashes are h() of the event id (collisions are probed like play slugs)

test("eventViews: future events by start, explicit choice beats the default", () => {
  const s = evState();
  const v = eventViews({ event_follow: { "bk:ws": true, "vkv:541": false } }, s);
  assert.deepEqual(v.map((x) => [x.id, x.followed]),
    [["bk:20301104-1400-cop-atlas", true], ["bk:ws", true], ["vkv:541", false]]);
  assert.deepEqual(eventViews({}, s).map((x) => x.followed), [true, false, true]);
  assert.deepEqual(eventViews({}, makeState()), []);
});

// ---- wave 4 G: every screen explains its buttons in 1-2 plain lines ----
test("every screen carries 1-2 plain explanation lines right under its title (italic is reserved for derived values)", () => {
  const s = srcState();
  Object.assign(s, evState());
  s.sources = srcState().sources;
  const routes = { m: HELP.main, "pl:0": HELP.list, [`p:${KB}`]: HELP.play, [`ts:${KB}:y`]: HELP.target,
    tl: HELP.watchlist, up: HELP.upcoming, src: HELP.sources, rf: HELP.sources, [`e:${h("vkv:541")}`]: HELP.event };
  for (const [route, lines] of Object.entries(routes)) {
    const text = render(route, cfg, s).text;
    assert.ok(lines.length >= 1 && lines.length <= 2, route);
    const split = text.split("\n");
    for (const l of lines) {
      assert.ok(split.includes(l), `${route} lacks: ${l}`);
      assert.ok(!l.includes("<i>"), l);
    }
    const pos = split.indexOf(lines[0]);
    assert.ok(pos >= 1 && pos <= 4, `${route}: help should sit under the title (line ${pos})`);
    assert.ok(text.length <= 4096, route);
  }
});

test("main menu explains Yenile and rf shows the per-source results", () => {
  assert.ok(HELP.main.join(" ").includes("Yenile: tüm kaynakları (Biletinial, Başiskele, VKV) şimdi tarar ve listeyi günceller"));
  const r = render("rf", cfg, srcState());
  for (const icon of ["✅ <b>Biletinial", "⚠️ <b>Başiskele", "❌ <b>VKV"]) assert.ok(r.text.includes(icon), r.text);
});

test("explanations do not push a long play screen over 4096 characters", () => {
  const s = makeState();
  const e = s.plays["kusursuz-bir-aile"];
  e.summary = "Özet. ".repeat(400);
  e.cast = Array.from({ length: 80 }, (_, i) => `Oyuncu Adı ${i}`);
  assert.ok(render(`p:${KB}`, cfg, s).text.length <= 4096);
  assert.ok(render(`p:${KB}`, cfg, s).text.includes("ℹ️ Aktif yap"));
});

test("sourceViews: a stored program warning turns a fresh check into a visible ⚠️ line", () => {
  const w = "Program haberi bulunamadı (başlık değişmiş olabilir)";
  const iso = new Date(Date.now() - 60e3).toISOString();
  const st = srcState({ basiskele: { name: "Başiskele Belediyesi", ok: true, last_check_at: iso, last_ok_at: iso, last_change_at: iso, items_count: 0, error: null, warning: w } });
  const v = sourceViews(st).find((x) => x.id === "basiskele");
  assert.equal(v.icon, "⚠️");
  assert.equal(v.warning, w);
  assert.ok(render("src", cfg, st).text.includes("başlık değişmiş olabilir"));
  st.sources.basiskele.warning = null;
  assert.equal(sourceViews(st).find((x) => x.id === "basiskele").icon, "✅");
});
