"""Shots-on-goal props: do shot attempts predict better than past shots on goal? (does not touch the website)

For every player-game, using only information from before that game:
  A  shots-on-goal rate per 60 (this season, shrunk toward last season, shrunk toward the position average)
  B  attempt rate per 60 (same shrinking) x his share of attempts that hit the net (shrunk toward last season)
  C  the average of A and B
Each rate x projected ice time (his recent games) -> Poisson probabilities for over 1.5 / 2.5 / 3.5 shots.
Attempts come from the NHL play-by-play (goals, saved shots, missed and blocked attempts).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .grade import summarize
from .model import _hours, _num, _sit
from .shots import collect

LINES = [1.5, 2.5, 3.5]
KS = [3.0, 6.0, 10.0]


def shrink(x, n, prior, k):
    return (x + k * prior) / (n + k)


def p_over(lam, line):
    k_max = int(math.floor(line))
    term, cdf = math.exp(-lam), 0.0
    for i in range(k_max + 1):
        if i:
            term *= lam / i
        cdf += term
    return max(0.0, 1 - cdf)


def priors(src, season):
    d = src.moneypuck("skaters", season - 1, False)
    if d is None:
        return {}, {}
    d = d[_sit(d) == "all"]
    df = pd.DataFrame({"pid": pd.to_numeric(d.playerId, errors="coerce"),
                       "posg": np.where(d.get("position", pd.Series("F", index=d.index)).astype(str) == "D", "D", "F"),
                       "h": _hours(d, "icetime"), "s": _num(d, "I_F_shotsOnGoal"), "att": _num(d, "I_F_shotAttempts"),
                       "gp": _num(d, "games_played")}).dropna(subset=["pid"])
    df = df.groupby(["pid", "posg"], as_index=False).sum()
    pos = {}
    for g, m in df.groupby("posg"):
        pos[g] = {"s60": m.s.sum() / m.h.sum(), "a60": m.att.sum() / m.h.sum(), "on": m.s.sum() / m.att.sum(),
                  "toi": m.h.sum() * 60 / m.gp.sum()}
    per = {int(r.pid): r for r in df.itertuples()}
    return per, pos


def season_rows(src, season, cfg, log=print):
    games, sk, _ = season_boxscores(src, season, cfg["workers"], log)
    lg = games.assign(season=season, final=True)
    shots = collect(src, lg, season, Path("data/cache/shots_bt"), cfg["workers"], log)
    att = {}
    for gid, g in shots.items():
        for pid, x, y, k, team, *_ in g["s"]:
            att[(int(gid), pid)] = att.get((int(gid), pid), 0) + 1
    per, pos = priors(src, season)
    if not pos:
        raise RuntimeError("no MoneyPuck priors")
    cum: dict[int, list] = {}           # pid -> [sog, att, toi_min, gp]
    tois: dict[int, list] = {}
    rows = []
    for date, day in sk.sort_values("date").groupby("date", sort=True):
        for r in day.itertuples():
            pg = pos.get(r.posg, pos["F"])
            pr = per.get(r.pid)
            if pr is not None and pr.h > 0:
                ps60 = shrink(pr.s, pr.h, pg["s60"], 8.0)
                pa60 = shrink(pr.att, pr.h, pg["a60"], 8.0)
                pon = shrink(pr.s, pr.att, pg["on"], 150.0)
                ptoi = pr.h * 60 / pr.gp if pr.gp else pg["toi"]
            else:
                ps60, pa60, pon, ptoi = pg["s60"], pg["a60"], pg["on"], pg["toi"]
            c = cum.get(r.pid, [0, 0, 0.0, 0])
            t = tois.get(r.pid, [])
            toi = (0.6 * float(np.mean(t[-5:])) + 0.4 * float(np.mean(t))) if t else ptoi
            hrs = c[2] / 60
            out = {"season": season, "pid": r.pid, "gp": c[3], "sog": int(r.s), "toi": toi}
            for k in KS:
                rs = shrink(c[0], hrs, ps60, k)
                ra = shrink(c[1], hrs, pa60, k) * shrink(c[0], c[1], pon, 150.0)
                out[f"A{int(k)}"] = rs * toi / 60
                out[f"B{int(k)}"] = ra * toi / 60
            rows.append(out)
        for r in day.itertuples():
            c = cum.setdefault(r.pid, [0, 0, 0.0, 0])
            c[0] += int(r.s); c[1] += att.get((int(r.gid), r.pid), 0); c[2] += float(r.toi); c[3] += 1
            if r.toi > 0:
                tois.setdefault(r.pid, []).append(float(r.toi))
    return pd.DataFrame(rows)


def evaluate(d: pd.DataFrame) -> dict:
    d = d[d.gp >= 5].copy()
    y = d.sog.values
    rep = {"rows": int(len(d)), "mean_actual": float(y.mean())}
    res = {}
    for name in [f"{m}{int(k)}" for m in "AB" for k in KS] + [f"C{int(k)}" for k in KS]:
        lam = d[name].values if name[0] != "C" else 0.5 * (d["A" + name[1:]].values + d["B" + name[1:]].values)
        scale = y.mean() / lam.mean()           # same calibration for every method, so only the ranking signal counts
        lam = lam * scale
        r = {"mean_pred_raw": float(lam.mean() / scale), "corr": float(np.corrcoef(lam, y)[0, 1]), "lines": {}}
        for L in LINES:
            yy = (y > L).astype(int)
            p = np.clip([p_over(x, L) for x in lam], 1e-4, 1 - 1e-4)
            r["lines"][str(L)] = summarize(list(zip(p, yy)))
        res[name] = r
    rep["methods"] = res
    return rep


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = pd.concat([season_rows(src, y, cfg, log) for y in sorted(seasons)])
    rep = {"seasons": seasons, **evaluate(d)}
    (out_dir / "attempts.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "attempts.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# Shots on goal: attempts vs shots ({', '.join(f'{s}-{s + 1}' for s in r['seasons'])})",
         f"Player-games (5+ games played this season): {r['rows']:,} · actual average {r['mean_actual']:.3f}", "",
         "A = shots-on-goal rate · B = attempts x on-net share · C = average · number = regression strength (hours)", "",
         "| Method | Raw mean | Corr | Brier o1.5 | Skill | Brier o2.5 | Skill | Brier o3.5 | Skill |", "|---|---|---|---|---|---|---|---|---|"]
    for name, x in r["methods"].items():
        cells = " | ".join(f"{x['lines'][str(l)]['brier']:.4f} | {x['lines'][str(l)]['skill'] * 100:+.2f}%" for l in LINES)
        L.append(f"| {name} | {x['mean_pred_raw']:.3f} | {x['corr']:.4f} | {cells} |")
    return "\n".join(L) + "\n"
