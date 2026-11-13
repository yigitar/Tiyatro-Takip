// Cloudflare Worker: Telegram webhook for the Tiyatro Takip menu.
import { createGithub, defaultConfig } from "./github.js";
import { isMutating, render, viewOf } from "./menu.js";

const OK = () => new Response("ok", { status: 200 });

/** Constant-time string comparison (no early exit on the first differing byte). */
function safeEqual(a, b) {
  const x = new TextEncoder().encode(String(a ?? ""));
  const y = new TextEncoder().encode(String(b ?? ""));
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

class TelegramError extends Error {
  constructor(method, status) {
    super(`telegram ${method} failed (HTTP ${status})`);
    this.name = "TelegramError";
  }
}

// The bot token is part of the URL, so errors never carry the URL or the original message.
function createTelegram(token, fetchImpl) {
  return async function tg(method, payload) {
    let res;
    try {
      res = await fetchImpl(`https://api.telegram.org/bot${token}/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch {
      throw new TelegramError(method, 0);
    }
    if (res.ok) return true;
    let description = "";
    try {
      description = String((await res.json()).description || "");
    } catch { /* body not JSON */ }
    if (res.status === 400 && /message is not modified/i.test(description)) return true;
    throw new TelegramError(method, res.status);
  };
}

async function loadState(gh) {
  const s = await gh.getJson("state.json");
  return s ? s.data : { plays: {}, meta: {}, emitted: {} };
}

async function loadConfig(gh) {
  const c = await gh.getJson("config.json");
  return c ? c.data : defaultConfig();
}

function screenPayload(chatId, screen) {
  return {
    chat_id: chatId,
    text: screen.text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
    reply_markup: { inline_keyboard: screen.keyboard },
  };
}

async function onCallback(cq, env, tg, gh) {
  const route = String(cq.data ?? "");
  const chatId = cq.message.chat.id;
  const messageId = cq.message.message_id;
  let toast;
  let screen;
  if (route === "rf") {
    await gh.dispatchWorkflow("scan.yml", "main");
    toast = "Tarama tetiklendi";
  } else if (isMutating(route)) {
    const state = await loadState(gh);
    const config = await gh.updateConfig(route, state);
    screen = render(viewOf(route), config, state);
  } else {
    const [config, state] = await Promise.all([loadConfig(gh), loadState(gh)]);
    screen = render(route, config, state);
  }
  if (screen) {
    await tg("editMessageText", { ...screenPayload(chatId, screen), message_id: messageId });
  }
  await tg("answerCallbackQuery", { callback_query_id: cq.id, ...(toast ? { text: toast } : {}) });
}

async function onMessage(message, tg, gh) {
  const text = String(message.text ?? "").trim();
  if (!/^\/(start|menu)(@\w+)?(\s|$)/.test(text)) return;
  const [config, state] = await Promise.all([loadConfig(gh), loadState(gh)]);
  await tg("sendMessage", screenPayload(message.chat.id, render("m", config, state)));
}

/** Testable handler with an injectable fetch. */
export async function handle(request, env, fetchImpl = globalThis.fetch) {
  if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
  const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
  if (!env.WEBHOOK_SECRET || !safeEqual(secret, env.WEBHOOK_SECRET)) return new Response("forbidden", { status: 403 });

  let update;
  try {
    update = await request.json();
  } catch {
    return OK();
  }

  const cq = update?.callback_query;
  const message = update?.message;
  // Private chat with the owner only: sender and chat must both be the configured id.
  const owner = Number(env.TELEGRAM_CHAT_ID);
  const authorized = (u) => Number.isFinite(owner) && u?.from?.id === owner
    && (u.chat?.id ?? u.message?.chat?.id ?? owner) === owner;
  if (!(cq ? authorized(cq) : message ? authorized(message) : false)) return OK();

  const tg = createTelegram(env.TELEGRAM_BOT_TOKEN, fetchImpl);
  const gh = createGithub({ token: env.TIYATRO_GH_TOKEN, repo: env.GITHUB_REPO, fetch: fetchImpl });
  try {
    if (cq && cq.message) await onCallback(cq, env, tg, gh);
    else if (cq) await tg("answerCallbackQuery", { callback_query_id: cq.id }); // stop the spinner
    else if (message) await onMessage(message, tg, gh);
  } catch (e) {
    console.error(`bot error: ${e?.name ?? "Error"}`);
    if (cq) {
      try {
        await tg("answerCallbackQuery", { callback_query_id: cq.id, text: "Hata, tekrar dene" });
      } catch { /* nothing more to do */ }
    }
  }
  return OK();
}

/** Cron trigger: kick the scan workflow. Never throws; logs only the error type. */
export async function runScheduled(env, fetchImpl = globalThis.fetch) {
  try {
    const gh = createGithub({ token: env.TIYATRO_GH_TOKEN, repo: env.GITHUB_REPO, fetch: fetchImpl });
    await gh.dispatchWorkflow("scan.yml", "main");
  } catch (e) {
    console.error(`scheduled error: ${e?.name ?? "Error"}`);
  }
}

export default {
  async fetch(request, env) {
    return handle(request, env, globalThis.fetch);
  },
  async scheduled(event, env, ctx) {
    await runScheduled(env, globalThis.fetch);
  },
};
