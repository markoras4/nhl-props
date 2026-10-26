"""Recent-form term: does giving his last 10 games a little more weight improve the model? (does not touch the website)

For every player-game: E10 = what the model expected over his previous 10 games, X10 = what he actually did.
Form-adjusted expectation = model x (X10 + K) / (E10 + K). Small K = trust recent form more; K = infinity = no change.
Fitted separately for goals, assists and shots on goal. K is chosen on the older seasons and judged on the newer ones
(out of sample). Then the streak table from backtest_hot is repeated with the chosen K.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict, p_over
from .backtest_hot import streak_cols, table, G_TIERS, P_TIERS
from .grade import summarize

KS = [None, 160, 120, 80, 60, 40, 20]


def add_form(df):
    df = df.sort_values(["pid", "date", "gid"]).copy()
    g = df.groupby("pid", sort=False)
    for x, lam in (("g", "lam_g"), ("a", "lam_a"), ("s", "lam_s")):
        df[f"X10_{x}"] = g[x].transform(lambda v: v.shift(1).rolling(10, min_periods=1).sum()).fillna(0.0)
        df[f"E10_{x}"] = g[lam].transform(lambda v: v.shift(1).rolling(10, min_periods=1).sum()).fillna(0.0)
    return df


def adj(d, x, lam, K):
    if K is None:
        return d[lam].values
    return d[lam].values * (d[f"X10_{x}"].values + K) / (d[f"E10_{x}"].values + K)


def brier(p, y):
    return float(((np.clip(p, 1e-6, 1 - 1e-6) - y) ** 2).mean())


def scores(d, Kg, Ka, Ks):
    lg, la, ls = adj(d, "g", "lam_g", Kg), adj(d, "a", "lam_a", Ka), adj(d, "s", "lam_s", Ks)
    y = {"Goal": (d.g >= 1).values.astype(float), "Assist": (d.a >= 1).values.astype(float),
         "Point": (d.g + d.a >= 1).values.astype(float), "SOG 2+": (d.s >= 2).values.astype(float), "SOG 3+": (d.s >= 3).values.astype(float)}
    p = {"Goal": 1 - np.exp(-lg), "Assist": 1 - np.exp(-la), "Point": 1 - np.exp(-(lg + la)),
         "SOG 2+": p_over(ls, 1.5), "SOG 3+": p_over(ls, 2.5)}
    return {m: summarize(list(zip(np.clip(p[m], 1e-4, 1 - 1e-4), y[m]))) for m in y}


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        if sk.empty:
            continue
        pri = prior_frames(src, season)
        df = add_form(predict(sk, pri, games, go, season, cfg))
        df["season"] = season
        frames.append(streak_cols(df))
    d = pd.concat(frames)
    d = d[d.c_gp >= 5].copy()
    half = sorted(seasons)[: len(seasons) // 2]
    fit, test = d[d.season.isin(half)], d[~d.season.isin(half)]
    rep = {"seasons": seasons, "fit_seasons": half, "rows_fit": int(len(fit)), "rows_test": int(len(test)), "grid": {}}
    best = {}
    for x, lam, y in (("g", "lam_g", (fit.g >= 1)), ("a", "lam_a", (fit.a >= 1)), ("s", "lam_s", (fit.s >= 2))):
        res = []
        for K in KS:
            l = adj(fit, x, lam, K)
            p = 1 - np.exp(-l) if x != "s" else p_over(l, 1.5)
            res.append({"K": K, "brier": brier(p, y.values.astype(float))})
        rep["grid"][x] = res
        best[x] = min(res, key=lambda r: r["brier"])["K"]
    rep["best"] = best
    rep["test_current"] = scores(test, None, None, None)
    rep["test_form"] = scores(test, best["g"], best["a"], best["s"])
    # streak table on the test seasons with the form term
    t2 = test.copy()
    t2["lam_g"] = adj(t2, "g", "lam_g", best["g"]); t2["lam_a"] = adj(t2, "a", "lam_a", best["a"])
    pb = lambda t: pd.cut(t.p_streak, [-1, 0, 2, 4, 99], labels=[0, 1, 2, 3]).astype(int)
    lab = [(0, "no point last game"), (1, "points in 1-2 straight"), (2, "points in 3-4 straight"), (3, "points in 5+ straight")]
    rep["points_before"] = table(test, P_TIERS, test.lam_g + test.lam_a, (test.g + test.a >= 1), pb, lab)
    rep["points_after"] = table(t2, P_TIERS, t2.lam_g + t2.lam_a, (t2.g + t2.a >= 1), pb, lab)
    (out_dir / "form.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "form.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    K = lambda k: "none" if k is None else str(k)
    L = [f"# Recent-form term: fit on {r['fit_seasons']}, tested on the other seasons of {r['seasons']}",
         f"Player-games: fit {r['rows_fit']:,} · test {r['rows_test']:,}", "",
         "Grid (fit seasons, Brier, lower is better):"]
    for x, name in (("g", "goals"), ("a", "assists"), ("s", "shots 2+")):
        L.append(f"- {name}: " + " · ".join(f"K={K(v['K'])}: {v['brier']:.5f}" for v in r["grid"][x]) + f"  → best {K(r['best'][x])}")
    L += ["", "## Test seasons (never used for fitting)", "| Market | Brier now | Skill now | Brier with form | Skill with form |", "|---|---|---|---|---|"]
    for m in r["test_current"]:
        a, b = r["test_current"][m], r["test_form"][m]
        L.append(f"| {m} | {a['brier']:.5f} | {a['skill'] * 100:+.2f}% | {b['brier']:.5f} | {b['skill'] * 100:+.2f}% |")
    def tb(rows):
        return ["| Level | Going into the game | n | Predicted | Happened | Diff | t |", "|---|---|---|---|---|---|---|"] + \
               [f"| {x['tier']} | {x['group']} | {x['n']:,} | {x['pred']:.1%} | {x['actual']:.1%} | {x['diff'] * 100:+.1f} | {x['t']:+.1f} |" for x in rows]
    L += ["", "## Point streaks, test seasons: now"] + tb(r["points_before"]) + ["", "## Point streaks, test seasons: with form"] + tb(r["points_after"])
    return "\n".join(L) + "\n"
