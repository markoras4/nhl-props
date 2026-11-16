// Stripe → evenout: keeps public.billing and profiles.plan in step with each subscription.
// Stripe calls this (no user token), so it's deployed with verify_jwt = false and trusts only events whose
// signature matches STRIPE_WEBHOOK_SECRET.
// Events: checkout.session.completed, customer.subscription.created / updated / deleted.
import type Stripe from "npm:stripe@17.7.0";
import { cryptoProvider, db, LIVE, stripe } from "../_shared/stripe.ts";

const iso = (s?: number | null) => (s ? new Date(s * 1000).toISOString() : null);

async function sync(sub: Stripe.Subscription, hintUser?: string | null) {
  const sb = db();
  const customer = typeof sub.customer === "string" ? sub.customer : sub.customer.id;
  let uid = hintUser || sub.metadata?.user_id || null;
  if (!uid) {
    const { data } = await sb.from("billing").select("user_id").eq("stripe_customer_id", customer).maybeSingle();
    uid = data?.user_id ?? null;
  }
  if (!uid) { console.warn("no user for", customer, sub.id); return; }

  const item = sub.items?.data?.[0];
  const end = (item as unknown as { current_period_end?: number })?.current_period_end ?? (sub as unknown as { current_period_end?: number }).current_period_end;
  const { data: cur } = await sb.from("billing").select("subscription_id,status").eq("user_id", uid).maybeSingle();
  // a newer live subscription wins over an old ended one
  if (cur?.subscription_id && cur.subscription_id !== sub.id && LIVE.includes(cur.status) && !LIVE.includes(sub.status)) return;

  await sb.from("billing").upsert({
    user_id: uid, stripe_customer_id: customer, subscription_id: sub.id, status: sub.status,
    price_key: item?.price?.lookup_key ?? sub.metadata?.price_key ?? null,
    current_period_end: iso(end), cancel_at_period_end: !!sub.cancel_at_period_end, updated_at: new Date().toISOString(),
  });

  // Pro while the subscription lives; creator / media plans are set by hand and never touched here
  const { data: prof } = await sb.from("profiles").select("plan").eq("id", uid).maybeSingle();
  const plan = prof?.plan ?? "free";
  if (plan === "creator" || plan === "media") return;
  const want = LIVE.includes(sub.status) ? "pro" : "free";
  if (want !== plan) await sb.from("profiles").update({ plan: want }).eq("id", uid);
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("ok");
  const secret = Deno.env.get("STRIPE_WEBHOOK_SECRET");
  const sig = req.headers.get("stripe-signature");
  if (!secret || !sig) return new Response("not configured", { status: 400 });
  const body = await req.text();
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(body, sig, secret, undefined, cryptoProvider);
  } catch (e) {
    console.warn("bad signature", (e as Error).message);
    return new Response("bad signature", { status: 400 });
  }
  try {
    if (event.type === "checkout.session.completed") {
      const s = event.data.object as Stripe.Checkout.Session;
      if (s.mode === "subscription" && s.subscription) {
        const sub = await stripe.subscriptions.retrieve(typeof s.subscription === "string" ? s.subscription : s.subscription.id);
        await sync(sub, s.client_reference_id);
      }
    } else if (event.type.startsWith("customer.subscription.")) {
      // re-read so out-of-order events always store the latest state
      const obj = event.data.object as Stripe.Subscription;
      const sub = event.type === "customer.subscription.deleted" ? obj : await stripe.subscriptions.retrieve(obj.id);
      await sync(sub);
    }
  } catch (e) {
    console.error("webhook", event.type, e);
    return new Response("error", { status: 500 });   // Stripe retries
  }
  return Response.json({ received: true });
});
