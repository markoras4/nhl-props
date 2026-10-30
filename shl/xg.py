"""SHL expected goals from shot location and context (logistic regression, numpy only).

Unblocked attempts (on target, missed, goals); empty-net attempts are scored separately. Features: distance, angle,
behind the net, rebound, power play / shorthanded / open ice (3-on-3, 4-on-4). Penalty shots get a fixed rate.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PEN_SHOT_XG = 0.32
EN_XG = 0.75


def features(s: pd.DataFrame) -> np.ndarray:
    d = s.dist.clip(0.5, 60).values
    a = s.ang.clip(0, 180).values / 90.0
    pp = (s.sk_for > s.sk_ag).astype(float).values
    sh = (s.sk_for < s.sk_ag).astype(float).values
    open_ice = ((s.sk_for == s.sk_ag) & (s.sk_for < 5)).astype(float).values
    behind = (s.x < 0).astype(float).values
    reb = s.reb.astype(float).values
    return np.column_stack([np.ones(len(s)), np.log(d), d / 10, a, a * a, a * np.log(d), behind, reb, reb * a, pp, sh, open_ice])


NAMES = ["const", "log_dist", "dist/10", "angle", "angle^2", "angle*log_dist", "behind_net", "rebound", "rebound*angle", "power_play", "shorthanded", "open_ice"]


def fit(X, y, l2=1e-3, iters=50):
    w = np.zeros(X.shape[1])
    w[0] = np.log(y.mean() / (1 - y.mean()))
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w)))
        g = X.T @ (p - y) + l2 * w
        H = (X * (p * (1 - p))[:, None]).T @ X + l2 * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def model_rows(s: pd.DataFrame) -> pd.Series:
    return s.kind.isin(["on", "miss", "goal"]) & ~s.en & ~s.pen_shot


def predict(s: pd.DataFrame, w) -> np.ndarray:
    out = np.zeros(len(s))
    m = model_rows(s).values
    if m.any():
        out[m] = 1 / (1 + np.exp(-(features(s[m]) @ np.asarray(w))))
    out[s.pen_shot.values] = PEN_SHOT_XG
    out[(s.en & s.kind.isin(["on", "miss", "goal"])).values & ~s.pen_shot.values] = EN_XG
    return out


def evaluate(s, p):
    y = s.goal.astype(float).values
    ll = float(-np.mean(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))))
    base = y.mean()
    ll0 = float(-np.mean(y * np.log(base) + (1 - y) * np.log(1 - base)))
    bins = []
    for lo, hi in ((0, .02), (.02, .05), (.05, .1), (.1, .2), (.2, .3), (.3, 1)):
        m = (p >= lo) & (p < hi)
        if m.sum():
            bins.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": int(m.sum()), "pred": float(p[m].mean()), "act": float(y[m].mean())})
    return {"n": int(len(y)), "goals": int(y.sum()), "xg": float(p.sum()), "logloss": ll, "logloss_base": ll0, "skill": 1 - ll / ll0, "bins": bins}


def train(S: pd.DataFrame, test_season: int | None = None, log=print):
    """fit on seasons before test_season (or all when None); returns weights and a report"""
    m = model_rows(S)
    tr = S[m & (S.season < test_season)] if test_season else S[m & (S.season <= S.season.max() - 1)]
    w = fit(features(tr), tr.goal.astype(float).values)
    rep = {"weights": dict(zip(NAMES, map(float, w))), "train_seasons": sorted(map(int, tr.season.unique()))}
    if test_season:
        te = S[m & (S.season == test_season)]
        rep["test"] = evaluate(te, predict(te, w))
    return w, rep
