import { test } from "node:test";
import assert from "node:assert/strict";
import { h, render, apply, buildIndex, guardLength } from "../src/menu.js";
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
  assert.ok(r.text.includes("biletinial.com/tr-tr/tiyatro/kusursuz-bir-aile"));
  assert.ok(flat(r.keyboard).some((b) => b.callback_data === `ta:${KB}:0`));
  assert.ok(labels(r).includes("Pasif yap"));
  const r2 = render(`p:${KP}`, cfg, st);
  assert.ok(flat(r2.keyboard).some((b) => b.callback_data === `ta:${KP}:1`));
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
  assert.ok(flat(pl.keyboard).some((b) => b.callback_data === `p:${KP}`));
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
