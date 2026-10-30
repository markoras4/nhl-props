"""Do we underrate high-volume shooters on shots on goal? (does not touch the website)

Shots model as on the site (usage weights: this season's shots x2, recent-form factor). For every player-game, info
strictly before the game. Then a Poisson regression fitted on the older seasons, judged on the newer ones:
    actual shots ~ exp(b0 + b1 * log(model) + b2 * log(opponent shots allowed vs league) + b3 * back-to-back)
b1 = 1: the model's spread is right; b1 > 1: it squeezes players together (big shooters too low, small too high).
b2, b3 show what the opponent and back-to-backs are really worth (the site uses its own factors for both).
Also: calibration by predicted volume, and whether shot counts swing more than a Poisson (negative binomial r).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .backtest import p_over
from .backtest_usage import frames, parts, lams
from .grade import summarize

LINES = [1.5, 2.5, 3.5, 4.5]


def nb_over(lam, L, r):
    lam = np.asarray(lam, float); k = int(math.floor(L))
    p = r / (r + lam); pm = p ** r; cdf = pm.copy()
    for i in range(1, k + 1):
        pm = pm * (r + i - 1) / i * (1 - p); cdf += pm
    return 1 - cdf


def pois_glm(X, y, iters=40):
    w = np.zeros(X.shape[1]); w[0] = math.log(max(y.mean(), 1e-6))
    for _ in range(iters):
        mu = np.exp(X @ w)
        H = (X * mu[:, None]).T @ X + 1e-6 * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - mu)); w += step
        if np.abs(step).max() < 1e-9:
            break
    return w


def design(d, lam):
    return np.column_stack([np.ones(len(d)), np.log(np.clip(lam, 0.05, None)), np.log(np.clip(d.opp_sa_r.values, 0.5, 1.6)), d.b2b.values])


def scores(lam, y, r=None):
    out = {}
    for L in LINES:
        p = nb_over(lam, L, r) if r else np.array([p_over(x, L) for x in lam])
        out[str(L)] = summarize(list(zip(np.clip(p, 1e-4, 1 - 1e-4), (y > L).astype(float))))
    return out


def tiers(lam, y, d, r=None):
    rows = []
    q = np.quantile(lam, [0, .2, .4, .6, .8, .9, .97, 1])
    for lo, hi in zip(q[:-1], q[1:]):
        m = (lam >= lo) & (lam <= hi)
        if m.sum() < 200:
            continue
        rr = {"band": f"{lo:.2f}-{hi:.2f}", "n": int(m.sum()), "pred": float(lam[m].mean()), "act": float(y[m].mean())}
        for L in (2.5, 3.5):
            p = nb_over(lam[m], L, r) if r else np.array([p_over(x, L) for x in lam[m]])
            rr[f"o{L}"] = [float(p.mean()), float((y[m] > L).mean())]
        # season-to-date volume: does his own average say more than we give him?
        h = d.c_s.values[m] / np.maximum(1, d.c_gp.values[m])
        rr["season_avg"] = float(h.mean())
        rows.append(rr)
    return rows


def fit_r(lam, y):
    best = None
    for r in (4, 6, 8, 10, 15, 20, 30, 50, 100, 1000):
        p = r / (r + lam)
        yy = y.astype(int)
        lg = np.array([math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) for k in range(int(yy.max()) + 1)])
        ll = float((lg[yy] + r * np.log(p) + yy * np.log(1 - p)).sum())
        if best is None or ll > best[1]:
            best = (r, ll)
    return best[0]


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, log)
    d = d[(d.c_gp >= 5) & (d.lam_s > 0)].reset_index(drop=True)
    pt = parts(d, cfg)
    um = cfg.get("usage_mult") or {}
    _, lam = lams(d, pt, cfg, um.get("s_sog", 2.0), um.get("xg_goal", 4.0))
    lam = np.asarray(lam, float)
    y = d.s.values.astype(float)
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fm = d.season.isin(half).values
    tm = ~fm
    # A: the model with one overall scale (fit seasons) = what the site does with its shots calibration
    c = y[fm].sum() / lam[fm].sum()
    lamA = lam * c
    # B: regression on log(model), opponent, back-to-back
    w = pois_glm(design(d[fm], lam[fm]), y[fm])
    lamB = np.exp(design(d, lam) @ w)
    # C: the site's own opponent/b2b-style factors aren't in the backtest model; B adds them; also test spread alone
    w1 = pois_glm(design(d[fm], lam[fm])[:, :2], y[fm])
    lamC = np.exp(design(d, lam)[:, :2] @ w1)
    rB = fit_r(lamB[fm], y[fm])
    rep = {"seasons": seasons, "fit_seasons": half, "rows": int(len(d)), "rows_test": int(tm.sum()),
           "scale_A": float(c), "glm": {"b0": float(w[0]), "b1_spread": float(w[1]), "b2_opp": float(w[2]), "b3_b2b": float(w[3]),
                                         "b2b_mult": float(math.exp(w[3]))},
           "glm_spread_only": {"b0": float(w1[0]), "b1_spread": float(w1[1])}, "nb_r": rB,
           "test_A": scores(lamA[tm], y[tm]), "test_C": scores(lamC[tm], y[tm]), "test_B": scores(lamB[tm], y[tm]),
           "test_B_nb": scores(lamB[tm], y[tm], rB),
           "tiers_A": tiers(lamA[tm], y[tm], d[tm]), "tiers_B": tiers(lamB[tm], y[tm], d[tm]), "tiers_B_nb": tiers(lamB[tm], y[tm], d[tm], rB)}
    # b2b and opponent, raw: actual / model by group (model A)
    grp = []
    for lab, m in (("back-to-back", d.b2b.values == 1), ("rested", d.b2b.values == 0),
                   ("opp allows few shots (<0.94)", d.opp_sa_r.values < 0.94), ("opp average", (d.opp_sa_r.values >= 0.94) & (d.opp_sa_r.values <= 1.06)),
                   ("opp allows many (>1.06)", d.opp_sa_r.values > 1.06)):
        m = m & tm
        if m.sum() > 200:
            grp.append({"group": lab, "n": int(m.sum()), "pred": float(lamA[m].mean()), "act": float(y[m].mean()), "ratio": float(y[m].mean() / lamA[m].mean())})
    rep["groups_A"] = grp
    (out_dir / "shooters.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "shooters.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    g = r["glm"]
    L = [f"# Shots on goal by shooter volume: fit on {r['fit_seasons']}, tested on the rest of {r['seasons']} ({r['rows_test']:,} test player-games)", "",
         f"A = model x one overall scale ({r['scale_A']:.3f}). C = spread fitted: shots ~ model^{r['glm_spread_only']['b1_spread']:.3f}. "
         f"B = spread {g['b1_spread']:.3f}, opponent shots allowed ^{g['b2_opp']:.3f}, back-to-back x{g['b2b_mult']:.3f}. NB dispersion r = {r['nb_r']}.", "",
         "b1 > 1 means the model squeezes shooters together (big shooters too low).", "",
         "## Accuracy (Brier skill, test seasons)", "| Line | A | C | B | B + NB |", "|---|---|---|---|---|"]
    for k in r["test_A"]:
        L.append(f"| over {k} | {r['test_A'][k]['skill']*100:+.2f}% | {r['test_C'][k]['skill']*100:+.2f}% | {r['test_B'][k]['skill']*100:+.2f}% | {r['test_B_nb'][k]['skill']*100:+.2f}% |")
    def tb(rows, name):
        o = [f"## By predicted shots: {name}", "| Predicted | n | Pred shots | Actual | Season avg so far | o2.5 pred | act | o3.5 pred | act |", "|---|---|---|---|---|---|---|---|---|"]
        for x in rows:
            o.append(f"| {x['band']} | {x['n']:,} | {x['pred']:.2f} | {x['act']:.2f} | {x['season_avg']:.2f} | {x['o2.5'][0]:.1%} | {x['o2.5'][1]:.1%} | {x['o3.5'][0]:.1%} | {x['o3.5'][1]:.1%} |")
        return o
    L += [""] + tb(r["tiers_A"], "A (overall scale only)") + [""] + tb(r["tiers_B"], "B (spread + opponent + b2b)") + [""] + tb(r["tiers_B_nb"], "B with negative binomial")
    L += ["", "## Back-to-backs and opponents (model A)", "| Group | n | Pred | Actual | Actual / pred |", "|---|---|---|---|---|"]
    for x in r["groups_A"]:
        L.append(f"| {x['group']} | {x['n']:,} | {x['pred']:.2f} | {x['act']:.2f} | {x['ratio']:.3f} |")
    return "\n".join(L) + "\n"
