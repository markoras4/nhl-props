// "Tonight's slate breakdown" (long-form YouTube video): runs inside the built app page (Pro data merged in when
// available) and returns plain JSON for video/longform.py: every game of the New York date args.date with the model's
// win chance and expected score (the site's own gameMu / gameDist), team form and style, starting goalies, the players
// worth watching (Heat Index, season line, point chance when the Pro props are there), head-to-head and playoff stakes.
// Stats and model numbers only: nothing from odds, prices, picks or grades is read here.
(args) => {
  const r3 = x => x == null || !isFinite(x) ? null : Math.round(x * 1000) / 1000;
  const r2 = x => x == null || !isFinite(x) ? null : Math.round(x * 100) / 100;
  const day = args.date;
  const games = DATA.games.filter(g => g.date === day && (!g.state || ["FUT", "PRE"].includes(g.state)))
                          .sort((a, b) => (a.start || "").localeCompare(b.start || ""));
  const TR = DATA.team_ranks?.teams || {}, FT = DATA.futures?.teams || {}, FG = DATA.futures?.games || {};
  const nTeams = Object.keys(TR).length || 32;
  // goalies with 5+ games, ranked by goals saved above expected (this season)
  const regs = Object.values(DATA.goalies || {}).filter(q => q?.cur?.gp >= 5).sort((a, b) => (b.cur.gsax || 0) - (a.cur.gsax || 0));
  const gRank = {}; regs.forEach((q, i) => { gRank[String(q.id)] = i + 1; });
  const rk = (t, k) => TR[t]?.[k] ? {v: r3(TR[t][k].v), rk: TR[t][k].rk} : null;
  // league ranks for the team stats board (1 = best), over every team in the page
  const TEAMS = Object.values(DATA.teams || {});
  const RANKED = {pts_pct: 1, gf_pg: 1, ga_pg: -1, xgf_pct5: 1, hdsf60: 1, hdsa60: -1, pp_xgf60_cur: 1, pk_xga60_cur: -1, pdo: 1, fo_pct: 1, pen_pg: -1, sf: 1};
  const lgRank = (t, k) => { const d = DATA.teams?.[t]; if (!d || d[k] == null) return null;
    const dir = RANKED[k]; return 1 + TEAMS.filter(x => x[k] != null && (dir > 0 ? x[k] > d[k] : x[k] < d[k])).length; };

  const team = (t, g) => {
    const d = DATA.teams?.[t] || {}, f = FT[t] || {};
    const sh = g.shots || {}, home = t === g.home;
    const sf = home ? sh.h_sf : sh.a_sf, sa = home ? sh.h_sa : sh.a_sa;
    const miss = (d.missing?.players || []).filter(x => /^Out/i.test(x.why || "") && (x.pts_share || 0) >= 0.05)
                  .sort((a, b) => (b.pts_share || 0) - (a.pts_share || 0)).slice(0, 2).map(x => ({name: x.name, pos: x.pos, why: x.why, share: r3(x.pts_share)}));
    return {abbr: t, name: home ? g.home_name : g.away_name, w: d.w, l: d.l, otl: d.otl, gp: d.gp, pts_pct: r3(d.pts_pct),
            streak: d.streak, streak_kind: d.streak_kind, last5: d.last5 || "", l5_gf: r2(d.l5_gf), l5_ga: r2(d.l5_ga),
            gf_pg: r2(d.gf_pg), ga_pg: r2(d.ga_pg), gf_ha: r2(home ? d.gf_home : d.gf_away), ga_ha: r2(home ? d.ga_home : d.ga_away),
            xgf_pct5: r3(d.xgf_pct5), pdo: r3(d.pdo), sh5: r3(d.sh5), sv5: r3(d.sv5), s_gf: d.s_gf, s_xgf: r2(d.s_xgf), s_ga: d.s_ga, s_xga: r2(d.s_xga),
            sf: r2(sf), sa: r2(sa), pp_gf60: r2(d.pp_gf60), pk_ga60: r2(d.pk_ga60), pen_pg: r2(d.pen_pg), fo_pct: r3(d.fo_pct),
            pp_xgf60: r2(d.pp_xgf60_cur ?? d.xgf_pp), pk_xga60: r2(d.pk_xga60_cur ?? d.xga_pk), hdxgf60: r2(d.hdxgf60), hdxga60: r2(d.hdxga60),
            lr: Object.fromEntries(Object.keys(RANKED).map(k => [k, lgRank(t, k)])),
            lines: Object.fromEntries(Object.entries(d.lines5 || {}).filter(([, L]) => L).map(([k, L]) => [k, {gp: L.gp, toi: r2(L.toi), gf: L.gf, xgf: r2(L.xgf), ga: L.ga, xga: r2(L.xga), sf: L.sf, sa: L.sa, name: L.name || "", exact: !!L.exact}])),
            groups: Object.fromEntries(Object.entries(d.lineup?.groups || {}).map(([k, ids]) => [k, ids.map(i => P(i)?.name).filter(Boolean)])),
            hd_for: r2(d.hdsf60), hd_against: r2(d.hdsa60), tier: d.tier || null,
            rank: {xgf: rk(t, "xgf"), pdo: rk(t, "pdo"), pp: rk(t, "pp"), pk: rk(t, "pk"), att: rk(t, "att"), def: rk(t, "def"), gsax: rk(t, "gsax"), cf: rk(t, "cf")},
            n_teams: nTeams, playoffs: r3(f.playoffs), cup: r3(f.cup), div: f.div_name || null, conf: f.conf || null,
            pts_now: f.pts_now ?? null, b2b: !!g.b2b?.[t], recent: (d.recent6 || []).slice(0, 5), out: miss};
  };

  const goalie = (g, t) => {
    let id = null; try { id = state.goalie?.[g.id]?.[t]; } catch (e) {}
    id = id || g.starters?.[t];
    const q = id && (DATA.goalies?.[id] || DATA.goalies?.[+id]);
    if (!q) return null;
    const opp = t === g.home ? g.away : g.home, vs = q.vs?.[opp];
    return {id: String(q.id), name: q.name, num: q.num || "", status: g.starter_status?.[t] || null,
            gp: q.cur?.gp ?? 0, sv: r3(q.cur?.sv), gaa: r2(q.cur?.gaa), gsax: r2(q.cur?.gsax), rk: gRank[String(q.id)] || null, of: regs.length,
            l3: q.last3 ? {gp: q.last3.gp, sv: r3(q.last3.sv), gsax: r2(q.last3.gsax)} : null,
            vs: vs && vs.gp ? {gp: vs.gp, sv: r3(vs.sv), gaa: r2(vs.gaa)} : null};
  };

  const lineOf = (p, t) => {
    const k = p.mates?.line; if (!k) return null;
    const L = DATA.teams?.[t]?.lines5?.[k];
    const ids = DATA.teams?.[t]?.lineup?.groups?.[k] || [];
    const names = ids.map(i => P(i)?.name).filter(Boolean);
    return {k, names, pp: p.mates?.pp || null,
            l5: L && L.gp >= 3 && L.toi >= 40 ? {gp: L.gp, toi: r2(L.toi), gf: L.gf, xgf: r2(L.xgf), ga: L.ga, xga: r2(L.xga), name: L.name || ""} : null};
  };

  const players = (g, t) => {
    const ids = new Set(), out = [];
    const prs = (g.props || []).filter(pr => pr.team === t);
    for (const pr of prs) ids.add(String(pr.id));
    if (!ids.size) for (const p of Object.values(DATA.players || {})) if (p.team === t && p.active) ids.add(String(p.id));
    for (const pid of ids) {
      const p = P(pid); if (!p || !p.active || p.injury) continue;
      const pr = prs.find(x => String(x.id) === pid);
      let pt = null, g1 = null;
      if (pr && pr.base && Object.keys(pr.base).length && !pr.locked) {
        try { pt = prob(pr, g, "pt"); g1 = prob(pr, g, "g"); } catch (e) { pt = g1 = null; }
      }
      const h = p.streaks?.heat, c = p.windows?.cur || {};
      out.push({id: pid, name: p.name, num: p.num || "", pos: p.pos, posg: p.posg, toi: r2(p.toi_proj), gp: c.gp || 0, g: c.g || 0, a: c.a || 0, p: c.p || 0, s: c.s || 0,
                heat: h && h.v != null ? {v: r2(h.v), x: h.x, e: r2(h.e), n: h.n} : null,
                pt: r3(pt), gl: r3(g1), xg: p.xg_windows?.cur ? {g: p.xg_windows.cur.g, xg: r2(p.xg_windows.cur.xg)} : null,
                sh: r3(c.sh), sh_true: r3(p.rates?.recent_sh), sh_career: r3(p.rates?.career_sh), line: lineOf(p, t),
                toi_season: r2(p.toi_season), toi_recent: r2(p.toi_recent), pp_toi: r2(p.x?.pp?.min_pg), ppp: p.x?.pp?.ppp ?? null,
                fo: p.x?.fo?.pct_cur != null && (p.x.fo.pg || 0) >= 5 ? r3(p.x.fo.pct_cur) : null,
                reg: p.regress ? {g: p.regress.g, xg: r2(p.regress.xg), exp: r2(p.regress.exp), s: p.regress.s} : null,
                g10: (p.form?.games || []).slice(0, 10).map(x => ({o: String(x.o || "").replace(/^@/, ""), p: (x.g || 0) + (x.a || 0), g: x.g || 0, s: x.s || 0, t: r2(x.t)})),
                age: p.age ?? null, career: p.career || null,
                last: (p.form?.games || []).slice(0, 5).map(x => (x.g || 0) + (x.a || 0))});
    }
    return out;
  };

  const out = [];
  for (const g of games) {
    let ph = g.default?.p_home ?? null, mh = null, ma = null;
    try { const mu = gameMu(g); mh = mu.h; ma = mu.a; const d = gameDist(mu.h, mu.a); ph = d.ph; } catch (e) {}
    if (mh == null && g.mu) { mh = g.mu.home.g + g.mu.home.o; ma = g.mu.away.g + g.mu.away.o; }
    const h2 = g.h2h?.games ? g.h2h.games.map(x => { const [hg, ag] = String(x.score).split("-").map(Number); return {date: x.date, hg, ag, hHome: !!x.home}; }) : [];
    const S = FG[String(g.id)] || FG[g.id] || null;
    const stakes = S ? Object.fromEntries(Object.entries(S).map(([t, v]) => [t, {win: r3(v.win), lose: r3(v.lose), now: r3(v.now), swing: r3(v.swing), level: v.level || null}])) : null;
    out.push({id: String(g.id), start: g.start, home: g.home, away: g.away, venue: g.venue || "",
              p_home: r3(ph), mu_h: r2(mh), mu_a: r2(ma), exp_total: r2(g.default?.exp_total ?? (mh + ma)), p_tie_reg: r3(g.default?.p_tie_reg),
              teams: {[g.home]: team(g.home, g), [g.away]: team(g.away, g)},
              goalies: {[g.home]: goalie(g, g.home), [g.away]: goalie(g, g.away)},
              players: {[g.home]: players(g, g.home), [g.away]: players(g, g.away)},
              h2h: h2, stakes});
  }
  return {date: day, built: DATA.built || null, pro: (DATA.games || []).some(g => g.date === day && (g.props || []).some(pr => pr.base && Object.keys(pr.base).length && !pr.locked)),
          n_games: out.length, games: out};
}
