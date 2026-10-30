"""Long shots: do multi-point / 2+ goal probabilities come out right? (does not touch the website)

The site turns expected goals / assists into 'at least k' chances with a plain Poisson. For low-scoring players
(and big favourites) that tail can be off. For every player-game (site-like model: usage weights + recent form),
actual vs predicted by expected-value bin, for 2+ goals, 3+ goals, 2+ points, 3+ points, 2+ assists. Correction
factors per bin are fitted on the older seasons and judged on the newer ones (Brier, log loss).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .backtest_usage import frames, parts, lams
from .grade import summarize

MK = {"g2": ("g", 2), "g3": ("g", 3), "pt2": ("pt", 2), "pt3": ("pt", 3), "a2": ("a", 2)}
BINS = {"g": [0, .06, .1, .15, .2, .3, .45, 9], "a": [0, .1, .15, .2, .3, .4, .55, 9], "pt": [0, .15, .25, .35, .5, .7, .9, 1.2, 9]}


def at_least(lam, k):
    lam = np.asarray(lam, float); term = np.exp(-lam); cdf = np.zeros_like(lam)
    for i in range(k):
        if i:
            term = term * lam / i
        cdf += term
    return np.clip(1 - cdf, 0, 1)


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, log)
    d = d[(d.c_gp >= 5) & (d.lam_g > 0)].reset_index(drop=True)
    pt = parts(d, cfg)
    um = cfg.get("usage_mult") or {}
    lg, _ = lams(d, pt, cfg, um.get("s_sog", 2.0), um.get("xg_goal", 4.0))
    lg = np.asarray(lg, float); la = d.lam_a.values * (np.asarray(lg) / np.maximum(d.lam_g.values, 1e-9)) ** 0  # assists as built
    lam = {"g": lg, "a": la, "pt": lg + la}
    act = {"g": d.g.values, "a": d.a.values, "pt": (d.g + d.a).values}
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fm = d.season.isin(half).values; tm = ~fm
    rep = {"seasons": seasons, "fit_seasons": half, "rows_test": int(tm.sum()), "markets": {}}
    for m, (base, k) in MK.items():
        L, y = lam[base], (act[base] >= k).astype(float)
        p = at_least(L, k)
        edges = BINS[base]
        knots, rows = [], []
        for lo, hi in zip(edges[:-1], edges[1:]):
            mf, mt = fm & (L >= lo) & (L < hi), tm & (L >= lo) & (L < hi)
            if mf.sum() < 300:
                continue
            fac = float(y[mf].sum() / max(p[mf].sum(), 1e-9))
            knots.append([float(L[mf].mean()), fac])
            rows.append({"bin": f"{lo:.2f}-{hi:.2f}", "lam": float(L[mf].mean()), "n_fit": int(mf.sum()), "fac_fit": fac,
                         "n_test": int(mt.sum()), "pred_test": float(p[mt].mean()) if mt.sum() else None,
                         "act_test": float(y[mt].mean()) if mt.sum() else None, "count_test": int(y[mt].sum())})
        # shrink factors toward 1 by sample (2+ counts in the fit bin)
        for kn, r in zip(knots, rows):
            n_ev = r["fac_fit"] * float(p[fm & (L >= float(r["bin"].split("-")[0])) & (L < float(r["bin"].split("-")[1]))].sum())
            w = n_ev / (n_ev + 30)
            kn[1] = 1 + w * (kn[1] - 1)
            r["fac"] = kn[1]
        xs, fs = np.array([x for x, _ in knots]), np.array([f for _, f in knots])
        corr = lambda Lv: np.interp(Lv, xs, fs) if len(xs) else np.ones_like(Lv)
        pc = np.clip(p * corr(L), 0, 1)
        rep["markets"][m] = {"knots": [[round(x, 4), round(f, 4)] for x, f in knots], "bins": rows,
                             "test_before": summarize(list(zip(np.clip(p[tm], 1e-5, 1 - 1e-5), y[tm]))),
                             "test_after": summarize(list(zip(np.clip(pc[tm], 1e-5, 1 - 1e-5), y[tm])))}
    (out_dir / "tail.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "tail.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# Long-shot markets: fit on {r['fit_seasons']}, tested on the rest of {r['seasons']} ({r['rows_test']:,} test player-games)", ""]
    for m, x in r["markets"].items():
        b, a = x["test_before"], x["test_after"]
        L += [f"## {m}  (Brier {b['brier']:.6f} -> {a['brier']:.6f}, skill {b['skill']*100:+.2f}% -> {a['skill']*100:+.2f}%, log loss {b['logloss']:.5f} -> {a['logloss']:.5f})",
              "| Expected | n fit | factor (fit, raw) | factor used | n test | pred test | actual test | count |", "|---|---|---|---|---|---|---|---|"]
        for z in x["bins"]:
            L.append(f"| {z['bin']} (avg {z['lam']:.3f}) | {z['n_fit']:,} | {z['fac_fit']:.2f} | {z['fac']:.2f} | {z['n_test']:,} | "
                     f"{(z['pred_test'] or 0):.2%} | {(z['act_test'] or 0):.2%} | {z['count_test']} |")
        L += [f"knots: {x['knots']}", ""]
    return "\n".join(L) + "\n"
