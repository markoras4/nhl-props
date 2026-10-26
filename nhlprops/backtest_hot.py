"""Hot streaks: after a player scores in several games in a row, is his next-game chance lower (or higher) than the
model says?  Does it depend on how good he is?  (does not touch the website)

For every player-game we take the model's point-in-time chance (backtest.predict: only earlier games used) and the
player's run going into the game:
  * goal streak: consecutive games with a goal (0, 1, 2, 3+)
  * point streak: consecutive games with a point (0, 1-2, 3-4, 5+)
  * 2+ goals in his last game
split by level (the model's own goal / point expectation before the game).
If the model is right, "happened" matches "predicted" in every group. A gap of more than ~2 standard errors
(happened below predicted for average players on a run) would justify a penalty.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict

G_TIERS = [(-1, 0.16, "below average (goal chance < 15%)"), (0.16, 0.33, "average (15-28%)"), (0.33, 9, "top scorers (28%+)")]
P_TIERS = [(-1, 0.45, "below average (point chance < 36%)"), (0.45, 0.85, "average (36-57%)"), (0.85, 9, "top producers (57%+)")]


def streak_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["pid", "date", "gid"]).copy()
    gs, ps, last2 = [], [], []
    for _, m in df.groupby("pid", sort=False):
        g_run = p_run = 0
        prev_g = 0
        for g, a in zip(m.g.values, m.a.values):
            gs.append(g_run); ps.append(p_run); last2.append(prev_g >= 2)
            g_run = g_run + 1 if g >= 1 else 0
            p_run = p_run + 1 if g + a >= 1 else 0
            prev_g = g
    df["g_streak"], df["p_streak"], df["two_last"] = gs, ps, last2
    return df


def table(d, tiers, lam, y, bucket, labels):
    out = []
    for lo, hi, name in tiers:
        t = d[(lam >= lo) & (lam < hi)]
        tl = lam[(lam >= lo) & (lam < hi)]
        ty = y[(lam >= lo) & (lam < hi)]
        for key, lab in labels:
            m = bucket(t) == key
            if m.sum() < 30:
                continue
            p = (1 - np.exp(-tl[m])).values
            a = ty[m].values.astype(float)
            se = math.sqrt(max(p.mean() * (1 - p.mean()), 1e-9) / m.sum())
            out.append({"tier": name, "group": lab, "n": int(m.sum()), "pred": float(p.mean()), "actual": float(a.mean()),
                        "diff": float(a.mean() - p.mean()), "se": se, "t": float((a.mean() - p.mean()) / se)})
    return out


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for season in seasons:
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        if sk.empty:
            continue
        pri = prior_frames(src, season)
        df = predict(sk, pri, games, go, season, cfg)
        frames.append(streak_cols(df))
    d = pd.concat(frames)
    d = d[d.c_gp >= 5].copy()
    lam_g, lam_p = d.lam_g, d.lam_g + d.lam_a
    yg, yp = (d.g >= 1), (d.g + d.a >= 1)
    gb = lambda t: t.g_streak.clip(upper=3)
    pb = lambda t: pd.cut(t.p_streak, [-1, 0, 2, 4, 99], labels=[0, 1, 2, 3]).astype(int)
    rep = {"seasons": seasons, "rows": int(len(d)),
           "goal_streak": table(d, G_TIERS, lam_g, yg, gb, [(0, "no goal last game"), (1, "goal in 1 straight"), (2, "goals in 2 straight"), (3, "goals in 3+ straight")]),
           "two_goals_last": table(d, G_TIERS, lam_g, yg, lambda t: t.two_last.astype(int), [(0, "fewer than 2 goals last game"), (1, "2+ goals last game")]),
           "point_streak": table(d, P_TIERS, lam_p, yp, pb, [(0, "no point last game"), (1, "points in 1-2 straight"), (2, "points in 3-4 straight"), (3, "points in 5+ straight")])}
    (out_dir / "hot.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "hot.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    def tb(rows, what):
        L = [f"| Level | Going into the game | Player-games | Predicted {what} | Happened | Difference | t |", "|---|---|---|---|---|---|---|"]
        L += [f"| {x['tier']} | {x['group']} | {x['n']:,} | {x['pred']:.1%} | {x['actual']:.1%} | {x['diff'] * 100:+.1f} pts | {x['t']:+.1f} |" for x in rows]
        return L
    return "\n".join([f"# Hot streaks vs the model: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}",
                      f"Player-games (5+ games played this season): {r['rows']:,}", "", "## Goal streaks"] + tb(r["goal_streak"], "goal")
                     + ["", "## After 2+ goals in a game"] + tb(r["two_goals_last"], "goal")
                     + ["", "## Point streaks"] + tb(r["point_streak"], "point")) + "\n"
