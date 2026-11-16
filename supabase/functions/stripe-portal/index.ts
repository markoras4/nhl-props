// Opens Stripe's customer portal (cancel, change card, invoices) for the signed-in user. Output: { url } or { error }.
// Deployed with verify_jwt = false: the user's token is checked here (userOf).
import { CORS, db, json, siteOf, stripe, userOf } from "../_shared/stripe.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (!Deno.env.get("STRIPE_SECRET_KEY")) return json({ error: "payments not set up" }, 503);
  const user = await userOf(req);
  if (!user) return json({ error: "sign in" }, 401);
  const { data: bill } = await db().from("billing").select("stripe_customer_id").eq("user_id", user.id).maybeSingle();
  if (!bill?.stripe_customer_id) return json({ error: "no subscription" }, 404);
  try {
    const s = await stripe.billingPortal.sessions.create({ customer: bill.stripe_customer_id, return_url: `${siteOf(req)}/app/` });
    return json({ url: s.url });
  } catch (e) {
    console.error("portal", e);
    return json({ error: "stripe" }, 502);
  }
});
