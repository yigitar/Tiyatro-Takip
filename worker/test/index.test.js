import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { handle } from "../src/index.js";
import { h } from "../src/menu.js";
import { makeConfig, makeState } from "./_fixtures.js";

const KB = h("kusursuz-bir-aile");
const TOKEN = "123456:SECRET-BOT-TOKEN";
const GH_TOKEN = "ghp_SECRETGITHUBTOKEN";
const ENV = {
  TELEGRAM_BOT_TOKEN: TOKEN, TELEGRAM_CHAT_ID: "4242", TIYATRO_GH_TOKEN: GH_TOKEN,
  WEBHOOK_SECRET: "whsec", GITHUB_REPO: "yigitar/Tiyatro-Takip",
};

const b64 = (obj) => Buffer.from(JSON.stringify(obj, null, 1), "utf8").toString("base64");
const jsonRes = (status, body) => new Response(body === undefined ? null : JSON.stringify(body), { status });

// Fake fetch: in-memory data branch + Telegram recorder.
function makeFake({ config = makeConfig(), state = makeState(), conflicts = 0, concurrent = null, ghFail = false,
  tgFail = null } = {}) {
  const files = { "config.json": { data: config, sha: "c1" }, "state.json": { data: state, sha: "s1" } };
  const calls = [];
  let shaN = 1;
  let remainingConflicts = conflicts;
  const fake = async (url, init = {}) => {
    const u = String(url);
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url: u, method, body, headers: init.headers });
    if (u.startsWith("https://api.github.com/")) {
      if (ghFail) throw new Error(`boom ${init.headers?.Authorization}`);
      const path = u.match(/contents\/([^?]+)/)?.[1];
      if (path && method === "GET") {
        const f = files[path];
        if (!f) return jsonRes(404, { message: "Not Found" });
        return jsonRes(200, { content: b64(f.data).replace(/(.{60})/g, "$1\n"), sha: f.sha, encoding: "base64" });
      }
      if (path && method === "PUT") {
        if (remainingConflicts > 0) {
          remainingConflicts--;
          if (concurrent) { // another writer (the PC app) commits first
            const cur = JSON.parse(JSON.stringify(files[path].data));
            concurrent(cur);
            files[path] = { data: cur, sha: `c${++shaN}` };
          }
          return jsonRes(409, { message: "conflict" });
        }
        if (body.sha !== files[path]?.sha) return jsonRes(409, { message: "sha mismatch" });
        files[path] = { data: JSON.parse(Buffer.from(body.content, "base64").toString("utf8")), sha: `c${++shaN}` };
        calls[calls.length - 1].written = files[path].data;
        calls[calls.length - 1].raw = Buffer.from(body.content, "base64").toString("utf8");
        return jsonRes(200, { content: { sha: files[path].sha } });
      }
      if (u.includes("/dispatches")) return new Response(null, { status: 204 });
    }
    if (u.startsWith("https://api.telegram.org/")) {
      const method2 = u.split("/").pop();
      if (tgFail && tgFail.method === method2) return jsonRes(400, { ok: false, description: tgFail.description });
      return jsonRes(200, { ok: true, result: {} });
    }
    return jsonRes(500, {});
  };
  return { fake, calls, files };
}

const tgCalls = (calls, m) => calls.filter((c) => c.url.startsWith("https://api.telegram.org/") && c.url.endsWith(`/${m}`));
const ghWrites = (calls) => calls.filter((c) => c.method === "PUT");

function req(update, { secret = "whsec", method = "POST" } = {}) {
  return new Request("https://worker.example/", {
    method,
    headers: { "content-type": "application/json", "X-Telegram-Bot-Api-Secret-Token": secret },
    body: method === "POST" ? JSON.stringify(update) : undefined,
  });
}
const cb = (data, chat = 4242) => ({
  update_id: 1,
  callback_query: { id: "q1", data, message: { message_id: 77, chat: { id: chat } }, from: { id: chat } },
});
const msg = (text, chat = 4242) => ({ update_id: 2, message: { message_id: 5, text, chat: { id: chat }, from: { id: chat } } });

test("only POST; wrong or missing secret -> 403 without any API call", async () => {
  const { fake, calls } = makeFake();
  assert.equal((await handle(req({}, { method: "GET" }), ENV, fake)).status, 405);
  assert.equal((await handle(req(msg("/start"), { secret: "nope" }), ENV, fake)).status, 403);
  const noHeader = new Request("https://w.example/", { method: "POST", body: JSON.stringify(msg("/start")) });
  assert.equal((await handle(noHeader, ENV, fake)).status, 403);
  assert.equal(calls.length, 0);
});

test("wrong chat -> silent 200 and no API call at all", async () => {
  const { fake, calls } = makeFake();
  assert.equal((await handle(req(msg("/start", 999)), ENV, fake)).status, 200);
  assert.equal((await handle(req(cb(`ta:${KB}:0`, 999)), ENV, fake)).status, 200);
  assert.equal(calls.length, 0);
});

test("garbage body and unrelated update -> 200", async () => {
  const { fake, calls } = makeFake();
  const bad = new Request("https://w.example/", { method: "POST",
    headers: { "X-Telegram-Bot-Api-Secret-Token": "whsec" }, body: "not json" });
  assert.equal((await handle(bad, ENV, fake)).status, 200);
  assert.equal((await handle(req({ update_id: 3, edited_message: {} }), ENV, fake)).status, 200);
  assert.equal(calls.length, 0);
});

test("/start and /menu -> sendMessage with the main menu keyboard", async () => {
  for (const text of ["/start", "/menu", "/start@tiyatro_bot"]) {
    const { fake, calls } = makeFake();
    const res = await handle(req(msg(text)), ENV, fake);
    assert.equal(res.status, 200);
    const sends = tgCalls(calls, "sendMessage");
    assert.equal(sends.length, 1);
    assert.equal(sends[0].url, `https://api.telegram.org/bot${TOKEN}/sendMessage`);
    assert.equal(sends[0].body.chat_id, 4242);
    assert.equal(sends[0].body.parse_mode, "HTML");
    const cds = sends[0].body.reply_markup.inline_keyboard.flat().map((b) => b.callback_data);
    assert.ok(cds.includes("pl:0") && cds.includes("tl") && cds.includes("up") && cds.includes("rf"));
  }
  const { fake, calls } = makeFake();
  await handle(req(msg("merhaba")), ENV, fake);
  assert.equal(calls.length, 0);
});

test("callback ta: config written once with the new value, message edited, callback answered", async () => {
  const { fake, calls, files } = makeFake();
  const res = await handle(req(cb(`ta:${KB}:0`)), ENV, fake);
  assert.equal(res.status, 200);
  const writes = ghWrites(calls);
  assert.equal(writes.length, 1);
  assert.match(writes[0].url, /contents\/config\.json$/);
  assert.equal(writes[0].body.branch, "data");
  assert.equal(writes[0].body.sha, "c1");
  assert.equal(files["config.json"].data.plays["kusursuz-bir-aile"].active, false);
  assert.equal(files["config.json"].data.plays["kucuk-prens"].active, false);
  assert.equal(writes[0].headers.Authorization, `Bearer ${GH_TOKEN}`);
  assert.equal(writes[0].headers.Accept, "application/vnd.github+json");
  assert.equal(writes[0].headers["X-GitHub-Api-Version"], "2022-11-28");
  assert.ok(writes[0].headers["User-Agent"]);
  const edits = tgCalls(calls, "editMessageText");
  assert.equal(edits.length, 1);
  assert.equal(edits[0].body.message_id, 77);
  assert.equal(edits[0].body.chat_id, 4242);
  assert.ok(edits[0].body.text.includes("Kusursuz"));
  assert.ok(edits[0].body.reply_markup.inline_keyboard.flat().some((b) => b.callback_data === `ta:${KB}:1`));
  assert.equal(tgCalls(calls, "answerCallbackQuery").length, 1);
  assert.equal(tgCalls(calls, "answerCallbackQuery")[0].body.callback_query_id, "q1");
});

test("written JSON keeps the Python writer's format (sorted keys, indent 1, Turkish unescaped)", async () => {
  const { fake, calls } = makeFake();
  await handle(req(cb(`tg:${KB}:c`)), ENV, fake);
  const raw = ghWrites(calls)[0].raw;
  assert.ok(raw.includes("Çocuk"));
  assert.ok(!raw.includes("\\u"));
  assert.ok(raw.startsWith('{\n "paused": false,\n "plays": {'));
  assert.ok(!raw.endsWith("\n"));
});

test("a no-op (already applied) route does not write again", async () => {
  const { fake, calls } = makeFake();
  await handle(req(cb(`ta:${KB}:1`)), ENV, fake); // already active
  assert.equal(ghWrites(calls).length, 0);
  assert.equal(tgCalls(calls, "editMessageText").length, 1);
});

test("non-mutating callback renders without writing", async () => {
  const { fake, calls } = makeFake();
  await handle(req(cb(`p:${KB}`)), ENV, fake);
  assert.equal(ghWrites(calls).length, 0);
  assert.equal(tgCalls(calls, "editMessageText").length, 1);
});

test("write conflict -> re-read and re-apply keeps the concurrent unrelated change", async () => {
  const { fake, calls, files } = makeFake({
    conflicts: 1,
    concurrent: (cfg) => { cfg.paused = true; cfg.plays["kusursuz-bir-aile"].targets[0].people = 3; },
  });
  const res = await handle(req(cb(`ta:${KB}:0`)), ENV, fake);
  assert.equal(res.status, 200);
  const final = files["config.json"].data;
  assert.equal(final.plays["kusursuz-bir-aile"].active, false); // our change
  assert.equal(final.paused, true); // their change survived
  assert.equal(final.plays["kusursuz-bir-aile"].targets[0].people, 3);
  assert.equal(ghWrites(calls).length, 2);
  assert.equal(calls.filter((c) => c.method === "GET" && /config\.json/.test(c.url)).length, 2);
  assert.equal(tgCalls(calls, "editMessageText").length, 1);
});

test("persistent conflicts give up after 4 tries, still 200, error toast", async () => {
  const { fake, calls } = makeFake({ conflicts: 99 });
  const res = await handle(req(cb(`ta:${KB}:0`)), ENV, fake);
  assert.equal(res.status, 200);
  assert.equal(ghWrites(calls).length, 4);
  const ans = tgCalls(calls, "answerCallbackQuery");
  assert.equal(ans.length, 1);
  assert.match(ans[0].body.text, /hata/i);
});

test("internal error -> still 200, token never logged, error toast", async () => {
  const { fake, calls } = makeFake({ ghFail: true });
  const logs = [];
  const orig = { log: console.log, error: console.error, warn: console.warn };
  console.log = console.error = console.warn = (...a) => logs.push(a.map(String).join(" "));
  let res;
  try {
    res = await handle(req(cb(`ta:${KB}:0`)), ENV, fake);
  } finally {
    Object.assign(console, orig);
  }
  assert.equal(res.status, 200);
  const all = logs.join("\n") + JSON.stringify(calls.filter((c) => c.url.includes("telegram")).map((c) => c.body));
  assert.ok(!all.includes(TOKEN));
  assert.ok(!all.includes(GH_TOKEN));
  assert.ok(logs.length >= 1);
  const ans = tgCalls(calls, "answerCallbackQuery");
  assert.equal(ans.length, 1);
  assert.match(ans[0].body.text, /hata/i);
});

test("Telegram failing never leaks the bot token and still returns 200", async () => {
  const { fake } = makeFake({ tgFail: { method: "sendMessage", description: "Bad Request: chat not found" } });
  const logs = [];
  const orig = { log: console.log, error: console.error, warn: console.warn };
  console.log = console.error = console.warn = (...a) => logs.push(a.map(String).join(" "));
  let res;
  try {
    res = await handle(req(msg("/start")), ENV, fake);
  } finally {
    Object.assign(console, orig);
  }
  assert.equal(res.status, 200);
  assert.ok(!logs.join("\n").includes(TOKEN));
});

test("'message is not modified' 400 counts as success", async () => {
  const { fake, calls } = makeFake({ tgFail: { method: "editMessageText",
    description: "Bad Request: message is not modified: specified new message content..." } });
  const logs = [];
  const orig = { log: console.log, error: console.error, warn: console.warn };
  console.log = console.error = console.warn = (...a) => logs.push(a.map(String).join(" "));
  try {
    const res = await handle(req(cb(`p:${KB}`)), ENV, fake);
    assert.equal(res.status, 200);
  } finally {
    Object.assign(console, orig);
  }
  const ans = tgCalls(calls, "answerCallbackQuery");
  assert.equal(ans.length, 1);
  assert.ok(!/hata/i.test(ans[0].body.text || ""));
  assert.equal(logs.length, 0);
});

test("rf -> workflow dispatch on main, answered 'Tarama tetiklendi'", async () => {
  const { fake, calls } = makeFake();
  const res = await handle(req(cb("rf")), ENV, fake);
  assert.equal(res.status, 200);
  const d = calls.filter((c) => c.url.includes("/dispatches"));
  assert.equal(d.length, 1);
  assert.equal(d[0].method, "POST");
  assert.equal(d[0].url, "https://api.github.com/repos/yigitar/Tiyatro-Takip/actions/workflows/scan.yml/dispatches");
  assert.deepEqual(d[0].body, { ref: "main" });
  assert.equal(ghWrites(calls).length, 0);
  assert.equal(tgCalls(calls, "answerCallbackQuery")[0].body.text, "Tarama tetiklendi");
});

test("pz sets paused and re-renders the main menu", async () => {
  const { fake, calls, files } = makeFake();
  await handle(req(cb("pz:1")), ENV, fake);
  assert.equal(files["config.json"].data.paused, true);
  const edit = tgCalls(calls, "editMessageText")[0];
  assert.ok(edit.body.reply_markup.inline_keyboard.flat().some((b) => b.callback_data === "pz:0"));
});

test("missing config.json is created from defaults; missing state.json tolerated", async () => {
  const { fake, calls, files } = makeFake();
  delete files["config.json"];
  delete files["state.json"];
  const res = await handle(req(cb("pz:1")), ENV, fake);
  assert.equal(res.status, 200);
  const put = ghWrites(calls)[0];
  assert.equal(put.body.sha, undefined);
  assert.equal(put.written.paused, true);
  assert.equal(put.written.version, 1);
  assert.ok(put.written.sources.length >= 1);
});

test("sender must also be the owner: foreign from.id (callback or text) is ignored silently", async () => {
  const { fake, calls } = makeFake();
  const c = cb(`ta:${KB}:0`);
  c.callback_query.from.id = 999; // in the owner's chat but sent by someone else
  assert.equal((await handle(req(c), ENV, fake)).status, 200);
  const m = msg("/start");
  m.message.from.id = 999;
  assert.equal((await handle(req(m), ENV, fake)).status, 200);
  const noFrom = msg("/start");
  delete noFrom.message.from;
  assert.equal((await handle(req(noFrom), ENV, fake)).status, 200);
  assert.equal(calls.length, 0);
});

test("callback without message still answers the callback (spinner stops), no GitHub traffic", async () => {
  const { fake, calls } = makeFake();
  const res = await handle(req({ update_id: 9, callback_query: { id: "q9", data: "m", from: { id: 4242 } } }), ENV, fake);
  assert.equal(res.status, 200);
  assert.equal(tgCalls(calls, "answerCallbackQuery").length, 1);
  assert.equal(tgCalls(calls, "answerCallbackQuery")[0].body.callback_query_id, "q9");
  assert.equal(calls.filter((c) => c.url.includes("github")).length, 0);
});

test("webhook secret: empty/unset secret rejects everything; length-differing secrets rejected", async () => {
  const { fake, calls } = makeFake();
  assert.equal((await handle(req(msg("/start"), { secret: "" }), { ...ENV, WEBHOOK_SECRET: "" }, fake)).status, 403);
  assert.equal((await handle(req(msg("/start"), { secret: "whsec" }), { ...ENV, WEBHOOK_SECRET: undefined }, fake)).status, 403);
  assert.equal((await handle(req(msg("/start"), { secret: "whsecX" }), ENV, fake)).status, 403);
  assert.equal((await handle(req(msg("/start"), { secret: "whse" }), ENV, fake)).status, 403);
  assert.equal(calls.length, 0);
});

test("files over 1 MB (empty content, encoding none) are read through the blob API", async () => {
  const cfg = makeConfig();
  const calls = [];
  const fake = async (url, init = {}) => {
    calls.push(String(url));
    if (/contents\/config\.json/.test(url)) return jsonRes(200, { content: "", encoding: "none", sha: "bigsha" });
    if (/git\/blobs\/bigsha$/.test(url)) return jsonRes(200, { content: b64(cfg), encoding: "base64" });
    return jsonRes(404, {});
  };
  const { createGithub } = await import("../src/github.js");
  const gh = createGithub({ token: "t", repo: "o/r", fetch: fake });
  const got = await gh.getJson("config.json");
  assert.equal(got.sha, "bigsha");
  assert.deepEqual(got.data, cfg);
  assert.equal(calls.length, 2);
});

test("scheduled: exactly one workflow dispatch; failure never throws and logs only the error type", async () => {
  const { fake, calls } = makeFake();
  const orig = globalThis.fetch;
  const logs = [];
  const o = { error: console.error };
  console.error = (...a) => logs.push(a.map(String).join(" "));
  try {
    globalThis.fetch = fake;
    // outside the dead-man warning bucket (UTC hour % 3 === 0, minute < 5): dispatch only
    await worker.scheduled({ scheduledTime: Date.parse("2030-03-01T01:30:00Z") }, ENV, {});
    assert.equal(calls.filter((c) => c.url.endsWith("/actions/workflows/scan.yml/dispatches")).length, 1);
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].body, { ref: "main" });
    const failing = makeFake({ ghFail: true });
    globalThis.fetch = failing.fake;
    await assert.doesNotReject(worker.scheduled({ scheduledTime: Date.parse("2030-03-01T01:30:00Z") }, ENV, {}));
    assert.ok(logs.length >= 1);
    assert.ok(!logs.join("\n").includes(GH_TOKEN));
  } finally {
    globalThis.fetch = orig;
    console.error = o.error;
  }
});

// ---- final review I7: dead-man's switch for the scheduler -----------------------------

const IN_BUCKET = Date.parse("2030-03-01T03:02:00Z"); // hour % 3 === 0, minute < 5
const OUT_BUCKET = Date.parse("2030-03-01T04:02:00Z");

function stateWithLastScan(msAgo, now) {
  const st = makeState();
  st.meta.last_scan = new Date(now - msAgo).toISOString();
  return st;
}

async function scheduledAt(when, opts) {
  const f = makeFake(opts);
  const logs = [];
  const o = console.error;
  console.error = (...a) => logs.push(a.map(String).join(" "));
  try {
    await worker.scheduled({ scheduledTime: when }, { ...ENV }, {}, f.fake);
  } finally {
    console.error = o;
  }
  return { ...f, logs, warnings: tgCalls(f.calls, "sendMessage") };
}

test("scheduled: stale last_scan -> one warning inside the 3-hour bucket only", async () => {
  const r = await scheduledAt(IN_BUCKET, { state: stateWithLastScan(35 * 60000, IN_BUCKET) });
  assert.equal(r.warnings.length, 1);
  assert.match(r.warnings[0].body.text, /Tarama gecikiyor\/başlatılamadı/);
  assert.equal(String(r.warnings[0].body.chat_id), ENV.TELEGRAM_CHAT_ID);
  const out = await scheduledAt(OUT_BUCKET, { state: stateWithLastScan(35 * 60000, OUT_BUCKET) });
  assert.equal(out.warnings.length, 0);
});

test("scheduled: last_scan 25 min old (a long burst run in progress) -> no warning", async () => {
  const r = await scheduledAt(IN_BUCKET, { state: stateWithLastScan(25 * 60000, IN_BUCKET) });
  assert.equal(r.warnings.length, 0);
});

test("scheduled: fresh last_scan -> no warning", async () => {
  const r = await scheduledAt(IN_BUCKET, { state: stateWithLastScan(6 * 60000, IN_BUCKET) });
  assert.equal(r.warnings.length, 0);
  assert.equal(r.calls.filter((c) => c.url.includes("/dispatches")).length, 1);
});

test("scheduled: dispatch failure -> warning in the bucket; never throws, no secrets logged", async () => {
  const r = await scheduledAt(IN_BUCKET, { ghFail: true });
  assert.equal(r.warnings.length, 1);
  assert.match(r.warnings[0].body.text, /başlatılamadı/);
  assert.ok(!r.logs.join("\n").includes(GH_TOKEN) && !r.logs.join("\n").includes(TOKEN));
  const out = await scheduledAt(OUT_BUCKET, { ghFail: true });
  assert.equal(out.warnings.length, 0);
  const tgDown = await scheduledAt(IN_BUCKET, { ghFail: true, tgFail: { method: "sendMessage", description: "x" } });
  assert.equal(tgDown.warnings.length, 1); // attempted; the failure is only logged
});
