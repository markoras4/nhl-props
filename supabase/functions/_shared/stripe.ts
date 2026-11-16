// Shared bits for the stripe-checkout, stripe-portal and stripe-webhook functions.
// Secrets (set in Supabase → Edge Functions → Secrets, never in the repo): STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET.
import Stripe from "npm:stripe@17.7.0";
import { createClient } from "jsr:@supabase/supabase-js@2";

export const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY") || "sk_not_set", {   // placeholder until the secret is added; the functions answer 503 then
  apiVersion: "2024-12-18.acacia",
  httpClient: Stripe.createFetchHttpClient(),
});
export const cryptoProvider = Stripe.createSubtleCryptoProvider();

export const db = () =>
  createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });

export const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
export const json = (b: unknown, status = 200) => Response.json(b, { status, headers: CORS });

// where Stripe sends people back: the site they came from, if it's one of ours
const SITES = ["https://evenout.app", "https://www.evenout.app", "https://nhl-props-lab.vercel.app", "http://localhost:8787"];
export const siteOf = (req: Request) => {
  const o = req.headers.get("origin") || "";
  return SITES.includes(o) ? o : "https://evenout.app";
};

// founder / Black Friday price until the end of 30 November 2026 (Swedish time); same date as the site shows
export const FOUNDER_END = Date.parse("2026-12-01T00:00:00+01:00");
export const priceKey = (interval: "month" | "year") =>
  (Date.now() < FOUNDER_END ? "founder_" : "pro_") + (interval === "year" ? "season" : "monthly");

// the signed-in user from the request's Supabase token
export async function userOf(req: Request) {
  const jwt = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!jwt) return null;
  const { data, error } = await db().auth.getUser(jwt);
  return error ? null : data?.user ?? null;
}

// a subscription that still gives Pro (past_due: Stripe is retrying the card, keep access meanwhile)
export const LIVE = ["active", "trialing", "past_due"];
