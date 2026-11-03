// ==UserScript==
// @name         NHL Props Lab · Betano sync
// @namespace    nhl-props-lab-betano
// @version      1.0
// @description  Reads Betano's (Canada) NHL prices on a schedule and sends them to NHL Props Lab (first and closing prices for the Triage record). Same sportsbook as Stoiximan; for when Stoiximan can't be reached.
// @match        https://www.betano.ca/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
/* Schedule: every hour; every 15 min in the 2 hours before the first puck drop; and once ~12 min before each game
   (the closing price, just before the site's lock at 5 min). Only one Betano tab does the work.
   Click the badge (bottom left) to run a check now. Keep this tab open (pinning it is easiest). */
(function () {
  "use strict";
  const ENDPOINT = "https://evxnevqxykbfocqfqxid.supabase.co/functions/v1/stx-sync";
  const TOKEN = "__STX_TOKEN__";
  const LEAGUE = "/sport/hockey/north-america/nhl/10118/";
  const ME = Math.random().toString(36).slice(2);
  const LS = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
               set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };

  // ---- one worker tab
  const leader = () => { const L = LS.get("nhlsync_leader", null), now = Date.now();
    if (!L || L.id === ME || now - L.ts > 90e3) { LS.set("nhlsync_leader", { id: ME, ts: now }); return true; } return false; };

  // ---- reading Betano pages
  const state = async (u) => { const h = await fetch(u, { credentials: "include" }).then(r => r.text()); const i = h.indexOf('initial_state"]=');
    if (i < 0) return null; let s = h.slice(i + 16); s = s.slice(0, s.indexOf("</script>")).trim().replace(/;$/, ""); return JSON.parse(s); };
  const markets = (st) => { const mk = []; const walk = (o, d) => { if (!o || typeof o !== "object" || d > 14) return;
    if (o.name && (Array.isArray(o.selections) || o.tableLayout)) { mk.push(o); return; } for (const k in o) walk(o[k], d + 1); }; walk(st && st.data, 0); return mk; };
  const clean = s => String(s || "").replace(/\s*\(.*$/, "").trim();

  async function events() {
    const st = await state(LEAGUE); if (!st) throw new Error("league page not readable");
    const out = new Map(); const walk = (o, d) => { if (!o || typeof o !== "object" || d > 14) return;
      if (typeof o.url === "string" && o.url.startsWith("/game-odds/") && o.startTime) out.set(o.url, +o.startTime); for (const k in o) walk(o[k], d + 1); };
    walk(st, 0); return [...out].map(([url, start]) => ({ url, start }));
  }

  async function readGame(ev) {
    const r = await fetch(ev.url, { credentials: "include" }); const base = new URL(r.url).pathname;
    const st0 = await state(base), st4 = await state(base + "?bt=4"), stP = await state(base + "?bt=1");
    const ms = [...markets(st0), ...markets(st4)].filter(m => !m.tableLayout);
    const mk = {}, tot = {}; const find = n => ms.filter(m => m.name === n);
    const win = find("Winner")[0]; const away = win ? win.selections[0].name : null, home = win ? win.selections[win.selections.length - 1].name : null;
    const res = find("End of Regulation Result")[0];
    if (res) for (const x of res.selections) { if (x.name === home) mk["1"] = x.price; else if (x.name === "Tie") mk.X = x.price; else if (x.name === away) mk["2"] = x.price; }
    for (const hc of find("Spread")) for (const x of hc.selections) { const mm = String(x.name).match(/^(.*) ([+-][\d.]+)$/); if (!mm) continue; const t = mm[1], h = mm[2];
      if (t === home && h === "-1.5") mk.hpl = x.price; if (t !== home && h === "+1.5") mk.hpl_n = x.price; if (t !== home && h === "-1.5") mk.apl = x.price; if (t === home && h === "+1.5") mk.apl_n = x.price; }
    const g6 = find("Goals").sort((a, b) => b.selections.length - a.selections.length)[0];
    if (g6) for (const x of g6.selections) { const [ou, L] = String(x.name).split(" "); if (!L) continue; tot[L] = tot[L] || {}; tot[L][ou === "Over" ? "over" : "under"] = x.price; }
    for (const m of ms) { const mt = m.name.match(/^(.+) Goals$/); if (mt && (mt[1] === home || mt[1] === away)) { const key = mt[1] === home ? "htt" : "att"; for (const x of m.selections) if (String(x.name).startsWith("Over")) mk[key + x.name.split(" ")[1]] = x.price; } }
    const p1 = find("Period 1 - Result")[0]; if (p1) { const d = p1.selections.find(x => x.name === "Tie"); if (d) mk.p1x = d.price; }
    const p1g = find("Period 1 - Goals")[0]; if (p1g) for (const x of p1g.selections) if (String(x.name).startsWith("Over")) mk["p1o" + x.name.split(" ")[1]] = x.price;
    const P = {}; const set = (n, k, v) => { if (v == null) return; (P[n] = P[n] || {})[k] = v; };
    for (const m of markets(stP)) { if (!m.tableLayout) continue; const t = m.name.replace(/^\[[^\]]*\]\s*/, "");
      for (const row of m.tableLayout.rows || []) { const n = clean(row.title);
        for (const gs of row.groupSelections || []) { const c = {}; for (const x of gs.selections || []) c[x.columnIndex] = x.price;
          const ov = (gs.selections || []).find(x => /^Over/.test(x.name)), un = (gs.selections || []).find(x => /^Under/.test(x.name)), L = gs.line;
          const ou = ov ? ov.price + "/" + (un ? un.price : "") : null;
          if (t === "To Score a Goal") { set(n, "g", c[0]); set(n, "fg", c[1]); }
          else if (t === "To Score 2+ Goals") set(n, "g2", c[0]);
          else if (t === "To score 3+ Goals") set(n, "g3", c[1]);
          else if (t === "Points") { const k = L === "0.5" ? "pt" : L === "1.5" ? "pt2" : L === "2.5" ? "pt3" : null; if (k && ou) set(n, k, ou); }
          else if (t === "Assists") { const k = L === "0.5" ? "a" : L === "1.5" ? "a2" : null; if (k && ou) set(n, k, ou); }
          else if (t === "Shots on Goal" && ou) set(n, "s" + L, ou);
          else if (t === "Total Blocked Shots" && ou) set(n, "b" + L, ou);
          else if (/o Score a Powerplay Point$/.test(t)) set(n, "ppp", c[0]);
          // "To score 2+ Points / Assists" tables are skipped: their prices don't match their labels
        } } }
    return { key: base, start: ev.start, home, markets: mk, totals: tot, players: P };
  }

  // ---- season awards: the awards event linked from the league page's awards tab
  async function awards() { return null;   // award prices come from Stoiximan
  }
  // ---- one check
  let busy = false;
  async function run(kind) {
    if (busy) return; busy = true; badge("checking…", "run");
    try {
      const now = Date.now(); const evs = (await events()).filter(e => e.start > now - 10 * 60e3 && e.start < now + 30 * 3600e3);
      const games = []; for (const e of evs) { try { games.push(await readGame(e)); } catch (err) { console.warn("[nhl-sync]", e.url, err); } }
      let aw = null; const awLast = LS.get("nhlsync_awts", 0);
      if (kind === "manual" || Date.now() - awLast > 3 * 3600e3) { try { aw = await awards(); if (aw) LS.set("nhlsync_awts", Date.now()); } catch (err) { console.warn("[nhl-sync] awards", err); } }
      if (!games.length && !aw) { LS.set("nhlsync_last", { ts: Date.now(), ok: true, kind, games: 0, prices: 0, note: "no games listed" }); badge(); return; }
      const r = await fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json", "x-stx-token": TOKEN }, body: JSON.stringify({ kind, games, awards: aw }) });
      const js = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(js.error || ("HTTP " + r.status));
      LS.set("nhlsync_last", { ts: Date.now(), ok: true, kind, games: js.games, prices: js.prices, awards: js.awards || LS.get("nhlsync_last", {}).awards || 0 });
    } catch (err) { LS.set("nhlsync_last", { ts: Date.now(), ok: false, kind, err: String(err && err.message || err) }); console.warn("[nhl-sync]", err); }
    finally { busy = false; badge(); }
  }

  // ---- schedule
  async function tick() {
    if (!leader()) { badge("another Betano tab is syncing", "idle"); return; }
    const last = LS.get("nhlsync_last", null), sinceLast = last ? Date.now() - last.ts : 1e12;
    let evs = LS.get("nhlsync_events", null);
    if (!evs || Date.now() - evs.ts > 30 * 60e3) { try { evs = { ts: Date.now(), list: await events() }; LS.set("nhlsync_events", evs); } catch (e) { evs = evs || { ts: 0, list: [] }; } }
    const now = Date.now(), up = evs.list.filter(e => e.start > now).sort((a, b) => a.start - b.start);
    const closed = LS.get("nhlsync_closed", {});
    const closing = up.find(e => e.start - now <= 13 * 60e3 && e.start - now >= 7 * 60e3 && !closed[e.url + e.start]);
    if (closing) { closed[closing.url + closing.start] = 1; LS.set("nhlsync_closed", closed); return run("close"); }
    const first = up[0], near = first && first.start - now <= 2 * 3600e3;
    if (near && sinceLast >= 15 * 60e3) return run("pre");
    if (sinceLast >= 60 * 60e3) return run("hourly");
    badge();
  }

  // ---- status badge
  let el;
  function badge(msg, mode) {
    if (!el) { el = document.createElement("div"); el.title = "NHL Props Lab sync: click to check now";
      el.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:2147483647;font:600 12px/1.3 system-ui,sans-serif;padding:7px 11px;border-radius:10px;background:#111821;color:#e7eef6;box-shadow:0 6px 18px rgba(0,0,0,.35);cursor:pointer;max-width:320px";
      el.onclick = () => run("manual"); document.body.appendChild(el); }
    const L = LS.get("nhlsync_last", null), t = d => new Date(d).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    let dot = "#8796a8", txt = msg || "waiting for the first check";
    if (!msg && L) { dot = L.ok ? "#3ddc97" : "#ff6b6b"; txt = L.ok ? `last check ${t(L.ts)} · ${L.games} games · ${L.prices} prices${L.awards ? ` · ${L.awards} award prices` : ""}` : `last check ${t(L.ts)} failed: ${L.err}`; }
    if (mode === "run") dot = "#ffb547";
    el.innerHTML = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dot};margin-right:7px"></span>NHL Props sync (Betano) · ${txt}`;
  }

  badge(); tick(); setInterval(tick, 60e3);
})();
