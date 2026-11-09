// Hands the Pro data file to signed-in Pro, Creator and Media accounts (see PAYWALL.md, nhlprops/paywall.py).
// Free users and visitors get 403, so Pro numbers never reach their browser.
// Input: { d: "latest" | "YYYY-MM-DD" | "archive/YYYY-MM-DD" | "alerts" }. Output: { url } (signed, 60 s) or { error }.
// Deployed with verify_jwt = false: the function checks the user's token itself (auth.getUser), like the other functions here.
import { createClient } from "jsr:@supabase/supabase-js@2";

const PRO_PLANS = ["pro", "creator", "media"];
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const json = (b: unknown, status = 200) => Response.json(b, { status, headers: CORS });
  const jwt = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!jwt) return json({ error: "sign in" }, 401);

  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
  const { data: u, error: ue } = await db.auth.getUser(jwt);
  if (ue || !u?.user) return json({ error: "sign in" }, 401);
  const { data: prof } = await db.from("profiles").select("plan").eq("id", u.user.id).maybeSingle();
  const plan = prof?.plan || "free";
  if (!PRO_PLANS.includes(plan)) return json({ error: "pro", plan }, 403);

  let d = "latest";
  try { d = String((await req.json())?.d || "latest"); } catch (_) { /* default */ }
  if (!/^(latest|alerts|\d{4}-\d{2}-\d{2}|archive\/\d{4}-\d{2}-\d{2})$/.test(d)) return json({ error: "bad d" }, 400);
  const { data: s, error } = await db.storage.from("pro").createSignedUrl(`${d}.json`, 60);
  if (error || !s?.signedUrl) return json({ error: "not found" }, 404);
  return json({ url: s.signedUrl, plan });
});
