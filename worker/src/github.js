// GitHub Contents API client (data branch) with an injectable fetch.
import { apply } from "./menu.js";

const API = "https://api.github.com";
const DATA_BRANCH = "data";
const DISPATCH_REF = "main";
export const MAX_TRIES = 4;

export const DEFAULT_SOURCE =
  "https://biletinial.com/tr-tr/mekan/kocaeli-buyuksehir-belediyesi-sehir-tiyatrolari";

export function defaultConfig() {
  return { plays: {}, sources: [DEFAULT_SOURCE], paused: false, version: 1 };
}

export class GithubError extends Error {
  constructor(what, status) {
    super(`github ${what} failed (HTTP ${status})`);
    this.name = "GithubError";
    this.status = status;
  }
}

function toBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function fromBase64(b64) {
  const bin = atob(b64.replace(/\s/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

/** Python json.dumps(sort_keys=True, indent=1, ensure_ascii=False) equivalent (no trailing newline). */
export function stableStringify(data) {
  const sort = (v) => {
    if (Array.isArray(v)) return v.map(sort);
    if (v && typeof v === "object") {
      return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sort(v[k])]));
    }
    return v;
  };
  return JSON.stringify(sort(data), null, 1);
}

export function createGithub({ token, repo, fetch: fetchImpl = globalThis.fetch }) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "tiyatro-takip-bot",
  };
  const contents = (path) => `${API}/repos/${repo}/contents/${path}`;

  // Network errors are re-thrown without the original message (it could echo headers).
  async function call(what, url, init) {
    try {
      return await fetchImpl(url, { ...init, headers: { ...headers, ...(init.headers || {}) } });
    } catch {
      throw new GithubError(what, 0);
    }
  }

  async function getJson(path) {
    const res = await call("GET", `${contents(path)}?ref=${DATA_BRANCH}`, { method: "GET" });
    if (res.status === 404) return null;
    if (!res.ok) throw new GithubError("GET", res.status);
    const body = await res.json();
    return { data: JSON.parse(fromBase64(body.content || "")), sha: body.sha };
  }

  async function putJson(path, data, sha, message) {
    const payload = { message, content: toBase64(stableStringify(data)), branch: DATA_BRANCH };
    if (sha) payload.sha = sha;
    const res = await call("PUT", contents(path), {
      method: "PUT", body: JSON.stringify(payload), headers: { "Content-Type": "application/json" },
    });
    if (res.status === 409 || res.status === 422) return false;
    if (!res.ok) throw new GithubError("PUT", res.status);
    return true;
  }

  async function dispatchWorkflow(file, ref = DISPATCH_REF) {
    const res = await call("dispatch", `${API}/repos/${repo}/actions/workflows/${file}/dispatches`, {
      method: "POST", body: JSON.stringify({ ref }), headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) throw new GithubError("dispatch", res.status);
    return true;
  }

  /**
   * Read config.json, apply the route, write with the sha; on a write conflict re-read and
   * re-apply (max MAX_TRIES) so a concurrent writer's changes survive. A route that changes
   * nothing is not written. Returns the config that is now stored.
   */
  async function updateConfig(route, state) {
    for (let i = 0; i < MAX_TRIES; i++) {
      const cur = await getJson("config.json");
      const config = cur ? cur.data : defaultConfig();
      const next = apply(route, config, state);
      if (cur && stableStringify(next) === stableStringify(config)) return config;
      if (await putJson("config.json", next, cur?.sha, "data: update config.json (telegram)")) return next;
    }
    throw new GithubError("config update (conflicts)", 409);
  }

  return { getJson, putJson, dispatchWorkflow, updateConfig };
}
