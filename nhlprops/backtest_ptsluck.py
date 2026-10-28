"""Team luck and future team results: does a team's points luck predict how it does next, beyond the model?
(does not touch the website)

Before every game (both teams 10+ games into the season), each team's season so far is split into:
  total   = points - expected points from its chances            (what the Futures "Pts vs exp." column shows)
  goals   = points its actual goals deserved - expected points    (finishing and goaltending luck)
  close   = points - points its actual goals deserved             (close games and overtimes)
Chances come from shots on goal x the league's shooting rate (same as the season backtest; per-game xG isn't
in the box scores). The model's win chance is the season simulation's (half goals, half chances, shrunk to last season).
Tests: (1) does each kind of luck persist from the first half of a season to the second?
       (2) adding each one to the model's win chance, fitted on older seasons: does it improve later seasons?
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .backtest_season import state_at
from .backtest_totals import team_prior
from .season import outcome_probs


def _xp(n, f, a, tb):
    pH, pT, pA = outcome_probs(max(f, 0.1), max(a, 0.1), tb)
    return n * (2 * pH + 1.5 * pT)


def season_rows(games: pd.DataFrame, prior: dict, prev_lg: float, cfg: dict) -> tuple[list, list]:
    ha, tb = cfg.get("home_adv", 1.05), cfg.get("tie_boost", 1.0)
    rows, halves = [], []
    acc: dict[str, dict] = {}
    for d, day in games.groupby("date", sort=True):
        rat, _, lg = state_at(games, d, prior, prev_lg, prior_r=0.7, k=20.0, w=0.5)
        past = games[games.date < d]
        ts, tg = float(past.hsog.fillna(0).sum() + past.asog.fillna(0).sum()), float(past.hs.sum() + past.as_.sum())
        lg_sh = tg / ts if ts else 0.095
        luck = {}
        for t, a in acc.items():
            n = a["gp"]
            if n < 10:
                continue
            xp = _xp(n, a["sf"] * lg_sh / n, a["sa"] * lg_sh / n, tb)
            gp_ = _xp(n, a["gf"] / n, a["ga"] / n, tb)
            luck[t] = {"total": (a["pts"] - xp) / n, "goals": (gp_ - xp) / n, "close": (a["pts"] - gp_) / n}
        lgm = float(np.mean([v[0] for v in rat.values()])) if rat else 3.0
        for r in day.itertuples():
            if r.home in luck and r.away in luck and r.home in rat and r.away in rat:
                mh = rat[r.home][0] * rat[r.away][1] / lgm * ha
                ma = rat[r.away][0] * rat[r.home][1] / lgm / ha
                pH, pT, pA = outcome_probs(mh, ma, tb)
                rows.append({"date": d, "p": pH + 0.5 * pT, "y": float(r.hs > r.as_),
                             **{f"d_{k}": luck[r.home][k] - luck[r.away][k] for k in ("total", "goals", "close")}})
        for r in day.itertuples():
            for t, f, a_, s1, s2, win in ((r.home, r.hs, r.as_, r.hsog, r.asog, r.hs > r.as_), (r.away, r.as_, r.hs, r.asog, r.hsog, r.as_ > r.hs)):
                x = acc.setdefault(t, {"gp": 0, "gf": 0, "ga": 0, "sf": 0.0, "sa": 0.0, "pts": 0, "log": []})
                x["gp"] += 1; x["gf"] += f; x["ga"] += a_; x["sf"] += float(s1 or 0); x["sa"] += float(s2 or 0)
                p = 2 if win else (1 if r.last in ("OT", "SO") else 0)
                x["pts"] += p
                x["log"].append((f, a_, float(s1 or 0), float(s2 or 0), p))
    # first half vs second half of each team's season
    ts, tg = float(games.hsog.fillna(0).sum() + games.asog.fillna(0).sum()), float(games.hs.sum() + games.as_.sum())
    lg_sh = tg / ts if ts else 0.095
    for t, a in acc.items():
        L = a["log"]
        if len(L) < 60:
            continue
        h = len(L) // 2
        out = {}
        for name, part in (("first", L[:h]), ("second", L[h:])):
            n = len(part)
            gf, ga, sf, sa, pts = (sum(x[i] for x in part) for i in range(5))
            xp = _xp(n, sf * lg_sh / n, sa * lg_sh / n, tb)
            gp_ = _xp(n, gf / n, ga / n, tb)
            out[name] = {"total": (pts - xp) / n, "goals": (gp_ - xp) / n, "close": (pts - gp_) / n,
                         "ppg": pts / n, "xppg": xp / n}
        halves.append(out)
    return rows, halves


def _fit(p, y, x, iters=40):
    """logit(p_adj) = logit(p) + b x, maximum likelihood for b."""
    z0 = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1))
    b = 0.0
    for _ in range(iters):
        q = 1 / (1 + np.exp(-(z0 + b * x)))
        g, h = float(((y - q) * x).sum()), float((q * (1 - q) * x * x).sum())
        if h <= 0:
            break
        step = g / h
        b += step
        if abs(step) < 1e-9:
            break
    q = 1 / (1 + np.exp(-(z0 + b * x)))
    return b, 1 / math.sqrt(max(float((q * (1 - q) * x * x).sum()), 1e-12))


def _score(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"brier": float(((p - y) ** 2).mean()), "logloss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())}


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    allrows, allhalves = [], []
    for season in sorted(seasons):
        games, _, _ = season_boxscores(src, season, cfg["workers"], log)
        games = games.copy()
        games["date"] = games.date.astype(str).str[:10]
        tp = team_prior(src, season)
        prior = {t: (v["xgf"], v["xga"]) for t, v in tp.items()}
        prev_lg = float(np.mean([v[0] for v in prior.values()])) if prior else 3.05
        rows, halves = season_rows(games, prior, prev_lg, cfg)
        for r in rows:
            r["season"] = season
        allrows += rows; allhalves += halves
        log(f"  {season}: {len(rows)} games scored, {len(halves)} team-seasons for the half-season check")
    d = pd.DataFrame(allrows)
    # 1. persistence
    pers = {}
    for k in ("total", "goals", "close"):
        a = np.array([h["first"][k] for h in allhalves]); b = np.array([h["second"][k] for h in allhalves])
        pers[k] = float(np.corrcoef(a, b)[0, 1])
    # does first-half luck predict second-half points beyond first-half chances?
    X = np.array([[1, h["first"]["xppg"], h["first"]["total"]] for h in allhalves]); Y = np.array([h["second"]["ppg"] for h in allhalves])
    coef = np.linalg.lstsq(X, Y, rcond=None)[0]
    X2 = np.array([[1, h["first"]["xppg"], h["first"]["goals"], h["first"]["close"]] for h in allhalves])
    coef2 = np.linalg.lstsq(X2, Y, rcond=None)[0]
    # 2. game level: fit on older seasons, judge on the newest
    test_s = max(seasons)
    fit, test = d[d.season != test_s], d[d.season == test_s]
    res = {"base_fit": _score(fit.p.values, fit.y.values), "base_test": _score(test.p.values, test.y.values), "terms": {}}
    for k in ("total", "goals", "close"):
        b, se = _fit(fit.p.values, fit.y.values, fit[f"d_{k}"].values)
        z0 = np.log(np.clip(test.p.values, 1e-6, 1 - 1e-6) / np.clip(1 - test.p.values, 1e-6, 1))
        q = 1 / (1 + np.exp(-(z0 + b * test[f"d_{k}"].values)))
        bt, set_ = _fit(d.p.values, d.y.values, d[f"d_{k}"].values)
        res["terms"][k] = {"beta_fit": b, "se_fit": se, "beta_all": bt, "se_all": set_, "test": _score(q, test.y.values),
                           "sd": float(d[f"d_{k}"].std())}
    rep = {"seasons": seasons, "games": int(len(d)), "team_seasons": len(allhalves), "persistence": pers,
           "second_half_fit": {"const": coef[0], "xppg": coef[1], "luck_total": coef[2]},
           "second_half_fit_split": {"const": coef2[0], "xppg": coef2[1], "luck_goals": coef2[2], "luck_close": coef2[3]},
           "game_level": res}
    (out_dir / "ptsluck.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    L = [f"# Team points luck and future results: {seasons} ({rep['games']:,} games, {rep['team_seasons']} team-seasons)", "",
         "## 1. Does luck persist? (first half of a season vs second half, per team)", "",
         "| Kind of luck (points per game vs chances) | r first→second half |", "|---|---|",
         f"| Total (points vs expected points) | {pers['total']:+.3f} |", f"| Goals (finishing + goaltending) | {pers['goals']:+.3f} |",
         f"| Close games / overtime | {pers['close']:+.3f} |", "",
         f"Second-half points per game = {coef[0]:.2f} + {coef[1]:.2f} × first-half expected points/game + {coef[2]:+.2f} × first-half total luck/game",
         f"Split: {coef2[0]:.2f} + {coef2[1]:.2f} × expected + {coef2[2]:+.2f} × goals luck + {coef2[3]:+.2f} × close-game luck", "",
         "(a luck coefficient near 0 = the luck doesn't carry into the second half; near 1 = it's real and fully carries)", "",
         f"## 2. Game level: does luck add to the model's win chance? (fitted on older seasons, judged on {test_s})", "",
         f"Model alone on {test_s}: Brier {res['base_test']['brier']:.5f}, log loss {res['base_test']['logloss']:.5f}", "",
         "| Luck term (home minus away, per game) | beta fitted (se) | beta all seasons (se) | Brier on test | log loss on test |", "|---|---|---|---|---|"]
    for k, v in res["terms"].items():
        L.append(f"| {k} | {v['beta_fit']:+.2f} ({v['se_fit']:.2f}) | {v['beta_all']:+.2f} ({v['se_all']:.2f}) | {v['test']['brier']:.5f} | {v['test']['logloss']:.5f} |")
    L += ["", "(negative beta = teams running lucky do worse than the model expects next = the model doesn't regress them enough)"]
    md = "\n".join(L) + "\n"
    (out_dir / "ptsluck.md").write_text(md, encoding="utf-8")
    log(md)
    return rep
