"""Team luck and player props: do players on teams running hot (goals well above their chances so far) score less than
the model expects afterwards, beyond each player's own pull toward his real level? And the reverse for cold teams.
(does not touch the website)

Team luck before each game = (team goals - team shots x league goals per shot) per game this season so far.
Player-games are grouped by their team's luck; for each group: model goals / points vs what happened. Then a small
adjustment lambda x exp(beta x luck) is fitted on the older seasons and judged on the newer ones.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict
from .grade import summarize


def team_luck(games: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in games.itertuples():
        rows.append((r.id, r.date, r.home, r.hs, r.hsog)); rows.append((r.id, r.date, r.away, r.as_, r.asog))
    t = pd.DataFrame(rows, columns=["gid", "date", "team", "gf", "sf"]).sort_values(["date", "gid"])
    t["sf"] = pd.to_numeric(t.sf, errors="coerce").fillna(0)
    lg_g = t.gf.cumsum() - t.gf; lg_s = t.sf.cumsum() - t.sf
    t["lg_sh"] = np.where(lg_s > 500, lg_g / lg_s.clip(lower=1), 0.095)
    t["xg_game"] = t.sf * t.lg_sh
    g = t.groupby("team", sort=False)
    t["c_gp"] = g.cumcount()
    t["c_gf"] = g.gf.cumsum() - t.gf
    t["c_xg"] = g.xg_game.cumsum() - t.xg_game
    t["luck"] = np.where(t.c_gp >= 5, (t.c_gf - t.c_xg) / t.c_gp.clip(lower=1), np.nan)
    return t[["gid", "team", "luck", "c_gp"]]


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        if sk.empty:
            continue
        pri = prior_frames(src, season)
        df = predict(sk, pri, games, go, season, cfg, prior_beta=cfg.get("prior_toi_beta", 1.0))
        df = df.merge(team_luck(games), on=["gid", "team"], how="left")
        df["season"] = season
        frames.append(df[["season", "pid", "team", "g", "a", "s", "lam_g", "lam_a", "luck", "c_gp"]])
    d = pd.concat(frames)
    d = d[d.luck.notna() & (d.c_gp >= 10)].copy()
    bins = [-9, -0.6, -0.3, -0.1, 0.1, 0.3, 0.6, 9]
    labels = ["very cold (< -0.6/g)", "cold", "a bit cold", "normal", "a bit hot", "hot", "very hot (> +0.6/g)"]
    d["grp"] = pd.cut(d.luck, bins, labels=labels)
    def tab(x):
        out = []
        for k, g in x.groupby("grp", observed=True):
            pg, pp = 1 - np.exp(-g.lam_g), 1 - np.exp(-(g.lam_g + g.lam_a))
            yg, yp = (g.g >= 1).astype(float), ((g.g + g.a) >= 1).astype(float)
            out.append({"group": str(k), "n": int(len(g)), "goal_pred": float(pg.mean()), "goal_act": float(yg.mean()),
                        "goal_t": float((yg.mean() - pg.mean()) / np.sqrt(yg.var() / len(g) + 1e-12)),
                        "pt_pred": float(pp.mean()), "pt_act": float(yp.mean()),
                        "pt_t": float((yp.mean() - pp.mean()) / np.sqrt(yp.var() / len(g) + 1e-12))})
        return out
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fit, test = d[d.season.isin(half)], d[~d.season.isin(half)]
    def brier(x, beta):
        f = np.exp(beta * x.luck.values)
        pg, pp = 1 - np.exp(-x.lam_g.values * f), 1 - np.exp(-(x.lam_g.values + x.lam_a.values) * f)
        return float((((pg - (x.g >= 1)) ** 2).mean() + ((pp - ((x.g + x.a) >= 1)) ** 2).mean()) / 2)
    grid = [{"beta": b, "brier": brier(fit, b)} for b in (-0.3, -0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2)]
    best = min(grid, key=lambda r: r["brier"])["beta"]
    def sc(x, beta):
        f = np.exp(beta * x.luck.values)
        out = {}
        for m, p, y in (("Goal", 1 - np.exp(-x.lam_g.values * f), (x.g >= 1)), ("Point", 1 - np.exp(-(x.lam_g.values + x.lam_a.values) * f), ((x.g + x.a) >= 1))):
            out[m] = summarize(list(zip(np.clip(p, 1e-4, 1 - 1e-4), np.asarray(y, float))))
        return out
    rep = {"seasons": seasons, "n": int(len(d)), "table": tab(d), "grid": grid, "best_beta": best,
           "test_now": sc(test, 0.0), "test_adj": sc(test, best)}
    (out_dir / "teamluck.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    L = [f"# Team luck and player props: {seasons} (player-games {rep['n']:,}, team 10+ games in)", "",
         "| Team so far (goals - expected, per game) | n | Goal: model | actual | t | Point: model | actual | t |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {x['group']} | {x['n']:,} | {x['goal_pred']:.1%} | {x['goal_act']:.1%} | {x['goal_t']:+.1f} | {x['pt_pred']:.1%} | {x['pt_act']:.1%} | {x['pt_t']:+.1f} |" for x in rep["table"]]
    L += ["", "Adjustment grid (fit seasons, mean Brier goal+point): " + " · ".join(f"{g['beta']:+.2f}: {g['brier']:.6f}" for g in grid) + f" → best {best:+.2f}",
          "", "| Test seasons | Brier now | Skill now | Brier adjusted | Skill adjusted |", "|---|---|---|---|---|"]
    for m in rep["test_now"]:
        a, b = rep["test_now"][m], rep["test_adj"][m]
        L.append(f"| {m} | {a['brier']:.5f} | {a['skill'] * 100:+.2f}% | {b['brier']:.5f} | {b['skill'] * 100:+.2f}% |")
    md = "\n".join(L) + "\n"
    (out_dir / "teamluck.md").write_text(md, encoding="utf-8")
    log(md)
    return rep
