// Agent Radar — Cloudflare Pages advanced-mode worker (_worker.js), shipped with every
// site deploy by radar/channels/_site.py. Adds SAME-ORIGIN endpoints on top of the static
// assets (same domain as the reading pages → no CORS, no separate reachability):
//
//   POST /vote   {date, item_id, vote: up|down, seg}   ← the reading page's 👍/👎 buttons
//   GET  /votes?since=<ms>   Authorization: Bearer HMAC(secret,"vote-read")[:32]
//                                                      ← the Mac `radar --mode serve` poller
//   POST /trigger        {seg}                         ← the home page's ⟳ 立即抓取 button
//   GET  /trigger?seg=…  | Authorization: Bearer HMAC(secret,"trigger-read")[:32]
//                          seg → state only (the page); bearer → state + pending req (the Mac)
//   POST /trigger/state  Authorization: Bearer …       ← the Mac reports queued→running→done
//
// Auth model: the page's own unguessable path segment doubles as the capability token —
// the worker recomputes HMAC-SHA256(WEB_SECRET, key)[:32] and rejects mismatches, so a
// guessed/leaked item id alone can't stuff votes or spend opus quota. `date` is the key for
// day pages, the literal "home" for the trigger (the home seg is exactly the bookmark URL).
// Reads by the Mac require a separate derived bearer (never embedded in any page). WEB_SECRET
// is a Pages project secret; state lives in the VOTES KV binding. Without either binding the
// endpoints degrade to 503 and the static site keeps working untouched.
//
// KV is eventually consistent (~60s worst case, measured 82s). Both trigger directions are
// built to tolerate that: the Mac poll just starts the run up to a minute late, and the page
// reconciles by timestamp (a state older than its own request still reads as 已排队).

const JSONH = { "content-type": "application/json; charset=utf-8" };
const NOSTORE = { ...JSONH, "cache-control": "no-store" };

// A manual run costs a full opus deepread pass — don't let a double-tap or a shared URL
// spend it twice. The Mac's RunLock is the hard guard; this is the cheap first one.
const TRIGGER_COOLDOWN_MS = 20 * 60 * 1000;
const TRIGGER_STALE_MS = 3 * 60 * 60 * 1000;   // a "running" older than this = the Mac died

const K_REQ = "trig:req";
const K_STATE = "trig:state";

async function hmacSeg(secret, msg) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

function bad(status, msg, extra) {
  return new Response(JSON.stringify({ ok: false, error: msg, ...(extra || {}) }),
    { status, headers: NOSTORE });
}

function ok(body) {
  return new Response(JSON.stringify({ ok: true, ...body }), { headers: NOSTORE });
}

async function isMac(request, env, purpose) {
  const auth = request.headers.get("authorization") || "";
  return auth === `Bearer ${await hmacSeg(env.WEB_SECRET, purpose)}`;
}

async function handleVote(request, env) {
  if (!env.VOTES || !env.WEB_SECRET) return bad(503, "vote backend not configured");
  let b;
  try { b = await request.json(); } catch { return bad(400, "bad json"); }
  const date = String(b?.date || ""), item = String(b?.item_id || "");
  const vote = String(b?.vote || ""), seg = String(b?.seg || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return bad(400, "bad date");
  if (!/^[0-9a-f]{8,32}$/.test(item)) return bad(400, "bad item_id");
  if (vote !== "up" && vote !== "down") return bad(400, "bad vote");
  if (seg !== await hmacSeg(env.WEB_SECRET, date)) return bad(403, "forbidden");
  const ts = Date.now();
  // one key per (date,item): last-write-wins, exactly like `radar mark` / the DingTalk card
  await env.VOTES.put(`v1:${date}:${item}`, JSON.stringify({ vote, ts }), { metadata: { ts } });
  return new Response(JSON.stringify({ ok: true }), { headers: JSONH });
}

async function handleVotes(request, env) {
  if (!env.VOTES || !env.WEB_SECRET) return bad(503, "vote backend not configured");
  if (!await isMac(request, env, "vote-read")) return bad(403, "forbidden");
  const since = Number(new URL(request.url).searchParams.get("since") || 0);
  const out = [];
  let cursor;
  do {
    const page = await env.VOTES.list({ prefix: "v1:", cursor });
    for (const k of page.keys) {
      const ts = Number(k.metadata?.ts || 0);
      if (ts <= since) continue;
      const v = await env.VOTES.get(k.name, "json");
      if (!v) continue;
      const [, date, item_id] = k.name.split(":");
      out.push({ date, item_id, vote: v.vote, ts: Number(v.ts || ts) });
    }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  out.sort((a, b) => a.ts - b.ts);
  return new Response(JSON.stringify({ votes: out }), { headers: JSONH });
}

// ---- manual run trigger ---------------------------------------------------------------

/** The stored state, with a dead "running" (Mac slept/crashed mid-run) aged out to idle. */
async function readState(env) {
  const st = await env.VOTES.get(K_STATE, "json");
  if (!st) return { state: "idle" };
  if ((st.state === "queued" || st.state === "running")
      && Date.now() - Number(st.ts || 0) > TRIGGER_STALE_MS) {
    return { ...st, state: "stale", note: "上一次运行没有回报结果（Mac 可能睡了或被杀）" };
  }
  return st;
}

async function handleTriggerPost(request, env) {
  if (!env.VOTES || !env.WEB_SECRET) return bad(503, "trigger backend not configured");
  let b;
  try { b = await request.json(); } catch { return bad(400, "bad json"); }
  if (String(b?.seg || "") !== await hmacSeg(env.WEB_SECRET, "home")) return bad(403, "forbidden");

  const now = Date.now();
  const st = await readState(env);
  if (st.state === "queued" || st.state === "running") {
    return bad(409, st.state, { state: st });   // already in flight — the page just keeps polling
  }
  const since = now - Number(st.accepted_ts || 0);
  if (st.accepted_ts && since < TRIGGER_COOLDOWN_MS) {
    return bad(429, "cooldown", { retry_after_s: Math.ceil((TRIGGER_COOLDOWN_MS - since) / 1000) });
  }
  const state = { state: "queued", ts: now, accepted_ts: now };
  await env.VOTES.put(K_REQ, JSON.stringify({ ts: now }));
  await env.VOTES.put(K_STATE, JSON.stringify(state));
  return ok({ state });
}

async function handleTriggerGet(request, env) {
  if (!env.VOTES || !env.WEB_SECRET) return bad(503, "trigger backend not configured");
  const state = await readState(env);
  if (await isMac(request, env, "trigger-read")) {   // the Mac poller: state + the pending request
    const req = await env.VOTES.get(K_REQ, "json");
    return ok({ state, req: req || null });
  }
  const seg = new URL(request.url).searchParams.get("seg") || "";
  if (seg !== await hmacSeg(env.WEB_SECRET, "home")) return bad(403, "forbidden");
  return ok({ state });                              // the page: state only, never the request
}

async function handleTriggerState(request, env) {
  if (!env.VOTES || !env.WEB_SECRET) return bad(503, "trigger backend not configured");
  if (!await isMac(request, env, "trigger-read")) return bad(403, "forbidden");
  let b;
  try { b = await request.json(); } catch { return bad(400, "bad json"); }
  const state = String(b?.state || "");
  if (!["queued", "running", "done", "failed", "busy"].includes(state)) return bad(400, "bad state");

  const prev = (await env.VOTES.get(K_STATE, "json")) || {};
  // `busy` = the Mac refused (a run already held the lock) → no quota was spent, so the
  // cooldown must NOT apply; any other terminal state keeps accepted_ts and its 20-min window.
  const accepted = state === "busy" ? 0 : (Number(prev.accepted_ts || 0) || Date.now());
  const next = {
    state,
    ts: Date.now(),
    accepted_ts: accepted,
    ...(b.run_id ? { run_id: String(b.run_id).slice(0, 64) } : {}),
    ...(b.note ? { note: String(b.note).slice(0, 200) } : {}),
  };
  await env.VOTES.put(K_STATE, JSON.stringify(next));
  if (state !== "queued") await env.VOTES.delete(K_REQ);   // claimed (or refused) → never replayed
  return ok({ state: next });
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    const m = request.method;
    if (pathname === "/vote" && m === "POST") return handleVote(request, env);
    if (pathname === "/votes" && m === "GET") return handleVotes(request, env);
    if (pathname === "/trigger" && m === "POST") return handleTriggerPost(request, env);
    if (pathname === "/trigger" && m === "GET") return handleTriggerGet(request, env);
    if (pathname === "/trigger/state" && m === "POST") return handleTriggerState(request, env);
    return env.ASSETS.fetch(request);   // the static site; root has no index → stays 404
  },
};
