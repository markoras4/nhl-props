"""Streak bonus + stronger depth correction for player points (does not touch the website).

backtest_hot / backtest_form showed, against the model as it stands:
  * established players (point chance 36%+) on a run of 3+ games with a point score more often than predicted
    (+2 to +5 percentage points), even after the live recent-form term;
  * below-average players (point chance < 36%) score less often than predicted, streak or not.
Starting from the live corrections (prior_toi_beta, depth_cal for forwards, form_k), two candidate fixes on the
expected goals and assists:
  streak bonus   x (1 + b)            for players with point chance >= 36% on a point run of S+ games
  depth extra    x (1 - c)            for players with point chance < 36%  (forwards and defencemen)
b, S and c are fitted on the oldest season (Brier of goal + assist + point) and judged on the newer ones.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict
from .backtest_depthcal import factor as depth_factor
from .backtest_form import add_form, adj, brier
from .backtest_hot import streak_cols, table, P_TIERS
from .grade import summarize

BS = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12]
SS = [3, 4, 5]
CS = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10]
P_CUT = 0.36


def live_frames(src, seasons, cfg, log):
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
        dc = cfg["depth_cal"]                                   # live depth correction (forwards under 17 min)
        f = depth_factor(df, dc["k"], dc["t0"], dc["vet"])
        df["lam_g"], df["lam_a"] = df.lam_g * f, df.lam_a * f
        df = add_form(df)                                       # live recent-form term
        df["lam_g"] = adj(df, "g", "lam_g", cfg["form_k"]["g"])
        df["lam_a"] = adj(df, "a", "lam_a", cfg["form_k"]["a"])
        df["season"] = season
        out.append(streak_cols(df))
    d = pd.concat(out, ignore_index=True)
    return d[d.c_gp >= 5].copy()


def apply(d, b, S, c):
    lp = d.lam_g + d.lam_a
    ppt = 1 - np.exp(-lp)
    m = np.ones(len(d))
    m = np.where((ppt >= P_CUT) & (d.p_streak >= S), 1 + b, m)
    m = np.where(ppt < P_CUT, 1 - c, m)
    return d.lam_g.values * m, d.lam_a.values * m


def score(d, lg, la):
    y = {"Goal": (d.g >= 1).values.astype(float), "Assist": (d.a >= 1).values.astype(float), "Point": (d.g + d.a >= 1).values.astype(float)}
    p = {"Goal": 1 - np.exp(-lg), "Assist": 1 - np.exp(-la), "Point": 1 - np.exp(-(lg + la))}
    return {k: summarize(list(zip(np.clip(p[k], 1e-4, 1 - 1e-4), y[k]))) for k in y}


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = live_frames(src, seasons, cfg, log)
    first = sorted(seasons)[:1]
    fit, test = d[d.season.isin(first)], d[~d.season.isin(first)]
    yf = [(fit.g >= 1).values.astype(float), (fit.a >= 1).values.astype(float), (fit.g + fit.a >= 1).values.astype(float)]
    grid = []
    for b, S, c in itertools.product(BS, SS, CS):
        lg, la = apply(fit, b, S, c)
        tot = brier(1 - np.exp(-lg), yf[0]) + brier(1 - np.exp(-la), yf[1]) + brier(1 - np.exp(-(lg + la)), yf[2])
        grid.append({"b": b, "S": S, "c": c, "brier_sum": tot})
    best = min(grid, key=lambda r: r["brier_sum"])
    base = next(r for r in grid if r["b"] == 0 and r["c"] == 0)
    # each fix on its own as well, to see which one carries the gain
    only_b = min((r for r in grid if r["c"] == 0), key=lambda r: r["brier_sum"])
    only_c = min((r for r in grid if r["b"] == 0), key=lambda r: r["brier_sum"])
    rep = {"seasons": seasons, "fit": first, "rows_fit": int(len(fit)), "rows_test": int(len(test)),
           "best": best, "base_fit": base, "only_b": only_b, "only_c": only_c}
    for name, r in (("now", {"b": 0, "S": 3, "c": 0}), ("streak only", only_b), ("depth only", only_c), ("both", best)):
        lg, la = apply(test, r["b"], r["S"], r["c"])
        rep.setdefault("test", {})[name] = {"params": {k: r[k] for k in ("b", "S", "c")}, "scores": score(test, lg, la)}
        t2 = test.copy(); t2["lam_g"], t2["lam_a"] = lg, la
        pb = lambda t: pd.cut(t.p_streak, [-1, 0, 2, 4, 99], labels=[0, 1, 2, 3]).astype(int)
        lab = [(0, "no point last game"), (1, "points in 1-2 straight"), (2, "points in 3-4 straight"), (3, "points in 5+ straight")]
        rep["test"][name]["streaks"] = table(t2, P_TIERS, t2.lam_g + t2.lam_a, (t2.g + t2.a >= 1), pb, lab)
    (out_dir / "streak.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "streak.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    p = lambda x: f"b={x['b']:.2f}, S={x['S']}, c={x['c']:.2f}"
    L = [f"# Streak bonus + depth correction: fit on {r['fit']}, tested on the other seasons of {r['seasons']}",
         f"Player-games: fit {r['rows_fit']:,} · test {r['rows_test']:,} (live depth_cal and form_k already applied)", "",
         f"Fit season, sum of Brier (goal + assist + point): now {r['base_fit']['brier_sum']:.5f} · streak only ({p(r['only_b'])}) "
         f"{r['only_b']['brier_sum']:.5f} · depth only ({p(r['only_c'])}) {r['only_c']['brier_sum']:.5f} · both ({p(r['best'])}) {r['best']['brier_sum']:.5f}", "",
         "## Test seasons (never used for fitting)", "| Version | Goal Brier | Goal skill | Assist Brier | Assist skill | Point Brier | Point skill |", "|---|---|---|---|---|---|---|"]
    for name, v in r["test"].items():
        s = v["scores"]
        L.append(f"| {name} ({p(v['params'])}) | " + " | ".join(f"{s[m]['brier']:.5f} | {s[m]['skill'] * 100:+.2f}%" for m in ("Goal", "Assist", "Point")) + " |")
    for name, v in r["test"].items():
        L += ["", f"## Point streaks on the test seasons: {name}", "| Level | Going into the game | n | Predicted | Happened | Diff | t |", "|---|---|---|---|---|---|---|"]
        L += [f"| {x['tier']} | {x['group']} | {x['n']:,} | {x['pred']:.1%} | {x['actual']:.1%} | {x['diff'] * 100:+.1f} | {x['t']:+.1f} |" for x in v["streaks"]]
    return "\n".join(L) + "\n"
