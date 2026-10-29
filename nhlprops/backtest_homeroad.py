"""Team-specific home ice: does a team's home/road split persist? (does not touch the website)

The live model uses one league-wide home edge. For every game the replay projection (backtest_totals, which includes
that league edge) gives expected goals; residual = actual goal margin - expected margin, from the home team's side.
A team's "extra home edge" = its average residual at home minus its average residual on the road.
  1. Within a season: first half vs second half (correlation across team-seasons).
  2. Season to season: one season vs the next.
  3. As a model term: each season, the team's extra edge over the previous seasons (shrunk by K games) is added to
     the projection (half to each side); K is fitted on the earlier test seasons and judged on the later ones,
     on the home-win log loss and the margin error.
  4. Five seasons pooled: each team's extra home edge with its standard error.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .backtest_totals import season_rows
from .backtest_markets import markets

KS = [None, 400, 200, 100, 50, 25]


def split(d):
    """per team: n home, n road, mean residual home, mean residual road, extra edge (home - road)/2 in goals per game"""
    h = d.groupby("home").res.agg(["sum", "count"]).rename(columns={"sum": "hs", "count": "hn"})
    a = d.assign(r=-d.res).groupby("away").r.agg(["sum", "count"]).rename(columns={"sum": "as_", "count": "an"})
    t = h.join(a, how="outer").fillna(0)
    t["edge"] = (t.hs / t.hn.clip(lower=1) - t.as_ / t.an.clip(lower=1)) / 2
    t["n"] = 2 / (1 / t.hn.clip(lower=1) + 1 / t.an.clip(lower=1))      # effective games for the difference
    return t


def corr(a, b):
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) < 10:
        return None, 0
    r = float(np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1])
    return r, len(j)


def ll(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    first = min(seasons)
    prev = pd.DataFrame()
    for y in range(first - 3, first):
        g, _, _ = season_boxscores(src, y, cfg["workers"], log)
        prev = pd.concat([prev, g])
    frames = []
    for y in sorted(seasons):
        df, games = season_rows(src, y, cfg, prev[prev.season >= y - 3] if len(prev) else prev, log)
        df = df.merge(games[["id", "hs", "as_"]], on="id", how="left")
        df["season"] = y
        frames.append(df)
        prev = pd.concat([prev, games])
    d = pd.concat(frames)
    d = d[(d.gp_min >= 5) & d.hs.notna()].copy().sort_values(["season", "date", "id"])
    d["res"] = (d.hs - d.as_) - (d.mh - d.ma)
    d["y"] = (d.hs > d.as_).astype(float)
    league = {"home_win": float(d.y.mean()), "res_mean": float(d.res.mean())}

    # 1. within season halves
    halves_a, halves_b = [], []
    for y, s in d.groupby("season"):
        mid = s.date.sort_values().iloc[len(s) // 2]
        a, b = split(s[s.date < mid]), split(s[s.date >= mid])
        halves_a.append(a.edge.rename(lambda t: f"{t}{y}")); halves_b.append(b.edge.rename(lambda t: f"{t}{y}"))
    r_half, n_half = corr(pd.concat(halves_a), pd.concat(halves_b))
    # 2. season to season
    per = {y: split(s).edge for y, s in d.groupby("season")}
    xs, ys = [], []
    for y in sorted(per)[:-1]:
        if y + 1 in per:
            xs.append(per[y].rename(lambda t: f"{t}{y}")); ys.append(per[y + 1].rename(lambda t: f"{t}{y}"))
    r_season, n_season = corr(pd.concat(xs), pd.concat(ys))
    # 3. as a model term: prior seasons' edge, shrunk
    tb = cfg.get("tie_boost", 1.0)
    ss = sorted(seasons)
    eval_seasons = ss[1:]
    fit_s, test_s = eval_seasons[: max(1, len(eval_seasons) // 2)], eval_seasons[max(1, len(eval_seasons) // 2):]

    def run_k(K, which):
        p_all, y_all, e_all = [], [], []
        for y in which:
            past = split(d[d.season < y])
            s = d[d.season == y]
            for r in s.itertuples():
                adj = 0.0
                if K is not None and r.home in past.index:
                    t = past.loc[r.home]; adj = float(t.edge * t.n / (t.n + K))
                mh, ma = max(0.3, r.mh + adj / 2), max(0.3, r.ma - adj / 2)
                m = markets(mh, ma, tb, 0.3, 1.0)
                p_all.append(m["h1"] + m["x"] * mh / (mh + ma)); y_all.append(r.y); e_all.append(((r.hs - r.as_) - (mh - ma)) ** 2)
        return ll(np.array(p_all), np.array(y_all)), float(np.mean(e_all))
    grid = [{"K": K, "ll_fit": run_k(K, fit_s)[0]} for K in KS]
    bestK = min(grid, key=lambda g: g["ll_fit"])["K"]
    test = {"none": run_k(None, test_s), "best": run_k(bestK, test_s)}
    # 4. pooled five seasons
    pool = split(d)
    pool["se"] = d.res.std() * np.sqrt(1 / pool.hn + 1 / pool.an) / 2
    pool["t"] = pool.edge / pool.se
    top = pool.sort_values("edge", ascending=False)
    rep = {"seasons": seasons, "games": int(len(d)), "league": league,
           "r_half": r_half, "n_half": n_half, "r_season": r_season, "n_season": n_season,
           "grid": grid, "bestK": bestK, "fit_seasons": fit_s, "test_seasons": test_s,
           "test": {k: {"ll": v[0], "mse_margin": v[1]} for k, v in test.items()},
           "pooled": [{"team": t, "edge": float(r.edge), "se": float(r.se), "t": float(r.t), "home_games": int(r.hn)} for t, r in top.iterrows()],
           "spread_sd": float(pool.edge.std()), "noise_sd": float(pool.se.mean())}
    (out_dir / "homeroad.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "homeroad.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    f = lambda x: "–" if x is None else f"{x:+.3f}"
    L = [f"# Team-specific home ice, seasons {r['seasons']} ({r['games']:,} games)", "",
         f"League: home team won {r['league']['home_win']:.1%}; average margin residual after the league home edge {r['league']['res_mean']:+.3f} goals", "",
         "Extra home edge = (avg residual at home − avg residual on the road) / 2, in goals per game beyond the league edge.", "",
         "## Does it persist?", "| Test | Correlation | Team-seasons |", "|---|---|---|",
         f"| First half → second half of the same season | {f(r['r_half'])} | {r['n_half']} |",
         f"| One season → the next | {f(r['r_season'])} | {r['n_season']} |", "",
         f"Spread of teams' pooled extra edge: sd {r['spread_sd']:.3f} goals; average noise (standard error) {r['noise_sd']:.3f}", "",
         "## As a model term (prior seasons' edge, shrunk by K games)",
         "Fit seasons " + str(r["fit_seasons"]) + ": " + " · ".join(f"K {g['K']}: {g['ll_fit']:.5f}" for g in r["grid"]) + f" → best K = {r['bestK']} (None = league edge only)", "",
         f"Test seasons {r['test_seasons']}: log loss league-only {r['test']['none']['ll']:.5f} vs with team edge {r['test']['best']['ll']:.5f} · margin MSE {r['test']['none']['mse_margin']:.4f} vs {r['test']['best']['mse_margin']:.4f}", "",
         "## Five seasons pooled: extra home edge by team", "| Team | Extra edge (goals/game) | ± se | t | Home games |", "|---|---|---|---|---|"]
    for x in r["pooled"]:
        L.append(f"| {x['team']} | {x['edge']:+.3f} | {x['se']:.3f} | {x['t']:+.1f} | {x['home_games']} |")
    return "\n".join(L) + "\n"
