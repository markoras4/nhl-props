"""SHL backtest: replay a season game day by game day with ratings from earlier games only; calibration and skill."""
from __future__ import annotations

import json
import math
import sys
import time

import numpy as np
import pandas as pd

from . import xg as xgm
from .model import CFG, build_state, project


def summarize(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    base = y.mean()
    br, br0 = float(((p - y) ** 2).mean()), float(((base - y) ** 2).mean())
    ll = float(-np.mean(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))))
    bins = []
    for lo in np.arange(0, 1, 0.1):
        m = (p >= lo) & (p < lo + 0.1)
        if m.sum() >= 20:
            bins.append({"bin": f"{lo:.1f}", "n": int(m.sum()), "pred": float(p[m].mean()), "act": float(y[m].mean())})
    return {"n": int(len(y)), "pred": float(p.mean()), "act": float(base), "brier": br, "skill": 1 - br / br0, "logloss": ll, "bins": bins}


def run(season=2025, cfg=CFG, data=None, log=print, every=1):
    sch, P, G, S, GL, names = data
    w, _ = xgm.train(S, test_season=season)          # xG fitted on earlier seasons only
    S = S.copy(); S["xg"] = xgm.predict(S, w)
    games = sch[(sch.season == season) & (sch.state == "post-game")].sort_values("start")
    rows, grows = [], []
    dates = sorted(games.date.unique())[::every]
    t0 = time.time()
    for i, d in enumerate(dates):
        st = build_state(P, G, S, sch, d, cfg, w)
        for gm in games[games.date == d].itertuples():
            pg = P[P.uuid == gm.uuid]
            gg = G[(G.uuid == gm.uuid) & (G.started)]
            lu_h = list(pg[pg.home].pid); lu_a = list(pg[~pg.home].pid)
            sh = gg[gg.home].pid.tolist(); sa = gg[~gg.home].pid.tolist()
            pr = project(st, gm.home, gm.away, lu_h, lu_a, sh[0] if sh else None, sa[0] if sa else None, cfg)
            res = pg.set_index("pid")
            for x in pr["props"]:
                if x["id"] not in res.index:
                    continue
                r = res.loc[x["id"]]
                if isinstance(r, pd.DataFrame):
                    r = r.iloc[0]
                pl = st["players"].get(x["id"], {})
                rows.append({"date": d, "pid": x["id"], "posg": pl.get("posg"), "gp_all": pl.get("gp_all", 0), "toi_proj": pl.get("toi_proj"),
                             "toi": r.toi, **{f"p_{k}": v for k, v in x["p"].items() if k in ("g", "a", "pt", "s1.5", "s2.5", "s3.5", "pt2", "g2")},
                             "lam_g": x["lam"]["g"], "lam_a": x["lam"]["a"], "lam_s": x["lam"]["s"], "g": r.g, "a": r.a, "s": r.s})
            dd = pr["default"]
            grows.append({"date": d, "p_home": dd["p_home"], "home_win": float(gm.hs > sch.loc[gm.Index, "as"]), "exp_total": dd["exp_total"],
                          "total": gm.hs + sch.loc[gm.Index, "as"], "o55": dd["over_5.5"], "mh": pr["mu"]["h"], "ma": pr["mu"]["a"],
                          "hs": gm.hs, "as": sch.loc[gm.Index, "as"]})
        if i % 20 == 0:
            log(f"  {d}: {len(rows)} player-games, {time.time() - t0:.0f}s")
    R, GR = pd.DataFrame(rows), pd.DataFrame(grows)
    R = R[R.gp_all >= 1]
    out = {
        "season": season, "player_games": int(len(R)), "games": int(len(GR)),
        "Goal": summarize(R.p_g, R.g >= 1), "Assist": summarize(R.p_a, R.a >= 1), "Point": summarize(R.p_pt, R.g + R.a >= 1),
        "2+ points": summarize(R.p_pt2, R.g + R.a >= 2), "2+ goals": summarize(R.p_g2, R.g >= 2),
        "SOG 2+": summarize(R["p_s1.5"], R.s >= 2), "SOG 3+": summarize(R["p_s2.5"], R.s >= 3), "SOG 4+": summarize(R["p_s3.5"], R.s >= 4),
        "Home win": summarize(GR.p_home, GR.home_win), "Over 5.5": summarize(GR.o55, GR.total > 5.5),
        "goals_exp_vs_act": {"exp": float(R.p_g.sum()), "act": float((R.g >= 1).sum())},
        "team_goals": {"exp": float(GR.mh.sum() + GR.ma.sum()), "act": float(GR.hs.sum() + GR["as"].sum())},
        "by_role": {}}
    for lab, m in (("F 15+ min", (R.posg == "F") & (R.toi_proj >= 15)), ("F 12-15", (R.posg == "F") & (R.toi_proj >= 12) & (R.toi_proj < 15)),
                   ("F <12", (R.posg == "F") & (R.toi_proj < 12)), ("D", R.posg == "D")):
        q = R[m]
        out["by_role"][lab] = {"n": int(len(q)), "goal": [float(q.p_g.mean()), float((q.g >= 1).mean())], "point": [float(q.p_pt.mean()), float((q.g + q.a >= 1).mean())],
                               "sog2": [float(q["p_s1.5"].mean()), float((q.s >= 2).mean())]}
    return out, R, GR


if __name__ == "__main__":
    import pickle
    data = pickle.load(open(sys.argv[1], "rb"))
    out, R, GR = run(int(sys.argv[2]) if len(sys.argv) > 2 else 2025, data=data, every=int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    print(json.dumps({k: v for k, v in out.items()}, indent=1)[:8000])
