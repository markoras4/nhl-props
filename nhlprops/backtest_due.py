"""'Due' players: do players with fewer goals than their chances this season score more than the model says?
(does not touch the website)

Site-like goal model (usage weights, recent-form factor). For every player-game with 8+ games played this season,
luck so far = (goals - expected goals) / sqrt(expected goals) from his in-season shots. Calibration of the goal
chance by luck group, with and without the recent-form factor, and a fitted correction (older seasons -> newer ones).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import backtest_usage as bu
from .grade import summarize

GROUPS = [(-9, -1.5, "very unlucky (z <= -1.5)"), (-1.5, -0.75, "unlucky (-1.5 to -0.75)"), (-0.75, 0.75, "normal"),
          (0.75, 1.5, "lucky (0.75 to 1.5)"), (1.5, 9, "very lucky (z >= 1.5)")]


def table(z, p, y, m):
    out = []
    for lo, hi, lab in GROUPS:
        mm = m & (z > lo) & (z <= hi)
        if mm.sum() < 300:
            continue
        se = np.sqrt(y[mm].var() / mm.sum())
        out.append({"group": lab, "n": int(mm.sum()), "pred": float(p[mm].mean()), "act": float(y[mm].mean()),
                    "ratio": float(y[mm].mean() / p[mm].mean()), "t": float((y[mm].mean() - p[mm].mean()) / se)})
    return out


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = bu.frames(src, seasons, cfg, log)
    d = d[(d.c_gp >= 8) & (d.lam_g > 0)].reset_index(drop=True)
    pt = bu.parts(d, cfg)
    um = cfg.get("usage_mult") or {}
    lg_form, _ = bu.lams(d, pt, cfg, um.get("s_sog", 2.0), um.get("xg_goal", 4.0))
    keep = bu.FORM; bu.FORM = None
    lg_noform, _ = bu.lams(d, pt, cfg, um.get("s_sog", 2.0), um.get("xg_goal", 4.0))
    bu.FORM = keep
    z = ((d.c_g - d.c_xg) / np.sqrt(np.maximum(d.c_xg, 0.5))).values
    y = (d.g >= 1).values.astype(float)
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fm = d.season.isin(half).values; tm = ~fm
    rep = {"seasons": seasons, "fit_seasons": half, "rows_test": int(tm.sum())}
    for name, lam in (("with_form", np.asarray(lg_form, float)), ("no_form", np.asarray(lg_noform, float))):
        p = 1 - np.exp(-lam)
        rep[name] = {"test": table(z, p, y, tm), "fit": table(z, p, y, fm)}
        # correction: goal rate x (1 + k * clip(-z, 0, 2)) for unlucky players, k fitted on the fit seasons
        best = None
        for k in np.arange(0, 0.21, 0.01):
            pc = 1 - np.exp(-lam * (1 + k * np.clip(-z, 0, 2)))
            b = float(((pc[fm] - y[fm]) ** 2).mean())
            if best is None or b < best[1]:
                best = (float(k), b)
        k = best[0]
        pc = 1 - np.exp(-lam * (1 + k * np.clip(-z, 0, 2)))
        rep[name]["k"] = k
        rep[name]["skill_test"] = [summarize(list(zip(np.clip(p[tm], 1e-4, 1 - 1e-4), y[tm])))["skill"],
                                   summarize(list(zip(np.clip(pc[tm], 1e-4, 1 - 1e-4), y[tm])))["skill"]]
        rep[name]["test_corrected"] = table(z, pc, y, tm)
    (out_dir / "due.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "due.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# 'Due' players: fit on {r['fit_seasons']}, tested on the rest of {r['seasons']} ({r['rows_test']:,} test player-games, 8+ GP)", ""]
    for name in ("with_form", "no_form"):
        x = r[name]
        L += [f"## Goal model {name.replace('_', ' ')} · correction k = {x['k']:.2f} · skill {x['skill_test'][0]*100:+.2f}% -> {x['skill_test'][1]*100:+.2f}%",
              "| Luck so far | n | Model | Actual | Actual / model | t | Corrected model |", "|---|---|---|---|---|---|---|"]
        for a, c in zip(x["test"], x["test_corrected"]):
            L.append(f"| {a['group']} | {a['n']:,} | {a['pred']:.1%} | {a['act']:.1%} | {a['ratio']:.3f} | {a['t']:+.1f} | {c['pred']:.1%} |")
        L.append("")
    return "\n".join(L) + "\n"
