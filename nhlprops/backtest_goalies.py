"""Goalie ratings: how much weight on this season, and how hard to pull toward average? (does not touch the website)

For every start we predict the goals he allows on the shots he actually faced:
    expected goals against = shots faced x league goals per shot x his rating
His rating = (goals allowed + k x average) / (expected goals + k x 1) over a weighted window:
  * previous seasons from MoneyPuck (goals vs expected goals, weights 3 / 2 / 1 for 1, 2, 3 seasons ago)
  * this season so far, before the game (goals vs shots x league goals per shot), with weight wc
k = how hard the rating is pulled toward an average goalie (in expected goals).
Scored by Poisson log loss of his goals allowed; the live site uses wc = 3 (same as last season) and k = 120.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames

KS = [30, 60, 120, 240, 480]
WCS = [0, 1, 2, 3, 5]


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        pri = prior_frames(src, season)["go"]
        prior = {}
        if len(pri):
            for pid, m in pri.groupby("pid"):
                w = m.season.map({season - 1: 3, season - 2: 2, season - 3: 1}).fillna(0)
                prior[int(pid)] = (float((m.ga * w).sum()), float((m.xga * w).sum()))
        go = go.copy()
        go["sa"] = pd.to_numeric(go["sa"], errors="coerce")
        st = go[go.starter & go.sa.notna() & (go.sa >= 10)].sort_values("date")
        lg_g = lg_s = 0.0
        cur: dict[int, list] = {}      # pid -> [goals allowed, shots faced]
        for date, day in st.groupby("date", sort=True):
            gps = (lg_g + 3000 * 0.095) / (lg_s + 3000) if True else 0.095
            for r in day.itertuples():
                pg, px = prior.get(int(r.pid), (0.0, 0.0))
                cg, cs = cur.get(int(r.pid), [0.0, 0.0])
                rows.append({"season": season, "pid": int(r.pid), "ga": int(r.ga), "sa": float(r.sa), "gps": gps,
                             "pg": pg, "px": px, "cg": cg, "cx": cs * gps, "gp_cur": len([1]) if cs else 0, "cs": cs})
            for r in day.itertuples():
                c = cur.setdefault(int(r.pid), [0.0, 0.0]); c[0] += r.ga; c[1] += r.sa
                lg_g += r.ga; lg_s += r.sa
    d = pd.DataFrame(rows)
    y = d.ga.values.astype(float)

    def ll(lam):
        lam = np.clip(lam, 1e-6, None)
        return float(np.mean(lam - y * np.log(lam) + np.array([math.lgamma(v + 1) for v in y])))

    base = d.sa.values * d.gps.values
    rep = {"seasons": seasons, "starts": int(len(d)), "average_goalie": ll(base)}
    # league GA/xGA level in MoneyPuck priors differs a little from goals/shot; normalise priors to 1.0 on average
    lg_prior = d.pg.sum() / d.px.sum() if d.px.sum() else 1.0
    grid = []
    for k in KS:
        for wc in WCS:
            ga = d.pg.values / lg_prior + wc * d.cg.values
            xga = d.px.values + wc * d.cx.values
            ratio = (ga + k) / (xga + k)
            grid.append({"k": k, "wc": wc, "logloss": ll(base * ratio)})
    rep["grid"] = grid
    best = min(grid, key=lambda r: r["logloss"])
    rep["best"] = best
    rep["live"] = next(r for r in grid if r["k"] == 120 and r["wc"] == 3)
    # season-to-date only (no history, no regression) for comparison
    raw = np.where(d.cx.values > 0, d.cg.values / np.maximum(d.cx.values, 1e-6), 1.0)
    rep["season_only_no_regression"] = ll(base * np.clip(raw, 0.3, 3))
    (out_dir / "goalies.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "goalies.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# Goalie ratings: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}", f"Starts: {r['starts']:,} (lower log loss = better)", "",
         f"- every goalie average: {r['average_goalie']:.5f}",
         f"- this season only, no regression: {r['season_only_no_regression']:.5f}",
         f"- live setting (k=120, this season weight 3): {r['live']['logloss']:.5f}",
         f"- best: k={r['best']['k']}, this season weight {r['best']['wc']}: {r['best']['logloss']:.5f}", "",
         "| k (pull to average) | " + " | ".join(f"weight {w}" for w in WCS) + " |", "|---|" + "---|" * len(WCS)]
    for k in KS:
        L.append(f"| {k} | " + " | ".join(f"{next(x['logloss'] for x in r['grid'] if x['k'] == k and x['wc'] == w):.5f}" for w in WCS) + " |")
    return "\n".join(L) + "\n"
