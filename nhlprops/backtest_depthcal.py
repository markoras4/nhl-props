"""Depth-forward correction: forwards with small projected roles score less than the model says (does not touch the website).

backtest_depth showed forwards projected under ~16 minutes are overrated on goals and points (about 6-16% too high),
while top-six forwards are calibrated. Candidate fix, applied to expected goals and assists of forwards only:
    factor = 1 - k * max(0, T0 - projected TOI) * (1 + v * veteran)       (floored at 0.6)
veteran = 83+ NHL games (the worst group). k, T0 and v are fitted on the older seasons (Brier of goal + point for
forwards), then judged on the newer ones: calibration by ice-time tier, Brier and log loss, before and after.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np

from .backtest_depth import frames, LIVE, TOI_F
from .grade import summarize

KS = [0.0, 0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05]
T0S = [15.0, 16.0, 17.0]
VS = [0.0, 0.5, 1.0]


def factor(d, k, t0, v):
    vet = (d.exp_gp > 82).astype(float)
    f = 1 - k * np.clip(t0 - d.toi_proj, 0, None) * (1 + v * vet)
    return np.where(d.posg == "F", np.clip(f, 0.6, 1.0), 1.0)


def probs(d, f):
    lg, la = d.lam_g * f, d.lam_a * f
    return 1 - np.exp(-lg), 1 - np.exp(-(lg + la))


def brier(d, f):
    pg, pp = probs(d, f)
    return float((((pg - (d.g >= 1)) ** 2).mean() + ((pp - (d.g + d.a >= 1)) ** 2).mean()) / 2)


def tiers(d, f):
    out = []
    pg, pp = probs(d, f)
    for lo, hi, lab in TOI_F + [(0, 14, "under 14 min, 83+ games")]:
        m = (d.posg == "F") & (d.toi_proj > lo) & (d.toi_proj <= hi)
        if "83+" in lab:
            m = m & (d.exp_gp > 82)
        if m.sum() < 300:
            continue
        y_g, y_p = (d.g[m] >= 1).astype(float), ((d.g + d.a)[m] >= 1).astype(float)
        row = {"group": lab, "n": int(m.sum())}
        for name, p, y in (("goal", pg[m], y_g), ("point", pp[m], y_p)):
            se = np.sqrt((y.var() + 1e-9) / len(y))
            row[name] = {"pred": float(np.mean(p)), "act": float(y.mean()), "t": float((y.mean() - np.mean(p)) / se)}
        out.append(row)
    return out


def scores(d, f):
    pg, pp = probs(d, f)
    F = (d.posg == "F").values
    res = {}
    for name, p, y in (("Goal", pg, d.g >= 1), ("Point", pp, d.g + d.a >= 1)):
        for grp, m in (("all", np.ones(len(d), bool)), ("F", F)):
            res[f"{name} {grp}"] = summarize(list(zip(np.clip(np.asarray(p, float)[m], 1e-4, 1 - 1e-4), np.asarray(y, float)[m])))
    return res


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, LIVE, log, {})
    d = d[d.c_gp >= 1].reset_index(drop=True)
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fit, test = d[d.season.isin(half)].reset_index(drop=True), d[~d.season.isin(half)].reset_index(drop=True)
    grid = []
    for k, t0, v in itertools.product(KS, T0S, VS):
        if k == 0 and (t0 != T0S[0] or v != 0):
            continue
        grid.append({"k": k, "t0": t0, "v": v, "brier": brier(fit, factor(fit, k, t0, v))})
    best = min(grid, key=lambda r: r["brier"])
    f0, f1 = factor(test, 0, 16, 0), factor(test, best["k"], best["t0"], best["v"])
    rep = {"seasons": seasons, "fit": half, "best": best, "grid": sorted(grid, key=lambda r: r["brier"])[:12],
           "tiers_now": tiers(test, f0), "tiers_new": tiers(test, f1),
           "scores_now": scores(test, f0), "scores_new": scores(test, f1),
           "factor_examples": {f"{t} min{' vet' if vt else ''}": float(np.clip(1 - best["k"] * max(0, best["t0"] - t) * (1 + best["v"] * vt), 0.6, 1))
                               for t in (9, 11, 13, 15, 17) for vt in (0, 1)}}
    (out_dir / "depthcal.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "depthcal.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    b = r["best"]
    L = [f"# Depth-forward correction: fitted on {r['fit']}, tested on the rest of {r['seasons']}", "",
         f"Best on the fit seasons: k = {b['k']}, T0 = {b['t0']} min, veteran extra = {b['v']} (Brier {b['brier']:.6f}; k = 0 is the live model)", "",
         "Top settings (fit seasons): " + " · ".join(f"k {g['k']} T0 {g['t0']} v {g['v']}: {g['brier']:.6f}" for g in r["grid"][:6]), "",
         "Factor on expected goals and assists: " + " · ".join(f"{k}: {v:.3f}" for k, v in r["factor_examples"].items()), ""]
    def tb(rows):
        out = ["| Forwards | n | Goal pred | Goal actual | t | Point pred | Point actual | t |", "|---|---|---|---|---|---|---|---|"]
        for x in rows:
            out.append(f"| {x['group']} | {x['n']:,} | " + " | ".join(f"{x[m]['pred']:.1%} | {x[m]['act']:.1%} | {x[m]['t']:+.1f}" for m in ("goal", "point")) + " |")
        return out
    L += ["## Test seasons: live model"] + tb(r["tiers_now"]) + ["", "## Test seasons: with the correction"] + tb(r["tiers_new"]) + [""]
    L += ["## Test seasons: accuracy", "| Market | Brier now | Brier new | Log loss now | Log loss new | Skill now | Skill new |", "|---|---|---|---|---|---|---|"]
    for m in r["scores_now"]:
        a, c = r["scores_now"][m], r["scores_new"][m]
        L.append(f"| {m} | {a['brier']:.6f} | {c['brier']:.6f} | {a['logloss']:.6f} | {c['logloss']:.6f} | {a['skill']*100:+.3f}% | {c['skill']*100:+.3f}% |")
    return "\n".join(L) + "\n"
