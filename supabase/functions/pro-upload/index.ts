// Stores the Pro data files from the daily GitHub build in the private "pro" bucket (see PAYWALL.md).
// No stored secret: the workflow sends GitHub's own short-lived OIDC token, and this function only accepts tokens
// GitHub signed for the markoras4/nhl-props repository on its main branch.
// POST ?path=latest.json | 2026-11-09.json | archive/2026-11-05.json | alerts.json, body = the JSON file.
// Deployed with verify_jwt = false (the GitHub token is checked here, not by Supabase).
import { createClient } from "jsr:@supabase/supabase-js@2";
import { createRemoteJWKSet, jwtVerify } from "npm:jose@5.9.6";

const REPO = "markoras4/nhl-props";
const AUDIENCE = "nhl-props-pro-upload";
const ISSUER = "https://token.actions.githubusercontent.com";
const JWKS = createRemoteJWKSet(new URL(ISSUER + "/.well-known/jwks"));
const PATH_OK = /^(latest|latest-demo|alerts|\d{4}-\d{2}-\d{2}(-demo)?|archive\/\d{4}-\d{2}-\d{2})\.json$/;

Deno.serve(async (req) => {
  if (req.method !== "POST") return Response.json({ error: "POST only" }, { status: 405 });
  const token = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  try {
    const { payload } = await jwtVerify(token, JWKS, { issuer: ISSUER, audience: AUDIENCE });
    if (payload.repository !== REPO || payload.ref !== "refs/heads/main") throw new Error("wrong repository or branch");
  } catch (e) {
    return Response.json({ error: "not allowed: " + (e as Error).message }, { status: 401 });
  }
  const path = new URL(req.url).searchParams.get("path") || "";
  if (!PATH_OK.test(path)) return Response.json({ error: "bad path" }, { status: 400 });
  const body = await req.text();
  try { JSON.parse(body); } catch (_) { return Response.json({ error: "body is not JSON" }, { status: 400 }); }

  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
  const { error } = await db.storage.from("pro").upload(path, new Blob([body], { type: "application/json" }), { upsert: true, contentType: "application/json" });
  if (error) return Response.json({ error: error.message }, { status: 500 });
  return Response.json({ ok: true, path, bytes: body.length });
});
