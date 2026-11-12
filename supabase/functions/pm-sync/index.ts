// Polymarket line movement for NHL games into public.pm_lines (read by Pro accounts on each game page).
// Run every 30 minutes by pg_cron (job "pm-sync"). Uses Polymarket's public read-only APIs, no key:
//   gamma-api.polymarket.com/events   the NHL games (series 10346) with their markets
//   clob.polymarket.com/prices-history the price of one outcome over time
// For each game starting between 4 hours ago and 60 hours ahead it stores three series: the away team's moneyline
// chance, the main total's over chance (the line nearest 50%) and the favourite's -1.5 puck line, each as
// [[unix_minute, chance*1000], ...] from when the market opened (hourly, then every 10 minutes for the last day).
// Anyone may call it, so it does nothing when the stored data is less than 20 minutes old.
// Deployed with verify_jwt = false.
import { createClient } from "jsr:@supabase/supabase-js@2";

const GAMMA = "https://gamma-api.polymarket.com";
const CLOB = "https://clob.polymarket.com";
const UA = { "User-Agent": "Mozilla/5.0 (compatible; nhl-props-lab/1.0)", "Accept": "application/json" };
// Polymarket team codes -> NHL codes (the rest are the same in upper case)
const CODE: Record<string, string> = { cal: "CGY", mon: "MTL", nj: "NJD", sj: "SJS", tb: "TBL", las: "VGK", utah: "UTA", la: "LAK" };
const code = (c: string) => CODE[c] || c.toUpperCase();

async function get(url: string) {
  for (let i = 0; i < 2; i++) {
    try {
      const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(20000) });
      if (r.ok) return await r.json();
    } catch (_) { /* retry once */ }
  }
  return null;
}

type Pt = [number, number];
async function history(token: string): Promise<Pt[]> {
  const [all, day] = await Promise.all([
    get(`${CLOB}/prices-history?market=${token}&interval=max&fidelity=60`),
    get(`${CLOB}/prices-history?market=${token}&interval=1d&fidelity=10`),
  ]);
  const pts = (h: any) => (h?.history || []).map((p: any) => [Math.round(p.t / 60), Math.round(p.p * 1000)] as Pt);
  const recent = pts(day), cut = recent.length ? recent[0][0] : Infinity;
  return [...pts(all).filter((p: Pt) => p[0] < cut), ...recent];
}

const parse = (s: unknown) => { try { return typeof s === "string" ? JSON.parse(s) : (s ?? []); } catch (_) { return []; } };

async function game(e: any) {
  const m = /^nhl-([a-z]+)-([a-z]+)-\d{4}-\d\d-\d\d$/.exec(e.slug || "");
  if (!m) return null;
  const start = e.startTime || e.markets?.[0]?.gameStartTime || e.endDate;
  if (!start) return null;
  const markets: any[] = e.markets || [];
  const ml = markets.find(x => x.sportsMarketType === "moneyline");
  if (!ml) return null;
  const mlTok = parse(ml.clobTokenIds)[0], mlNow = +parse(ml.outcomePrices)[0];
  const awayName = parse(ml.outcomes)[0];
  // main total: the line whose over is nearest 50%
  const tots = markets.filter(x => x.sportsMarketType === "totals" && parse(x.outcomePrices).length)
    .map(x => ({ x, line: +((/O\/U\s*([\d.]+)/.exec(x.question || "") || [])[1]), p: +parse(x.outcomePrices)[0] }))
    .filter(o => o.line > 0 && o.p > 0 && o.p < 1).sort((a, b) => Math.abs(a.p - 0.5) - Math.abs(b.p - 0.5));
  // puck line: the moneyline favourite at -1.5
  const favAway = mlNow >= 0.5;
  const pl = markets.find(x => x.sportsMarketType === "spreads" && /\(-1\.5\)/.test(x.question || "")
    && (parse(x.outcomes)[0] === awayName) === favAway);
  const [mlS, totS, plS] = await Promise.all([
    history(mlTok),
    tots[0] ? history(parse(tots[0].x.clobTokenIds)[0]) : Promise.resolve([]),
    pl ? history(parse(pl.clobTokenIds)[0]) : Promise.resolve([]),
  ]);
  const away = code(m[1]), home = code(m[2]);
  return {
    slug: e.slug, start_time: new Date(start).toISOString(), away, home,
    ml: mlS.length ? { s: mlS, vol: Math.round(+ml.volume || 0) } : null,
    tot: totS.length ? { line: tots[0].line, s: totS } : null,
    pl: plS.length ? { team: favAway ? away : home, line: -1.5, s: plS } : null,
    volume: Math.round(+e.volume || 0), fetched_at: new Date().toISOString(),
  };
}

Deno.serve(async () => {
  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
  const { data: last } = await db.from("pm_lines").select("fetched_at").order("fetched_at", { ascending: false }).limit(1).maybeSingle();
  if (last && Date.now() - new Date(last.fetched_at).getTime() < 20 * 60e3) return Response.json({ skipped: "fresh" });
  const events = (await get(`${GAMMA}/events?series_id=10346&closed=false&limit=100`)) || [];
  const lo = Date.now() - 4 * 3600e3, hi = Date.now() + 60 * 3600e3;
  const todo = events.filter((e: any) => { const t = Date.parse(e.startTime || e.endDate || ""); return t >= lo && t <= hi; });
  const rows: any[] = [];
  for (let i = 0; i < todo.length; i += 6) {   // a few games at a time, to stay polite to the API
    for (const r of await Promise.all(todo.slice(i, i + 6).map(game))) if (r && r.ml) rows.push(r);
  }
  if (rows.length) {
    const { error } = await db.from("pm_lines").upsert(rows, { onConflict: "slug" });
    if (error) return Response.json({ error: error.message }, { status: 500 });
  }
  await db.from("pm_lines").delete().lt("start_time", new Date(Date.now() - 3 * 86400e3).toISOString());
  return Response.json({ events: events.length, stored: rows.length });
});
