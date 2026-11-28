// Starts a Stripe Checkout for evenout Pro. Input: { interval: "month" | "year" }. Output: { url } or { error }.
// The price is picked here (founder price until 30 Nov 2026), never trusted from the browser.
// Deployed with verify_jwt = false: the user's token is checked here (userOf), like the other functions.
import { CORS, db, json, LIVE, priceKey, siteOf, stripe, userOf } from "../_shared/stripe.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (!Deno.env.get("STRIPE_SECRET_KEY")) return json({ error: "payments not set up" }, 503);
  const user = await userOf(req);
  if (!user) return json({ error: "sign in" }, 401);

  let interval: "month" | "year" = "month";
  try { interval = (await req.json())?.interval === "year" ? "year" : "month"; } catch (_) { /* default */ }

  const sb = db();
  const { data: prof } = await sb.from("profiles").select("plan, promo_until").eq("id", user.id).maybeSingle();
  if (["creator", "media"].includes(prof?.plan)) return json({ error: "already pro" }, 409);
  // free Black Week Pro still running: nobody is charged for a period that is free (they can subscribe after it ends)
  if (prof?.promo_until && Date.parse(prof.promo_until) > Date.now()) return json({ error: "already pro", promo_until: prof.promo_until }, 409);
  const { data: bill } = await sb.from("billing").select("*").eq("user_id", user.id).maybeSingle();
  if (bill?.subscription_id && LIVE.includes(bill.status)) return json({ error: "already pro" }, 409);

  try {
    // one Stripe customer per account, created on the first checkout; a stored customer that this Stripe key
    // can't see (deleted, or made with the other mode's key: test vs live) is replaced with a new one
    let customer = bill?.stripe_customer_id as string | undefined;
    if (customer) {
      try { const c = await stripe.customers.retrieve(customer); if ((c as { deleted?: boolean }).deleted) customer = undefined; }
      catch (e) { if ((e as { code?: string }).code === "resource_missing") customer = undefined; else throw e; }
    }
    if (!customer) {
      const c = await stripe.customers.create({ email: user.email, metadata: { user_id: user.id } });
      customer = c.id;
      await sb.from("billing").upsert({ user_id: user.id, stripe_customer_id: customer, updated_at: new Date().toISOString() });
    }
    const key = priceKey(interval);
    const prices = await stripe.prices.list({ lookup_keys: [key], active: true, limit: 1 });
    const price = prices.data[0];
    if (!price) return json({ error: "price missing: " + key }, 500);

    const site = siteOf(req);
    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      customer,
      client_reference_id: user.id,
      line_items: [{ price: price.id, quantity: 1 }],
      subscription_data: { metadata: { user_id: user.id, price_key: key } },
      metadata: { user_id: user.id, price_key: key },
      allow_promotion_codes: true,
      success_url: `${site}/app/?checkout=success`,
      cancel_url: `${site}/app/?checkout=cancel`,
      custom_text: {
        submit: {
          message: "Pro starts right away. You can cancel at any time under Account → Manage subscription and keep Pro until the end of the paid period. By subscribing you accept the evenout terms (evenout.app/legal).",
        },
      },
    });
    return json({ url: session.url });
  } catch (e) {
    console.error("checkout", e);
    return json({ error: "stripe" }, 502);
  }
});
