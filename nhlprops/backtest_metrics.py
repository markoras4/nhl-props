"""Two new player metrics, tested like Corsi was: are they a skill (repeatable) and do they help the model?
(does not touch the website)

Spread Index (SI)
  Two players with the same points per game can hit "1+ point" at different rates: one scores in bunches, one
  steadily. SI = his share of games with a point - the share a typical player with the same points per game gets
  (league curve by points-per-game, same season). +5% = he spreads his points 5 points more evenly than normal.
  Model use: point chance + w * SI(last season),  2+ points chance - v * SI(last season).

Script Beta (gamma)
  How strongly his scoring rides on his team's scoring. Given the team scored TG goals in the game (its expected
  goals ETG = team goals per game so far, shrunk to the league), his point chance is
      1 - exp(-model_points * (TG / ETG) ** gamma)
  gamma 0 = he scores regardless of how the team does, 1 = in proportion, >1 = only in blow-outs.
  Use: same-game combos (player point + team over). Tested: one league gamma vs each player's own (from last season).

Both are judged on (1) split-half reliability inside a season (odd vs even games), (2) season-to-season correlation,
(3) the model's Brier when the metric from last season is added (fitted on the oldest pair of seasons, tested on the rest).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict
from .backtest_depthcal import factor as depth_factor
from .backtest_form import add_form, adj, brier

GAMMAS = np.round(np.arange(0.0, 2.01, 0.1), 2)
MIN_GP = 40


def frames(src, seasons, cfg, log):
    out = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        if sk.empty:
            continue
        pri = prior_frames(src, season)
        df = predict(sk, pri, games, go, season, cfg, prior_beta=cfg.get("prior_toi_beta", 1.0))
        P = pri["sk"]
        prev_gp = P.groupby("pid").gp.sum() if len(P) else pd.Series(dtype=float)
        df["exp_gp"] = df.pid.map(prev_gp).fillna(0) + df.c_gp
        dc = cfg["depth_cal"]
        f = depth_factor(df, dc["k"], dc["t0"], dc["vet"])
        df["lam_g"], df["lam_a"] = df.lam_g * f, df.lam_a * f
        df = add_form(df)
        df["lam_g"] = adj(df, "g", "lam_g", cfg["form_k"]["g"])
        df["lam_a"] = adj(df, "a", "lam_a", cfg["form_k"]["a"])
        # team goals in the game, and the team's expected goals before it
        tg = sk.groupby(["gid", "team"]).g.sum().rename("tg").reset_index()
        tg = tg.merge(games[["id", "date"]], left_on="gid", right_on="id").sort_values(["team", "date", "gid"])
        tg["c_gf"] = tg.groupby("team").tg.cumsum() - tg.tg
        tg["c_n"] = tg.groupby("team").cumcount()
        lg = tg.tg.mean()
        tg["etg"] = (tg.c_gf + 10 * lg) / (tg.c_n + 10)
        df = df.join(tg.set_index(["gid", "team"])[["tg", "etg"]], on=["gid", "team"])
        df["season"] = season
        out.append(df)
    d = pd.concat(out, ignore_index=True)
    d["pt"] = d.g + d.a
    d["lam_p"] = d.lam_g + d.lam_a
    d = d.sort_values(["pid", "season", "date", "gid"])
    d["half"] = d.groupby(["pid", "season"]).cumcount() % 2
    return d


def league_curve(ps):
    """Typical hit rate for a given points per game (per season, binned, interpolated)."""
    out = pd.Series(index=ps.index, dtype=float)
    for s, t in ps.groupby("season"):
        t = t[t.gp >= 20]
        bins = np.quantile(t.ppg, np.linspace(0, 1, 16))
        mid = t.groupby(pd.cut(t.ppg, np.unique(bins), include_lowest=True), observed=True).agg(x=("ppg", "mean"), y=("hit", "mean"))
        m = ps.season == s
        out[m] = np.interp(ps.ppg[m], mid.x, mid.y)
    return out


def spread(d):
    def agg(x):
        g = x.groupby(["pid", "season"])
        r = g.agg(gp=("pt", "size"), pts=("pt", "sum"), posg=("posg", "first"))
        r["hit"] = g.pt.apply(lambda v: (v >= 1).mean())
        r["multi"] = g.pt.apply(lambda v: (v >= 2).mean())
        r["ppg"] = r.pts / r.gp
        return r.reset_index()
    ps = agg(d)
    ps["si"] = ps.hit - league_curve(ps)
    ps["si_pois"] = ps.hit - (1 - np.exp(-ps.ppg))
    halves = []
    for h in (0, 1):
        x = agg(d[d.half == h]); x["si"] = x.hit - league_curve(x); halves.append(x.set_index(["pid", "season"]).si.rename(f"si{h}"))
    ps = ps.join(pd.concat(halves, axis=1), on=["pid", "season"])
    return ps


def gamma_fit(d, g_lg=None, K=0):
    """Each player-season's best gamma (grid), shrunk toward g_lg with K games."""
    r = d.tg / d.etg
    sse = {}
    for gm in GAMMAS:
        p = 1 - np.exp(-d.lam_p * r ** gm)
        sse[gm] = ((p - (d.pt >= 1)) ** 2).groupby([d.pid, d.season]).sum()
    S = pd.DataFrame(sse)
    best = S.idxmin(axis=1).astype(float)
    n = d.groupby(["pid", "season"]).size()
    return best, n


def rel(a, b):
    m = a.notna() & b.notna()
    return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 10 else float("nan")


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, log)
    ss = sorted(d.season.unique())
    rep = {"seasons": [int(s) for s in ss]}

    # ---------------- Spread Index
    ps = spread(d)
    q = ps[ps.gp >= MIN_GP]
    sb = lambda r: 2 * r / (1 + r)                                         # split-half -> full season
    rep["si_split_half"] = {"r": rel(q.si0, q.si1), "full_season": sb(rel(q.si0, q.si1)), "n": int(len(q))}
    nxt = q.assign(season=q.season + 1).set_index(["pid", "season"])
    pair = q.join(nxt[["si", "si_pois", "ppg", "gp"]], on=["pid", "season"], rsuffix="_prev").dropna(subset=["si_prev"])
    rep["si_year_to_year"] = {"r": rel(pair.si_prev, pair.si), "r_poisson_version": rel(pair.si_pois_prev, pair.si_pois),
                              "r_ppg": rel(pair.ppg_prev, pair.ppg), "n": int(len(pair))}
    rep["si_spread"] = {"sd": float(q.si.std()), "noise_sd": float(np.sqrt((q.hit * (1 - q.hit) / q.gp).mean())),
                        "corr_with_ppg": rel(q.si, q.ppg),
                        "by_pos": {p: float(t.si.mean()) for p, t in q.groupby("posg")}}

    # model use: last season's SI added to this season's point / 2+ point chances
    prev = ps[ps.gp >= 20].assign(season=ps.season + 1).set_index(["pid", "season"])[["si", "gp"]].rename(columns={"si": "si_prev", "gp": "gp_prev"})
    e = d[d.c_gp >= 5].join(prev, on=["pid", "season"])
    e["si_prev"] = e.si_prev.fillna(0)
    p1 = 1 - np.exp(-e.lam_p)
    p2 = 1 - np.exp(-e.lam_p) * (1 + e.lam_p)
    y1, y2 = (e.pt >= 1).astype(float), (e.pt >= 2).astype(float)
    fit, test = e.season == ss[1], e.season > ss[1]
    W = np.round(np.arange(0, 1.51, 0.1), 2)
    bw = min(W, key=lambda w: brier(np.clip(p1[fit] + w * e.si_prev[fit], .001, .999), y1[fit]))
    bv = min(W, key=lambda v: brier(np.clip(p2[fit] - v * e.si_prev[fit], .001, .999), y2[fit]))
    has = test & (e.gp_prev >= 40)
    rep["si_model"] = {"fit_season": int(ss[1]), "w": float(bw), "v": float(bv), "n_test": int(test.sum()),
                       "point": {"now": brier(p1[test], y1[test]), "with_si": brier(np.clip(p1[test] + bw * e.si_prev[test], .001, .999), y1[test])},
                       "point_2plus": {"now": brier(p2[test], y2[test]), "with_si": brier(np.clip(p2[test] - bv * e.si_prev[test], .001, .999), y2[test])},
                       "point_players_with_history": {"n": int(has.sum()), "now": brier(p1[has], y1[has]),
                                                      "with_si": brier(np.clip(p1[has] + bw * e.si_prev[has], .001, .999), y1[has])}}
    # calibration by last season's SI (test seasons): does the model miss spread players?
    tb = e[has].assign(p1=p1[has], y1=y1[has], b=pd.qcut(e.si_prev[has], 5, labels=False))
    rep["si_quintiles"] = [{"q": int(k) + 1, "si_prev": float(t.si_prev.mean()), "n": int(len(t)), "pred": float(t.p1.mean()),
                            "actual": float(t.y1.mean())} for k, t in tb.groupby("b")]

    # ---------------- Script Beta
    g = d[(d.c_gp >= 5) & d.etg.notna()].copy()
    r = g.tg / g.etg
    y = (g.pt >= 1).astype(float)
    fitg, testg = g.season == ss[1], g.season > ss[1]
    lg_curve = {float(gm): brier(1 - np.exp(-g.lam_p[fitg] * r[fitg] ** gm), y[fitg]) for gm in GAMMAS}
    g_lg = min(lg_curve, key=lg_curve.get)
    best, n = gamma_fit(g)
    gb = pd.DataFrame({"gam": best, "n": n}).reset_index()
    gb = gb[gb.n >= MIN_GP]
    gh = []
    for h in (0, 1):
        b2, n2 = gamma_fit(g[g.half == h]); gh.append(b2.rename(f"g{h}"))
    gb = gb.join(pd.concat(gh, axis=1), on=["pid", "season"])
    gn = gb.assign(season=gb.season + 1).set_index(["pid", "season"])
    gp_ = gb.join(gn[["gam"]], on=["pid", "season"], rsuffix="_prev").dropna(subset=["gam_prev"])
    rep["beta_league"] = {"gamma": g_lg, "curve": lg_curve}
    rep["beta_split_half"] = {"r": rel(gb.g0, gb.g1), "full_season": sb(rel(gb.g0, gb.g1)), "n": int(len(gb))}
    rep["beta_year_to_year"] = {"r": rel(gp_.gam_prev, gp_.gam), "n": int(len(gp_)), "sd": float(gb.gam.std())}
    # player gamma from last season, shrunk toward the league with K games (K fitted on the fit season)
    pv = gb.assign(season=gb.season + 1).set_index(["pid", "season"])[["gam", "n"]]
    gg = g.join(pv.rename(columns={"gam": "gam_prev", "n": "n_prev"}), on=["pid", "season"])
    def score(mask, K):
        gm = np.where(gg.gam_prev.notna(), (gg.n_prev.fillna(0) * gg.gam_prev.fillna(g_lg) + K * g_lg) / (gg.n_prev.fillna(0) + K), g_lg)
        p = 1 - np.exp(-gg.lam_p * r ** gm)
        return brier(p[mask], y[mask])
    Ks = [0, 20, 40, 80, 160, 320, 640, 1e9]
    bk = min(Ks, key=lambda K: score(fitg, K))
    hp = testg & gg.gam_prev.notna()
    rep["beta_model"] = {"K": bk, "n_test": int(testg.sum()),
                         "ignore_team_goals": brier((1 - np.exp(-g.lam_p))[testg], y[testg]),
                         "league_gamma": score(testg, 1e9), "player_gamma": score(testg, bk),
                         "players_with_history": {"n": int(hp.sum()), "league_gamma": score(hp, 1e9), "player_gamma": score(hp, bk)}}
    # what it looks like: point rate by team goals for low / high gamma players (previous season), test seasons
    t = gg[hp].assign(y=y[hp], tgc=g.tg[hp].clip(upper=6), grp=pd.qcut(gg.gam_prev[hp], 3, labels=["low", "mid", "high"]))
    rep["beta_table"] = {str(k): {int(a): float(b) for a, b in s.groupby("tgc").y.mean().items()} for k, s in t.groupby("grp", observed=True)}
    rep["beta_table_n"] = {str(k): int(len(s)) for k, s in t.groupby("grp", observed=True)}
    rep["beta_table_gamma"] = {str(k): float(s.gam_prev.mean()) for k, s in t.groupby("grp", observed=True)}

    (out_dir / "metrics.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "metrics.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    s, m = r["si_model"], r["beta_model"]
    L = [f"# New metrics: Spread Index and Script Beta ({r['seasons']})", "",
         "## Spread Index", f"- split-half reliability (odd vs even games, {r['si_split_half']['n']} player-seasons with {MIN_GP}+ GP): "
         f"r = {r['si_split_half']['r']:.3f} -> full season {r['si_split_half']['full_season']:.3f}",
         f"- season to season: r = {r['si_year_to_year']['r']:.3f} (Poisson version {r['si_year_to_year']['r_poisson_version']:.3f}; "
         f"points per game for comparison {r['si_year_to_year']['r_ppg']:.3f}; n {r['si_year_to_year']['n']})",
         f"- spread between players: sd {r['si_spread']['sd']*100:.2f} points vs pure-luck sd {r['si_spread']['noise_sd']*100:.2f}; "
         f"corr with PPG {r['si_spread']['corr_with_ppg']:.2f}; mean by position {', '.join(f'{k} {v*100:+.2f}' for k, v in r['si_spread']['by_pos'].items())}",
         f"- model (fit {s['fit_season']}, w={s['w']}, v={s['v']}), test Brier, {s['n_test']:,} player-games:",
         f"  - point: now {s['point']['now']:.5f} · with SI {s['point']['with_si']:.5f}",
         f"  - 2+ points: now {s['point_2plus']['now']:.5f} · with SI {s['point_2plus']['with_si']:.5f}",
         f"  - point, players with 40+ GP last season ({s['point_players_with_history']['n']:,}): now {s['point_players_with_history']['now']:.5f} · with SI {s['point_players_with_history']['with_si']:.5f}",
         "", "| Last season's SI quintile | mean SI | n | Model point chance | Happened | Diff |", "|---|---|---|---|---|---|"]
    L += [f"| {x['q']} | {x['si_prev']*100:+.1f} | {x['n']:,} | {x['pred']:.1%} | {x['actual']:.1%} | {(x['actual']-x['pred'])*100:+.1f} |" for x in r["si_quintiles"]]
    L += ["", "## Script Beta", f"- league gamma (fit season): {r['beta_league']['gamma']}",
          f"- split-half reliability: r = {r['beta_split_half']['r']:.3f} -> full season {r['beta_split_half']['full_season']:.3f} (n {r['beta_split_half']['n']})",
          f"- season to season: r = {r['beta_year_to_year']['r']:.3f} (n {r['beta_year_to_year']['n']}), sd between players {r['beta_year_to_year']['sd']:.2f}",
          f"- point chance given the team's actual goals, test Brier ({m['n_test']:,}): ignoring team goals {m['ignore_team_goals']:.5f} · league gamma {m['league_gamma']:.5f} · "
          f"player gamma (K={m['K']}) {m['player_gamma']:.5f}",
          f"  - players with history ({m['players_with_history']['n']:,}): league {m['players_with_history']['league_gamma']:.5f} · player {m['players_with_history']['player_gamma']:.5f}",
          "", "| Last season's gamma | mean gamma | n | " + " | ".join(f"team {k} goals" if k < 6 else "team 6+" for k in range(7)) + " |", "|---|---|---|" + "---|" * 7]
    for k, row in r["beta_table"].items():
        L.append(f"| {k} | {r['beta_table_gamma'][k]:.2f} | {r['beta_table_n'][k]:,} | " + " | ".join(f"{row.get(i, float('nan')):.1%}" for i in range(7)) + " |")
    return "\n".join(L) + "\n"
