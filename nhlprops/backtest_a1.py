"""Primary vs secondary assists backtest: should the assist rate weight first assists more? (does not touch the website)

Secondary assists barely repeat from season to season; primary assists repeat much better. The model builds each
player's assist rate from his previous seasons (MoneyPuck) plus this season, counting every assist the same.
Here the previous-season assists are counted as  A1 + w x A2  (rescaled each season so the league's total assists
don't change, so only the split between players moves), for w = 1 (today), 0.75, 0.5, 0.25 and 0.
Point-in-time replay of the same seasons with the site-like model; skill for 1+ assist and 1+ point, overall and
for players whose history is heavy in secondary assists. Fit on the older half of the seasons, tested on the newer.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict
from .grade import summarize
from .model import _num, _sit

WS = (1.0, 0.75, 0.5, 0.25, 0.0)


def a_split(src, season: int) -> pd.DataFrame:
    rows = []
    for y in range(season - 8, season):
        d = src.moneypuck("skaters", y, False)
        if d is None:
            continue
        d = d[_sit(d) == "all"]
        rows.append(pd.DataFrame({"pid": pd.to_numeric(d.playerId, errors="coerce"), "season": y,
                                  "a1": _num(d, "I_F_primaryAssists"), "a2": _num(d, "I_F_secondaryAssists")}))
    return pd.concat(rows).groupby(["pid", "season"], as_index=False).sum() if rows else pd.DataFrame()


def weighted_prior(pri: dict, split: pd.DataFrame, w: float) -> dict:
    sk = pri["sk"].merge(split, on=["pid", "season"], how="left").fillna({"a1": 0.0, "a2": 0.0})
    tot = sk.groupby("season")[["a1", "a2"]].transform("sum")
    scale = (tot.a1 + tot.a2) / (tot.a1 + w * tot.a2).replace(0, np.nan)
    has = (sk.a1 + sk.a2) > 0
    sk.loc[has, "a"] = ((sk.a1 + w * sk.a2) * scale)[has]
    return {**pri, "sk": sk.drop(columns=["a1", "a2"])}


def score(p, y):
    return summarize(list(zip(np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4), np.asarray(y, float))))


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    seasons = sorted(seasons)
    half = seasons[: max(1, len(seasons) // 2)]
    res = {w: [] for w in WS}
    for season in seasons:
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        pri = prior_frames(src, season)
        split = a_split(src, season)
        # share of secondary assists in each player's previous 3 seasons (for the subgroup check)
        sp = split[split.season >= season - 3].groupby("pid")[["a1", "a2"]].sum()
        sec = (sp.a2 / (sp.a1 + sp.a2).replace(0, np.nan)).to_dict()
        for w in WS:
            df = predict(sk, weighted_prior(pri, split, w), games, go, season, cfg, prior_beta=1.0)
            df["season"] = season
            df["sec"] = df.pid.map(sec)
            res[w].append(df[["season", "pid", "g", "a", "lam_g", "lam_a", "c_gp", "sec", "posg"]])
        log(f"  {season}: done ({len(sk):,} player-games)")
    rep = {"seasons": seasons, "fit": half, "rows": {}}
    for w in WS:
        d = pd.concat(res[w], ignore_index=True)
        tm = ~d.season.isin(half)
        pa, pp = 1 - np.exp(-d.lam_a), 1 - np.exp(-(d.lam_g + d.lam_a))
        ya, yp = (d.a >= 1).astype(float), ((d.g + d.a) >= 1).astype(float)
        out = {}
        for name, m in (("fit", ~tm), ("test", tm), ("test_sec_heavy", tm & (d.sec >= 0.55)), ("test_a1_heavy", tm & (d.sec <= 0.40)),
                        ("test_F", tm & (d.posg == "F")), ("test_D", tm & (d.posg == "D"))):
            if m.sum() < 500:
                continue
            sa, sp_ = score(pa[m], ya[m]), score(pp[m], yp[m])
            out[name] = {"n": int(m.sum()), "assist_skill": sa["skill"], "point_skill": sp_["skill"],
                         "assist_pred": float(pa[m].mean()), "assist_act": float(ya[m].mean())}
        rep["rows"][str(w)] = out
    (out_dir / "a1.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "a1.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# Primary vs secondary assists: previous seasons counted as A1 + w x A2 · fit {r['fit']}, tested on the rest of {r['seasons']}", "",
         "Skill = how much better than always guessing the average (higher is better). w = 1 is today's model.", ""]
    for grp in ("fit", "test", "test_F", "test_D", "test_sec_heavy", "test_a1_heavy"):
        L += [f"## {grp}", "", "| w | n | 1+ assist skill | 1+ point skill | assist predicted | assist actual |", "|---|---|---|---|---|---|"]
        for w, rows in r["rows"].items():
            x = rows.get(grp)
            if x:
                L.append(f"| {w} | {x['n']:,} | {x['assist_skill']*100:+.3f}% | {x['point_skill']*100:+.3f}% | {x['assist_pred']:.2%} | {x['assist_act']:.2%} |")
        L.append("")
    return "\n".join(L) + "\n"
