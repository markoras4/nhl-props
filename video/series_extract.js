// Runs inside the built app page (after the Pro data is merged in) and returns plain JSON for video/series.py:
// the day's graded value picks (the site's own Triage: triageRows / triage) and the hottest and coldest players by
// Heat Index (p.streaks.heat). Same numbers as the site, nothing recomputed here.
(args) => {
  const day = args.day && dates.includes(args.day) ? args.day : dates[0];
  const games = DATA.games.filter(g => g.ld === day && (!g.state || ["FUT", "PRE"].includes(g.state)));
  const meta = (pr, g) => `${pr.team} ${pr.home ? "vs" : "@"} ${pr.opp}`;
  const r2 = x => x == null ? null : Math.round(x * 1000) / 1000;
  const sideName = (m, side) => /^s\d/.test(m) ? `${side === "n" ? "Under" : "Over"} ${m.slice(1)} shots` : side === "n" ? `No ${mName(m)}` : mName(m);

  // ---- board: graded value picks ----
  let rows = [];
  try { rows = triageRows(day); } catch (e) { rows = []; }
  const pick = r => {
    const p = r.p, why = [];
    const vs = Object.entries(r.views || {});
    if (r.gr === "S") why.push("Season and last-10 numbers also show value at this price");
    else if (vs.length && vs.some(([, e]) => e > 0) && vs.some(([, e]) => e <= 0)) why.push("Only one of the two form checks backs the edge");
    if (p.mates?.pp === "pp1" && r.gr !== "F") why.push("On the first power-play unit");
    const toi = p.toi_proj; if (toi && r.gr !== "F" && why.length < 2) why.push(`${toi.toFixed(1)} minutes projected tonight`);
    let cautions = []; try { cautions = triCautions(r); } catch (e) {}
    for (const c of cautions) if (why.length < 3) why.push(c);
    let info = []; try { info = triInfo(r).filter((x, i) => r.info[i] !== "sharp_ok"); } catch (e) {}
    for (const c of info) if (why.length < 3) why.push(c);
    if (r.gr === "F" && !cautions.length) why.push("Only the model sees value: both form checks say no");
    return {id: p.id || r.pr.id, name: p.name, team: r.pr.team, opp: r.pr.opp, home: !!r.pr.home, num: p.num || "", pos: p.pos,
            game: r.g.id, start: r.g.start, meta: meta(r.pr, r.g), market: sideName(r.m, r.side), grade: r.gr,
            model: r2(r.pm), market_p: r2(1 / r.od), od: r.od, edge: r2(r.ev), why: why.slice(0, 3)};
  };
  rows.sort((a, b) => ({S: 0, L: 1, F: 2}[a.gr] - {S: 0, L: 1, F: 2}[b.gr]) || b.ev - a.ev);
  const seenP = new Set(), perGame = {}, board = [];
  const nGames = games.length, want = Math.max(1, Math.min(4, Math.round(nGames / 3)));
  for (const r of rows) {
    if ((r.gr === "F" && !args.testAllowF) || r.od < 1.4 || r.od > 6) continue;   // Solid and Lean at sensible prices (testAllowF: preview renders only)
    const pid = r.pr.id; if (seenP.has(pid) || (perGame[r.g.id] || 0) >= 1) continue;
    seenP.add(pid); perGame[r.g.id] = (perGame[r.g.id] || 0) + 1; board.push(pick(r));
    if (board.length >= want) break;
  }
  // one "looks like value, but Fragile" teaching moment when the slate has one with a big model edge
  const trap = rows.filter(r => r.gr === "F" && r.ev >= 0.08 && r.od >= 1.5 && r.od <= 6 && !seenP.has(r.pr.id)).sort((a, b) => b.ev - a.ev)[0];

  // ---- hot or lucky: biggest Heat Index swings among regulars playing on this slate (the video is for these games) ----
  const playing = new Map();
  for (const g of games) for (const pr of g.props || []) if (!playing.has(String(pr.id))) playing.set(String(pr.id), {g, pr});
  const heat = [];
  for (const [pid, {g, pr}] of playing) {
    const p = P(pid); const h = p?.streaks?.heat; if (!p?.active || !h || h.v == null || h.n < 8) continue;
    if ((p.toi_proj || 0) < 14) continue;
    const games10 = (p.form?.games || []).slice(0, 10).reverse().map(x => ({p: (x.g || 0) + (x.a || 0), o: String(x.o || "").replace(/^@/, "")}));
    let ch = null; try { ch = prob(pr, g, "pt"); } catch (e) {}
    heat.push({id: pid, name: p.name, team: pr.team, opp: pr.opp, home: !!pr.home, num: p.num || "", pos: p.pos, v: h.v, x: h.x, e: h.e, n: h.n,
               games: games10, sh_cur: p.windows?.cur?.sh ?? null, sh_true: p.rates?.recent_sh ?? null, gp: p.windows?.cur?.gp ?? null,
               next: {game: g.id, date: g.ld, meta: `${pr.team} ${pr.home ? "vs" : "@"} ${pr.opp}`, pt: r2(ch)}});
  }
  heat.sort((a, b) => b.v - a.v);
  return {day, n_games: nGames, board, trap: trap ? pick(trap) : null, hot: heat.slice(0, 3), cold: heat.slice(-3).reverse(),
          site: location.href};
}
