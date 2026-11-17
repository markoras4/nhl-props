// "Last night" series: runs inside the built app page and returns the site's own numbers AFTER last night's games
// (the morning build already includes them): season lines, Heat Index, goalie GSAx, playoff chances, team ranks.
// Stats only: nothing from odds, picks or grades is read here. args.pids = every player/goalie who played last night.
(args) => {
  const r3 = x => x == null ? null : Math.round(x * 1000) / 1000;
  const players = {}, goalies = {};
  for (const id of args.pids || []) {
    const p = DATA.players && (DATA.players[id] || DATA.players[+id]);
    if (p) players[id] = {name: p.name, team: p.team, num: p.num || "", pos: p.pos, heat: p.streaks?.heat || null,
                          cur: p.windows?.cur ? {gp: p.windows.cur.gp, g: p.windows.cur.g, a: p.windows.cur.a, p: p.windows.cur.p, s: p.windows.cur.s} : null,
                          xg: p.xg_windows?.cur ? {g: p.xg_windows.cur.g, xg: p.xg_windows.cur.xg, gp: p.xg_windows.cur.gp} : null,
                          last: (p.form?.games || [])[0]?.d || null};
  }
  // every goalie's season GSAx (goals saved above expected), for a rank among regulars
  const gl = [];
  for (const g of Object.values(DATA.goalies || {})) if (g?.cur && g.cur.gp != null) gl.push({id: String(g.id), team: g.team, name: g.name, num: g.num || "", gp: g.cur.gp, sv: r3(g.cur.sv), gsax: g.cur.gsax});
  for (const g of gl) if ((args.pids || []).includes(g.id)) goalies[g.id] = g;
  const regs = gl.filter(g => g.gp >= 5).sort((a, b) => b.gsax - a.gsax);
  regs.forEach((g, i) => { if (goalies[g.id]) { goalies[g.id].rk = i + 1; goalies[g.id].of = regs.length; } });
  const fut = {};
  for (const [t, f] of Object.entries(DATA.futures?.teams || {})) fut[t] = {playoffs: f.playoffs, name: f.name, pts_now: f.pts_now, gp: f.gp};
  const ranks = {};
  for (const [t, r] of Object.entries(DATA.team_ranks?.teams || {})) ranks[t] = {pdo: r.pdo || null, gsax: r.gsax || null, xgf: r.xgf || null};
  return {date: DATA.date, players, goalies, futures: fut, ranks, archive_dates: DATA.archive_dates || []};
}
