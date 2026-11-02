"""Season projection backtest: are the player season ranges honest? (does not touch the website)

From a cut-off date early in each past season (about 14 team games in), every skater who played in his team's last
five games is projected to the end of the season the way the site does it: his per-game goal and assist rates at
the cut-off (point in time, from the site-like model) times the games his team has left, with a share of games
missed, and with uncertainty in the true rate (Gamma with shape K). 10,000 simulated seasons per player.
Checks: does the 10-90% range contain the real final total about 80% of the time, is the median unbiased,
are the milestone chances (30 goals, 80 / 100 points) calibrated, and which K / games-played share fit best.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict

KS = (8, 12, 18, 25)
AVAIL = (0.76, 0.80, 0.84)
CONC = (1.5, 3, 6)   # how much the share of games played varies between players / seasons (Beta concentration)
N = 6000


def frames(src, season, cfg, log=print):
    games, sk, go = season_boxscores(src, season, cfg["workers"], log)
    pri = prior_frames(src, season)
    df = predict(sk, pri, games, go, season, cfg, prior_beta=1.0)
    df["date"] = pd.to_datetime(df["date"])
    # cut-off: the first date on which the median team has played 14 games
    tg = df.groupby(["team", "gid"]).date.first().reset_index().sort_values("date")
    tg["n"] = tg.groupby("team").cumcount() + 1
    cut = tg[tg.n == 14].date.median()
    before, after = df[df.date < cut], df[df.date >= cut]
    team_games_left = tg[tg.date >= cut].groupby("team").gid.nunique()
    team_games_by = tg[tg.date < cut].groupby("team").gid.nunique()
    last5 = tg[tg.date < cut].groupby("team").tail(5)
    recent = set(zip(before.merge(last5[["team", "gid"]], on=["team", "gid"]).pid, before.merge(last5[["team", "gid"]], on=["team", "gid"]).team))
    first_after = after.sort_values("date").groupby("pid").head(1).set_index("pid")
    last_before = before.sort_values("date").groupby("pid").tail(1).set_index("pid")
    rows = []
    for pid, team in recent:
        src_row = first_after.loc[pid] if pid in first_after.index else last_before.loc[pid]
        if isinstance(src_row, pd.DataFrame):
            src_row = src_row.iloc[0]
        b = before[before.pid == pid]
        a = after[after.pid == pid]
        rows.append({"season": season, "pid": pid, "team": team, "posg": src_row.posg,
                     "g0": int(b.g.sum()), "a0": int(b.a.sum()), "gp0": len(b),
                     "lg": float(src_row.lam_g), "la": float(src_row.lam_a), "left": int(team_games_left.get(team, 0)),
                     "gp_after": len(a), "g_end": int(b.g.sum() + a.g.sum()), "a_end": int(b.a.sum() + a.a.sum())})
    log(f"  {season}: cut-off {cut.date()}, {len(rows)} skaters")
    return pd.DataFrame(rows)


def sim(d: pd.DataFrame, K: float, av: float, rng, conc: float = 0) -> dict:
    n = len(d)
    share = rng.beta(av * conc, (1 - av) * conc, (n, N)) if conc else av
    games = rng.binomial(d.left.values[:, None], share, (n, N))
    mg = d.lg.values[:, None] if K > 1e8 else rng.gamma(K, d.lg.values[:, None] / K, (n, N))
    ma = d.la.values[:, None] if K > 1e8 else rng.gamma(K, d.la.values[:, None] / K, (n, N))
    G = d.g0.values[:, None] + rng.poisson(mg * games)
    A = d.a0.values[:, None] + rng.poisson(ma * games)
    P = G + A
    yp, yg = (d.g_end + d.a_end).values, d.g_end.values
    lo, med, hi = np.percentile(P, 10, axis=1), np.median(P, axis=1), np.percentile(P, 90, axis=1)
    glo, ghi = np.percentile(G, 10, axis=1), np.percentile(G, 90, axis=1)
    out = {"cover_p": float(((yp >= lo) & (yp <= hi)).mean()), "cover_g": float(((yg >= glo) & (yg <= ghi)).mean()),
           "below_p": float((yp < lo).mean()), "above_p": float((yp > hi).mean()),
           "bias_p": float((yp - med).mean()), "mae_p": float(np.abs(yp - med).mean())}
    for key, arr, line, y in (("p80", P, 80, yp), ("p100", P, 100, yp), ("g30", G, 30, yg), ("g40", G, 40, yg)):
        pr = (arr >= line).mean(axis=1)
        m = pr >= 0.05
        out[key] = {"n": int(m.sum()), "pred": float(pr[m].mean()) if m.any() else None, "act": float((y[m] >= line).mean()) if m.any() else None}
    return out


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = pd.concat([frames(src, s, cfg, log) for s in sorted(seasons)], ignore_index=True)
    d = d[(d.lg + d.la) > 0]
    rng = np.random.default_rng(11)
    rep = {"seasons": seasons, "skaters": int(len(d)), "gp_share_actual": float((d.gp_after / d.left.clip(lower=1)).mean()), "grid": {}}
    sh = (d.gp_after / d.left.clip(lower=1)).clip(0, 1)
    rep["gp_share_pct"] = {q: float(sh.quantile(q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)}
    for K in KS:
        for av in AVAIL:
            for c in CONC:
                rep["grid"][f"K{K}|av{av}|c{c}"] = sim(d, K, av, rng, c)
    top = d[(d.g0 + d.a0) / d.gp0.clip(lower=1) >= 0.7].reset_index(drop=True)
    best = sorted(rep["grid"].items(), key=lambda kv: abs(kv[1]["cover_p"] - 0.8) + abs(kv[1]["below_p"] - kv[1]["above_p"]))[:3]
    rep["best"] = [k for k, _ in best]
    rep["top_scorers"] = {}
    for k in rep["best"]:
        K_, av_, c_ = k.split("|"); rep["top_scorers"][k] = sim(top, float(K_[1:]), float(av_[2:]), rng, float(c_[1:]))
    (out_dir / "proj.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    L = [f"# Player season projections: from ~14 team games in, {rep['skaters']} skater-seasons, {seasons}", "",
         f"Actual share of team games played after the cut-off: {rep['gp_share_actual']:.1%} (10/25/50/75/90%: " + " / ".join(f"{v:.0%}" for v in rep["gp_share_pct"].values()) + ")", "",
         f"Best settings: {', '.join(rep['best'])}", "",
         "| setting | 10-90% holds points | holds goals | below | above | median bias (pts) | MAE (pts) | 100+ pts pred/act | 30+ G pred/act |", "|---|---|---|---|---|---|---|---|---|"]
    for k, v in rep["grid"].items():
        L.append(f"| {k} | {v['cover_p']:.1%} | {v['cover_g']:.1%} | {v['below_p']:.1%} | {v['above_p']:.1%} | {v['bias_p']:+.1f} | {v['mae_p']:.1f} | "
                 f"{(v['p100']['pred'] or 0):.0%} / {(v['p100']['act'] or 0):.0%} ({v['p100']['n']}) | {(v['g30']['pred'] or 0):.0%} / {(v['g30']['act'] or 0):.0%} ({v['g30']['n']}) |")
    L += ["", "Top scorers (0.7+ points per game at the cut-off):", ""]
    for k, v in rep["top_scorers"].items():
        L.append(f"- {k}: holds {v['cover_p']:.1%}, below {v['below_p']:.1%}, above {v['above_p']:.1%}, bias {v['bias_p']:+.1f}")
    md = "\n".join(L) + "\n"
    (out_dir / "proj.md").write_text(md, encoding="utf-8")
    log(md)
    return rep
