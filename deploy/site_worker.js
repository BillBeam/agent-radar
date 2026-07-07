// Agent Radar — Cloudflare Pages advanced-mode worker (_worker.js), shipped with every
// site deploy by radar/channels/_site.py. Adds two SAME-ORIGIN endpoints on top of the
// static assets (same domain as the reading pages → no CORS, no separate reachability):
//
//   POST /vote   {date, item_id, vote: up|down, seg}   ← the reading page's 👍/👎 buttons
//   GET  /votes?since=<ms>   Authorization: Bearer HMAC(secret,"vote-read")[:32]
//                                                      ← the Mac `radar --mode serve` poller
//
// Auth model: the page's own unguessable path segment doubles as the capability token —
// the worker recomputes HMAC-SHA256(WEB_SECRET, date)[:32] and rejects mismatches, so a
// guessed/leaked item id alone can't stuff votes. Reads require the separate derived
// bearer (never embedded in any page). WEB_SECRET is a Pages project secret; votes live
// in the VOTES KV binding. Without either binding the endpoints degrade to 503 and the
// static site keeps working untouched.

const JSONH = { "content-type": "application/json; charset=utf-8" };

async function hmacSeg(secret, msg) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

function bad(status, msg) {
  return new Response(JSON.stringify({ ok: false, error: msg }), { status, headers: JSONH });
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
  const auth = request.headers.get("authorization") || "";
  if (auth !== `Bearer ${await hmacSeg(env.WEB_SECRET, "vote-read")}`) return bad(403, "forbidden");
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

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/vote" && request.method === "POST") return handleVote(request, env);
    if (pathname === "/votes" && request.method === "GET") return handleVotes(request, env);
    return env.ASSETS.fetch(request);   // the static site; root has no index → stays 404
  },
};
