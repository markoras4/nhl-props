// Sends push alerts to Pro users (Pro, Creator, Media): props whose edge passes the user's level, confirmed
// starting goalies, and game-day alerts for players on the user's watchlist.
// Paywall change: the alerts feed is no longer public. The daily workflow uploads it to the private "pro"
// storage bucket (alerts.json), and only accounts on a Pro plan get alerts. Takes no input; each alert is sent once.
import { createClient } from "jsr:@supabase/supabase-js@2";
import webpush from "npm:web-push@3.6.7";

const PUBLIC_KEY = "BK0o9JRdP9_HQync1vRC5g2lX8c35DxwVh_rkolSKTkRjcFHSMaLXNMQwEgeMNzEAEZsfNkzujHTMoegiNPI1oM";
const PRO_PLANS = ["pro", "creator", "media"];
const MKT: Record<string, string> = { g: "Goal", a: "1+ assist", pt: "1+ point" };
const mname = (m: string) => MKT[m] || `${Math.ceil(parseFloat(m.slice(1)))}+ shots`;

Deno.serve(async () => {
  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
  const { data: priv } = await db.rpc("get_vapid_private");
  if (!priv) return new Response("no key", { status: 500 });
  webpush.setVapidDetails("https://nhl-props-lab.vercel.app", PUBLIC_KEY, priv as string);

  let feed: any;
  try {
    const { data, error } = await db.storage.from("pro").download("alerts.json");
    if (error || !data) throw new Error(error?.message || "missing");
    feed = JSON.parse(await data.text());
  } catch (e) { return Response.json({ error: "feed: " + e }); }

  const { data: all } = await db.from("alert_settings").select("user_id, edge_min, goalies, watch");
  if (!all?.length) return Response.json({ sent: 0, users: 0 });
  const { data: pros } = await db.from("profiles").select("id").in("id", all.map((s) => s.user_id)).in("plan", PRO_PLANS);
  const proIds = new Set((pros || []).map((p) => p.id));
  const settings = all.filter((s) => proIds.has(s.user_id));
  if (!settings.length) return Response.json({ sent: 0, users: 0 });
  const users = settings.map((s) => s.user_id);
  const { data: subs } = await db.from("push_subscriptions").select("id, user_id, endpoint, p256dh, auth").in("user_id", users);
  const { data: sentRows } = await db.from("alerts_sent").select("user_id, key").in("user_id", users).gte("sent_at", new Date(Date.now() - 3 * 86400e3).toISOString());
  const sent = new Set((sentRows || []).map((r) => r.user_id + "|" + r.key));
  const watchUsers = settings.filter((s) => s.watch !== false).map((s) => s.user_id);
  const { data: wl } = watchUsers.length && (feed.plays || []).length
    ? await db.from("watchlist").select("user_id, player_id").in("user_id", watchUsers)
    : { data: [] as { user_id: string; player_id: number }[] };
  const playsBy = new Map<number, any>();
  for (const p of feed.plays || []) if (!playsBy.has(p.pid)) playsBy.set(p.pid, p);
  const now = Date.now();
  let total = 0;

  for (const s of settings) {
    const mySubs = (subs || []).filter((x) => x.user_id === s.user_id);
    if (!mySubs.length) continue;
    const msgs: { key: string; title: string; body: string; url: string }[] = [];
    if (s.watch !== false) {
      for (const w of (wl || []).filter((x) => x.user_id === s.user_id)) {
        const p = playsBy.get(w.player_id);
        if (!p || new Date(p.start).getTime() <= now) continue;
        const key = `watch:${p.gid}:${p.pid}`;
        if (sent.has(s.user_id + "|" + key)) continue;
        msgs.push({ key, title: `${p.name} plays today`,
          body: `${p.team} ${p.home ? "vs" : "@"} ${p.opp} · goal chance ${Math.round(p.p * 100)}%` + (p.odds ? ` · odds ${Number(p.odds).toFixed(2)}` : ""), url: `/app/#g${p.gid}` });
      }
    }
    if (s.edge_min != null) {
      for (const e of feed.edges || []) {
        if (e.ev < s.edge_min || new Date(e.start).getTime() <= now) continue;
        const key = `edge:${e.gid}:${e.pid}:${e.m}`;
        if (sent.has(s.user_id + "|" + key)) continue;
        msgs.push({ key, title: `${e.name}: ${mname(e.m)} at ${Number(e.odds).toFixed(2)}`,
          body: `Model ${Math.round(e.p * 100)}% · edge +${Math.round(e.ev * 100)}% · ${e.team} ${e.home ? "vs" : "@"} ${e.opp}`, url: `/app/#g${e.gid}` });
      }
    }
    if (s.goalies) {
      for (const gk of feed.goalies || []) {
        if (new Date(gk.start).getTime() <= now) continue;
        const key = `goalie:${gk.gid}:${gk.team}:${gk.name}`;
        if (sent.has(s.user_id + "|" + key)) continue;
        msgs.push({ key, title: `${gk.team} goalie confirmed: ${gk.name}`, body: `${gk.team} vs ${gk.opp}. Props for this game now use the confirmed starter.`, url: `/app/#g${gk.gid}` });
      }
    }
    if (!msgs.length) continue;
    // at most 4 single alerts per run; anything more becomes one summary
    const out = msgs.length > 4 ? [...msgs.slice(0, 3), { key: "", title: `${msgs.length - 3} more alerts`, body: "Open the app to see them all.", url: "/app/" }] : msgs;
    for (const sub of mySubs) {
      for (const m of out) {
        try {
          await webpush.sendNotification({ endpoint: sub.endpoint, keys: { p256dh: sub.p256dh, auth: sub.auth } },
            JSON.stringify({ title: m.title, body: m.body, url: m.url, tag: m.key || "summary" }), { TTL: 3600 });
          total++;
        } catch (err: any) {
          if (err?.statusCode === 404 || err?.statusCode === 410) { await db.from("push_subscriptions").delete().eq("id", sub.id); break; }
        }
      }
    }
    const rows = msgs.map((m) => ({ user_id: s.user_id, key: m.key }));
    await db.from("alerts_sent").upsert(rows, { onConflict: "user_id,key", ignoreDuplicates: true });
  }
  return Response.json({ sent: total, users: settings.length });
});
