// Hourly sharp-book prices (Pinnacle NHL player props) into public.pin_odds, read by Pro accounts in the app so the
// sharp price stays fresh between site builds.
// Pinnacle's guest API blocks Supabase's servers, so the GitHub workflow fetches it (python -m nhlprops sharp) and
// POSTs the parsed rows here, signed with GitHub's own OIDC token (only this repo's main branch is accepted; no
// stored key).
//   GET            -> { age_min }  minutes since the last stored price (the workflow skips the fetch when < 50)
//   POST {rows}    -> stores them (upsert per game / player / market) and drops games that started 6+ hours ago
// Deployed with verify_jwt = false (the GitHub token is checked here).
import { createClient } from "jsr:@supabase/supabase-js@2";
import { createRemoteJWKSet, jwtVerify } from "npm:jose@5.9.6";

const REPO = "markoras4/nhl-props";
const AUDIENCE = "nhl-props-pin-sync";
const ISSUER = "https://token.actions.githubusercontent.com";
const JWKS = createRemoteJWKSet(new URL(ISSUER + "/.well-known/jwks"));
const MARKET = /^(g|g2|g3|a|a2|pt|pt2|pt3|s\d+\.\d)$/;

Deno.serve(async (req) => {
  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
  if (req.method === "GET") {
    const { data } = await db.from("pin_odds").select("fetched_at").order("fetched_at", { ascending: false }).limit(1).maybeSingle();
    return Response.json({ age_min: data ? Math.round((Date.now() - new Date(data.fetched_at).getTime()) / 60e3) : null });
  }
  if (req.method !== "POST") return Response.json({ error: "GET or POST" }, { status: 405 });
  const token = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  try {
    const { payload } = await jwtVerify(token, JWKS, { issuer: ISSUER, audience: AUDIENCE });
    if (payload.repository !== REPO || payload.ref !== "refs/heads/main") throw new Error("wrong repository or branch");
  } catch (e) {
    return Response.json({ error: "not allowed: " + (e as Error).message }, { status: 401 });
  }
  let body: any;
  try { body = await req.json(); } catch (_) { return Response.json({ error: "body is not JSON" }, { status: 400 }); }
  const fetched_at = new Date().toISOString();
  const rows = (Array.isArray(body?.rows) ? body.rows : []).filter((r: any) =>
    r && typeof r.player_name === "string" && typeof r.home_name === "string" && typeof r.away_name === "string" &&
    MARKET.test(String(r.market)) && Number(r.over) > 1 && !isNaN(Date.parse(r.start_time))
  ).map((r: any) => ({ start_time: new Date(r.start_time).toISOString(), home_name: r.home_name.slice(0, 80), away_name: r.away_name.slice(0, 80),
    player_name: r.player_name.slice(0, 80), market: String(r.market), over: Number(r.over), under: Number(r.under) > 1 ? Number(r.under) : null, fetched_at }));
  if (rows.length) {
    const { error } = await db.from("pin_odds").upsert(rows, { onConflict: "start_time,home_name,player_name,market" });
    if (error) return Response.json({ error: error.message }, { status: 500 });
  }
  await db.from("pin_odds").delete().lt("start_time", new Date(Date.now() - 6 * 3600e3).toISOString());
  return Response.json({ stored: rows.length, games: new Set(rows.map((r: any) => r.start_time + r.home_name)).size });
});
