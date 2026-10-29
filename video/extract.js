// Runs inside the built site page (app/index.html) so the video uses exactly the site's own numbers:
// its "Best picks" (bestPicks / pickReasons), model probabilities (prob), book odds and no-vig prices,
// public betting splits and futures. Returns plain JSON.
(day) => {
  const r2 = x => x == null ? null : Math.round(x * 10000) / 10000;
  const out = { date: day, games: [], futures: null };
  for (const g of DATA.games.filter(x => x.date === day)) {
    let bp = { picks: [] };
    try { bp = bestPicks(g); } catch (e) { }
    const picks = bp.picks.map(c => {
      let rs = { why: [], warn: [] };
      try { rs = pickReasons(c, g); } catch (e) { }
      const b = book(g.id, c.pr.id, c.m);
      const p = c.p;
      const form = (p.form?.games || []).slice(0, 10).reverse().map(x => ({ d: x.d, o: x.o, h: x.h, g: x.g, a: x.a, s: x.s }));
      const lu = DATA.teams[c.pr.team]?.lineup?.groups || {};
      const line = Object.entries(lu).find(([k, ids]) => /^[fd]\d$/.test(k) && ids.includes(c.pr.id))?.[0] || null;
      return {
        id: c.pr.id, name: p.name, team: c.pr.team, opp: c.pr.opp, home: !!c.pr.home, num: p.num, pos: p.pos, age: p.age,
        m: c.m, market: mName(c.m), v: r2(c.v), od: c.od, ev: r2(c.ev), nv: r2(noVig(b)), has_odds: !!bp.hasOdds,
        why: rs.why || [], warn: rs.warn || [],
        toi: p.toi_proj, line, pp1: (lu.pp1 || []).includes(c.pr.id),
        season: p.windows?.cur || null, last5: { gp: p.form?.gp, g: p.form?.g, a: p.form?.a, s: p.form?.s },
        streak: p.streaks || null, career: p.career || null, flag: p.regress?.flag || null, form,
      };
    });
    const rows = g.props.filter(pr => P(pr.id)?.active).map(pr => {
      const p = P(pr.id);
      const cell = m => ({ v: r2(prob(pr, g, m)), od: book(g.id, pr.id, m)?.o ?? null });
      return { id: pr.id, name: p.name, team: pr.team, g: cell("g"), pt: cell("pt"), s: cell("s2.5") };
    }).sort((a, b) => b.pt.v - a.pt.v);
    const top = rows.slice(0, 6);
    if (picks[0] && !top.some(x => x.id === picks[0].id)) { top.pop(); top.push(rows.find(x => x.id === picks[0].id)); }
    const o = DATA.odds?.[g.id] || {};
    const nv = (a, b) => a && b ? (1 / a) / ((1 / a) + (1 / b)) : null;
    out.games.push({
      id: g.id, home: g.home, away: g.away, home_name: g.home_name, away_name: g.away_name, start: g.start,
      p_home: r2(g.default.p_home), mk_home: r2(nv(o.winner?.home, o.winner?.away)),
      exp_total: g.default.exp_total, over: r2(g.default["over_5.5"]), mk_over: r2(nv(o.totals?.["5.5"]?.over, o.totals?.["5.5"]?.under)),
      public: o.public || null, stakes: DATA.futures?.games?.[g.id] || null,
      picks, table: top,
    });
  }
  const T = DATA.futures?.teams || {};
  const ranked = k => Object.entries(T).map(([t, x]) => [t, x[k], x.name]).filter(x => x[1] != null).sort((a, b) => b[1] - a[1]);
  out.futures = { cup: ranked("cup").slice(0, 3), playoffs: ranked("playoffs").slice(0, 3), cup_odds: DATA.futures?.odds?.cup || null };
  return out;
}
