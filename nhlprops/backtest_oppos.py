"""Opponent by position: does what a team has allowed to centres / wingers / defencemen add anything beyond the
team-level defence adjustment? (does not touch the website)

For every player-game we know the model's expectation (lam_g / lam_a / lam_s) and what happened. For the opponent,
before each game, we add up (this season so far):
    A = goals (assists, shots) actually scored against them, E = what the model expected for those players.
Team level  : all skaters together         R_team = (A + K) / (E + K)
Position    : only his position (C, W, D)   R_pos  = (A_pos + K) / (E_pos x R_team + K)   (what's left after the team level)
Variants: model as is · x R_team · x R_team x R_pos · x R_pos alone. K is fitted on the older seasons and the variants
are judged on the newer ones (out of sample). Shots use K x 10 because shot counts are about 10x larger.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict, p_over
from .grade import summarize

KS = [None, 200, 100, 50, 25, 12, 6]
STATS = (("g", "lam_g"), ("a", "lam_a"), ("s", "lam_s"))


def cum_before(d, keys):
    """season-to-date sums before each date, for actual and expected, by keys (+ season, opp)"""
    cols = [x for x, _ in STATS] + [l for _, l in STATS]
    day = d.groupby(["season", "opp", *keys, "date"], as_index=False)[cols].sum().sort_values("date")
    gk = day.groupby(["season", "opp", *keys], sort=False)
    out = day[["season", "opp", *keys, "date"]].copy()
    for c in cols:
        out["c_" + c] = gk[c].cumsum() - day[c]
    out["c_n"] = gk.cumcount()
    return out


def add_cols(d):
    t = cum_before(d, []).rename(columns=lambda c: c.replace("c_", "T_") if c.startswith("c_") else c)
    p = cum_before(d, ["grp"]).rename(columns=lambda c: c.replace("c_", "P_") if c.startswith("c_") else c)
    d = d.merge(t, on=["season", "opp", "date"], how="left").merge(p, on=["season", "opp", "grp", "date"], how="left")
    return d.fillna({c: 0.0 for c in d.columns if c[:2] in ("T_", "P_")})


def factor(d, x, lam, variant, Kt, Kp):
    sc = 10.0 if x == "s" else 1.0
    lt = d[lam].values.copy()
    rt = np.ones(len(d))
    if Kt is not None:
        rt = (d["T_" + x].values + Kt * sc) / (d["T_" + lam].values + Kt * sc)
    if variant == "now":
        return lt
    if variant == "team":
        return lt * rt
    rp_base = rt if variant == "team+pos" else np.ones(len(d))
    rp = np.ones(len(d)) if Kp is None else (d["P_" + x].values + Kp * sc) / (d["P_" + lam].values * rp_base + Kp * sc)
    return lt * rp_base * rp


def probs(lg, la, ls):
    return {"Goal": 1 - np.exp(-lg), "Assist": 1 - np.exp(-la), "Point": 1 - np.exp(-(lg + la)),
            "SOG 2+": p_over(ls, 1.5), "SOG 3+": p_over(ls, 2.5)}


def outcomes(d):
    return {"Goal": (d.g >= 1).values.astype(float), "Assist": (d.a >= 1).values.astype(float),
            "Point": (d.g + d.a >= 1).values.astype(float), "SOG 2+": (d.s >= 2).values.astype(float),
            "SOG 3+": (d.s >= 3).values.astype(float)}


def brier(p, y):
    return float(((np.clip(p, 1e-6, 1 - 1e-6) - y) ** 2).mean())


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        if sk.empty:
            continue
        pri = prior_frames(src, season)
        df = predict(sk, pri, games, go, season, cfg)
        df["season"] = season
        pos = df["pos"] if "pos" in df else pd.Series(np.where(df.posg == "D", "D", "F"), index=df.index)
        df["grp"] = np.where(pos == "D", "D", np.where(pos == "C", "C", np.where(pos.isin(["L", "R", "W"]), "W", "F")))
        frames.append(df[["season", "gid", "date", "pid", "opp", "grp", "posg", "g", "a", "s", "lam_g", "lam_a", "lam_s", "c_gp"]])
    d = add_cols(pd.concat(frames, ignore_index=True))
    d = d[(d.c_gp >= 5) & (d.T_n >= 5)].copy()
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fit, test = d[d.season.isin(half)], d[~d.season.isin(half)]
    rep = {"seasons": seasons, "fit_seasons": half, "rows_fit": int(len(fit)), "rows_test": int(len(test)),
           "groups": d.grp.value_counts().to_dict(), "grid": {}, "best": {}}
    # fit K: team level first, then position on top of the team level, then position alone
    yfit = {"g": (fit.g >= 1).values.astype(float), "a": (fit.a >= 1).values.astype(float), "s": (fit.s >= 2).values.astype(float)}
    pf = lambda x, l: 1 - np.exp(-l) if x != "s" else p_over(l, 1.5)
    for x, lam in STATS:
        g_team = [{"K": K, "brier": brier(pf(x, factor(fit, x, lam, "team", K, None)), yfit[x])} for K in KS]
        kt = min(g_team, key=lambda r: r["brier"])["K"]
        g_tp = [{"K": K, "brier": brier(pf(x, factor(fit, x, lam, "team+pos", kt, K)), yfit[x])} for K in KS]
        kp = min(g_tp, key=lambda r: r["brier"])["K"]
        g_p = [{"K": K, "brier": brier(pf(x, factor(fit, x, lam, "pos", None, K)), yfit[x])} for K in KS]
        kpo = min(g_p, key=lambda r: r["brier"])["K"]
        rep["grid"][x] = {"team": g_team, "team+pos": g_tp, "pos": g_p}
        rep["best"][x] = {"team": kt, "team+pos": kp, "pos": kpo}
    B = rep["best"]

    def score(sub):
        out = {}
        y = outcomes(sub)
        for v in ("now", "team", "team+pos", "pos"):
            L = {x: factor(sub, x, lam, v, B[x]["team"], B[x]["pos"] if v == "pos" else B[x]["team+pos"]) for x, lam in STATS}
            P = probs(L["g"], L["a"], L["s"])
            out[v] = {m: summarize(list(zip(np.clip(P[m], 1e-4, 1 - 1e-4), y[m]))) for m in y}
        return out
    rep["test"] = score(test)
    rep["test_by_group"] = {gname: score(test[test.grp == gname]) for gname in sorted(test.grp.unique()) if (test.grp == gname).sum() > 2000}
    # how big the position factors get (test seasons, team+pos variant)
    for x, lam in STATS:
        f = factor(test, x, lam, "team+pos", B[x]["team"], B[x]["team+pos"]) / np.maximum(factor(test, x, lam, "team", B[x]["team"], None), 1e-9)
        rep.setdefault("spread", {})[x] = {"p10": float(np.percentile(f, 10)), "p90": float(np.percentile(f, 90))}
    (out_dir / "oppos.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "oppos.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    K = lambda k: "none" if k is None else str(k)
    L = [f"# Opponent by position: fit on {r['fit_seasons']}, tested on the rest of {r['seasons']}",
         f"Player-games: fit {r['rows_fit']:,} · test {r['rows_test']:,} · groups {r['groups']}", "",
         "Best K (fit seasons; none = no adjustment; shots use K x 10):"]
    for x, name in (("g", "goals"), ("a", "assists"), ("s", "shots")):
        b = r["best"][x]
        L.append(f"- {name}: team {K(b['team'])} · position on top of team {K(b['team+pos'])} · position alone {K(b['pos'])}")
        for v in ("team", "team+pos", "pos"):
            L.append(f"  - {v}: " + " · ".join(f"{K(g['K'])}: {g['brier']:.5f}" for g in r["grid"][x][v]))
    def table(sc):
        rows = ["| Market | Now | + team | + team + position | + position alone |", "|---|---|---|---|---|"]
        for m in sc["now"]:
            rows.append(f"| {m} | " + " | ".join(f"{sc[v][m]['brier']:.5f} ({sc[v][m]['skill'] * 100:+.2f}%)" for v in ("now", "team", "team+pos", "pos")) + " |")
        return rows
    L += ["", "## Test seasons: Brier (skill vs a constant)"] + table(r["test"])
    for gname, sc in r["test_by_group"].items():
        L += ["", f"### Only {gname}"] + table(sc)
    L += ["", "Position factor on top of team level, 10th-90th percentile: " + " · ".join(f"{x} {v['p10']:.3f}–{v['p90']:.3f}" for x, v in r["spread"].items())]
    return "\n".join(L) + "\n"
