"""Depth players and rookies: are their chances too high, and does a role-based prior fix it? (does not touch the website)

Players with little history are pulled toward a prior. Today that prior is the average player at his position.
Alternative: the prior scales with his projected ice time, prior x (TOI / position average TOI) ^ beta, so a 12-minute
fourth-liner is pulled toward what 12-minute players produce. beta = 0 is the current model.
1) Calibration by NHL experience and by ice-time tier (current model).
2) beta fitted on the older seasons, judged on the newer ones, then the same calibration tables again.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict, p_over
from .grade import summarize

CONFIGS = [(1.0, 0.0, 0.0), (1.0, 0.0, 0.1), (1.0, 0.0, 0.2), (1.0, 0.0, 0.3), (1.0, 0.0, 0.45)]   # (prior_beta, role_gamma, toi_delta); live = (1, 0, 0)
LIVE = (1.0, 0.0, 0.0)
EXP = [(-1, 20, "0-20 NHL games"), (20, 82, "21-82"), (82, 250, "83-250"), (250, 99999, "250+")]
TOI_F = [(0, 12, "under 12 min"), (12, 14, "12-14"), (14, 16, "14-16"), (16, 18, "16-18"), (18, 99, "18+")]


def frames(src, seasons, cfg, conf, log, cache):
    out = []
    for season in sorted(seasons):
        if season not in cache:
            cache[season] = (season_boxscores(src, season, cfg["workers"], log), prior_frames(src, season))
        (games, sk, go), pri = cache[season]
        if sk.empty:
            continue
        df = predict(sk, pri, games, go, season, cfg, prior_beta=conf[0], role_gamma=conf[1], toi_delta=conf[2])
        P = pri["sk"]
        prev_gp = P.groupby("pid").gp.sum() if len(P) else pd.Series(dtype=float)
        df["exp_gp"] = df.pid.map(prev_gp).fillna(0) + df.c_gp
        df["season"] = season
        out.append(df[["season", "pid", "posg", "g", "a", "s", "lam_g", "lam_a", "lam_s", "toi_proj", "c_gp", "exp_gp"]])
    return pd.concat(out, ignore_index=True)


def calib(d, mask, label):
    sub = d[mask]
    if len(sub) < 300:
        return None
    row = {"group": label, "n": int(len(sub))}
    for m, p, y in (("goal", 1 - np.exp(-sub.lam_g), sub.g >= 1), ("point", 1 - np.exp(-(sub.lam_g + sub.lam_a)), sub.g + sub.a >= 1),
                    ("sog2", p_over(sub.lam_s, 1.5), sub.s >= 2)):
        p, y = np.asarray(p, float), np.asarray(y, float)
        se = np.sqrt((y.var() + 1e-9) / len(y))
        row[m] = {"pred": float(p.mean()), "act": float(y.mean()), "t": float((y.mean() - p.mean()) / se)}
    return row


def tables(d):
    F = d[d.posg == "F"]
    return {"exp": [r for r in (calib(d, (d.exp_gp > lo) & (d.exp_gp <= hi), lab) for lo, hi, lab in EXP) if r],
            "toi_f": [r for r in (calib(F, (F.toi_proj > lo) & (F.toi_proj <= hi), lab) for lo, hi, lab in TOI_F) if r],
            "rookie_depth": [r for r in (calib(F, (F.exp_gp <= 82) & (F.toi_proj <= 14), "F, <=82 games, <=14 min"),
                                         calib(F, (F.exp_gp <= 82) & (F.toi_proj > 14), "F, <=82 games, >14 min"),
                                         calib(F, (F.exp_gp > 82) & (F.toi_proj <= 14), "F, 83+ games, <=14 min")) if r]}


def scores(d):
    y = {"Goal": (d.g >= 1), "Point": (d.g + d.a >= 1), "SOG 2+": (d.s >= 2), "SOG 3+": (d.s >= 3)}
    p = {"Goal": 1 - np.exp(-d.lam_g), "Point": 1 - np.exp(-(d.lam_g + d.lam_a)), "SOG 2+": p_over(d.lam_s, 1.5), "SOG 3+": p_over(d.lam_s, 2.5)}
    return {m: summarize(list(zip(np.clip(np.asarray(p[m], float), 1e-4, 1 - 1e-4), np.asarray(y[m], float)))) for m in y}


def brier(d):
    pg, pp, ps = 1 - np.exp(-d.lam_g), 1 - np.exp(-(d.lam_g + d.lam_a)), p_over(d.lam_s, 1.5)
    return float((((pg - (d.g >= 1)) ** 2).mean() + ((pp - (d.g + d.a >= 1)) ** 2).mean() + ((np.asarray(ps) - (d.s >= 2)) ** 2).mean()) / 3)


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    runs = {}
    for c in CONFIGS:
        d = frames(src, seasons, cfg, c, log, cache)
        runs[c] = d[d.c_gp >= 1].copy()        # from his 2nd game of the season (rookies included)
    grid = [{"beta": c[0], "gamma": c[1], "delta": c[2], "brier_fit": brier(runs[c][runs[c].season.isin(half)])} for c in CONFIGS]
    bg = min(grid, key=lambda r: r["brier_fit"]); best = (bg["beta"], bg["gamma"], bg["delta"])
    now, new = runs[LIVE], runs[best]
    tn, tb = now[~now.season.isin(half)], new[~new.season.isin(half)]
    lowF = lambda d: d[(d.posg == "F") & (d.exp_gp <= 82)]
    rep = {"seasons": seasons, "fit_seasons": half, "grid": grid, "best_beta": best,
           "tables_now": tables(tn), "tables_new": tables(tb),
           "test_now": scores(tn), "test_new": scores(tb),
           "test_now_lowexpF": scores(lowF(tn)), "test_new_lowexpF": scores(lowF(tb))}
    (out_dir / "depth.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "depth.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# Depth players & rookies: fit on {r['fit_seasons']}, tested on the rest of {r['seasons']}", "",
         "Grid (fit seasons, mean Brier of goal / point / SOG 2+; beta = role-based prior, gamma = role change vs his history): " +
         " · ".join(f"delta {g['delta']}: {g['brier_fit']:.6f}" for g in r["grid"]) + f" → best {r['best_beta']}. 'Now' below = live model (beta 1, gamma 0).", ""]
    def tb(rows):
        out = ["| Group | n | Goal pred | Goal actual | t | Point pred | Point actual | t | SOG2+ pred | SOG2+ actual | t |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in rows:
            out.append(f"| {x['group']} | {x['n']:,} | " + " | ".join(f"{x[m]['pred']:.1%} | {x[m]['act']:.1%} | {x[m]['t']:+.1f}" for m in ("goal", "point", "sog2")) + " |")
        return out
    for key, name in (("exp", "By NHL experience (all skaters)"), ("toi_f", "Forwards by projected ice time"), ("rookie_depth", "Young / depth forwards")):
        L += [f"## {name}: model now (test seasons)"] + tb(r["tables_now"][key]) + ["", f"## {name}: best setting {r['best_beta']}"] + tb(r["tables_new"][key]) + [""]
    L += ["## Accuracy, test seasons (Brier, skill)", "| Market | All now | All new | Forwards <=82 games now | new |", "|---|---|---|---|---|"]
    for m in r["test_now"]:
        a, b, c, d = r["test_now"][m], r["test_new"][m], r["test_now_lowexpF"][m], r["test_new_lowexpF"][m]
        L.append(f"| {m} | {a['brier']:.5f} ({a['skill']*100:+.2f}%) | {b['brier']:.5f} ({b['skill']*100:+.2f}%) | {c['brier']:.5f} ({c['skill']*100:+.2f}%) | {d['brier']:.5f} ({d['skill']*100:+.2f}%) |")
    return "\n".join(L) + "\n"
