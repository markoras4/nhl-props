"""Power-play points backtest: is the site's PPP chance too low? (does not touch the website)

The live Triage ledger showed "No power-play point" bets winning far less often than the model said (73% vs 84%),
and "Yes" bets more often. This replays past seasons, point in time, with the site's PPP model:
  lam = (PP points this season + K * prior) / (games + K) x opponent penalty factor ^ beta     (K = 20, beta = 0.5)
  prior = last season's 5-on-4 points per game (MoneyPuck)
and checks calibration overall, by predicted chance (the priced players are the top buckets) and by role. Variants:
  * prior from last season's actual PP points per game (play-by-play, every PP situation: 5-on-3, 4-on-3 too)
  * a fitted multiplier on the rate
  * smaller K (trust this season's PP points sooner)
Fitted on the older half of the seasons, tested on the newer half.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict
from .backtest_extra import pbp_season, mp_pp, shrink, score, ll

BUCKETS = [(0, .10), (.10, .20), (.20, .30), (.30, .40), (.40, 1.0)]


def frames(src, seasons, cfg, log=print) -> pd.DataFrame:
    rows = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        pri = prior_frames(src, season)
        df = predict(sk, pri, games, go, season, cfg)
        prev_games, prev_sk, _ = season_boxscores(src, season - 1, cfg["workers"], log)
        pb = pbp_season(src, games, season, cfg["workers"], log)
        pbp_prev = pbp_season(src, prev_games, season - 1, cfg["workers"], log)
        ppg = {}
        for gid, g in pb.items():
            for pid in g["pp"]:
                ppg[(int(gid), pid)] = ppg.get((int(gid), pid), 0) + 1
        df["ppp"] = [ppg.get((int(a), int(b)), 0) for a, b in zip(df.gid, df.pid)]
        prev_pp = {}
        for g in pbp_prev.values():
            for pid in g["pp"]:
                prev_pp[pid] = prev_pp.get(pid, 0) + 1
        prev_gp = prev_sk.groupby("pid").gid.count().to_dict()
        lg_prev = sum(prev_pp.values()) / max(1, sum(prev_gp.values()))
        pmp, pp_lg = mp_pp(src, season)
        team_of = {int(r.id): (r.home, r.away) for r in games.itertuples()}
        df = df.sort_values(["date", "gid"])
        tpen, tgp, lg_pen, c = {}, {}, [0.0, 0], {}
        for date, day in df.groupby("date", sort=True):
            for r in day.itertuples():
                pc = c.get(r.pid, {"ppp": 0, "gp": 0, "l10": []})
                mp = pmp.get(int(r.pid))
                if mp and mp[1] > 0 and mp[2] > 0:
                    prior54 = shrink(mp[0], mp[1], pp_lg * 0.6, 6) * mp[1] / mp[2]
                else:
                    prior54 = 0.03
                g0 = prev_gp.get(r.pid, 0)
                prior_all = (prev_pp.get(r.pid, 0) + 10 * lg_prev * 0.6) / (g0 + 10) if g0 else 0.03
                lgp = (lg_pen[0] / lg_pen[1]) if lg_pen[1] else 3.3
                op = shrink(tpen.get(r.opp, 0), tgp.get(r.opp, 0), lgp, 15)
                rows.append({"season": season, "pid": r.pid, "posg": r.posg, "gp": r.c_gp, "toi_proj": r.toi_proj,
                             "ppp": r.ppp, "cur_ppp": pc["ppp"], "cur_gp": pc["gp"], "prior54": prior54, "prior_all": prior_all,
                             "l10": sum(pc["l10"][-10:]), "opp_pen": op / lgp if lgp else 1.0})
            for r in day.itertuples():
                pc = c.setdefault(r.pid, {"ppp": 0, "gp": 0, "l10": []})
                pc["ppp"] += r.ppp; pc["gp"] += 1; pc["l10"].append(r.ppp)
            for gid in set(day.gid):
                g = pb.get(str(int(gid)))
                h, a = team_of.get(int(gid), (None, None))
                if not g or not h:
                    continue
                for team, tid in ((h, g["hid"]), (a, g["aid"])):
                    tpen[team] = tpen.get(team, 0) + g["pen"].get(str(tid), 0); tgp[team] = tgp.get(team, 0) + 1
                    lg_pen[0] += g["pen"].get(str(tid), 0); lg_pen[1] += 1
        log(f"  {season}: {len(df):,} player-games")
    d = pd.DataFrame(rows)
    return d[d.gp >= 5].reset_index(drop=True)


def lam_of(d, prior="prior54", K=20.0, beta=0.5, mult=1.0):
    return mult * (d.cur_ppp + K * d[prior]) / (d.cur_gp + K) * d.opp_pen ** beta


def calib(p, y, m=None):
    m = np.ones(len(p), bool) if m is None else m
    out = []
    for lo, hi in BUCKETS:
        mm = m & (p >= lo) & (p < hi)
        if mm.sum() < 200:
            continue
        se = math.sqrt(max(p[mm].mean() * (1 - p[mm].mean()), 1e-9) / mm.sum())
        out.append({"range": f"{int(lo*100)}-{int(hi*100) if hi < 1 else 100}%", "n": int(mm.sum()), "pred": float(p[mm].mean()),
                    "act": float(y[mm].mean()), "t": float((y[mm].mean() - p[mm].mean()) / se)})
    return out


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, log)
    y = (d.ppp >= 1).values.astype(float)
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fm = d.season.isin(half).values; tm = ~fm
    rep = {"seasons": seasons, "fit": half, "rows": int(len(d)), "rows_test": int(tm.sum())}
    p0 = 1 - np.exp(-lam_of(d).values)
    rep["site"] = {"all": score(p0[tm], y[tm]), "calib": calib(p0, y, tm),
                   "mean_pred": float(p0[tm].mean()), "mean_act": float(y[tm].mean()),
                   "calib_F": calib(p0, y, tm & (d.posg == "F").values), "calib_D": calib(p0, y, tm & (d.posg == "D").values)}
    # grid on the fit seasons
    grid = {}
    for prior in ("prior54", "prior_all"):
        for K in (5, 10, 20, 40):
            for beta in (0.0, 0.5):
                for mult in (1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3):
                    p = 1 - np.exp(-lam_of(d, prior, K, beta, mult).values)
                    grid[f"{prior}|K{K}|b{beta}|x{mult}"] = ll(p[fm], y[fm])
    best = min(grid, key=grid.get)
    pr, K, b, x = best.split("|")
    pb_ = 1 - np.exp(-lam_of(d, pr, float(K[1:]), float(b[1:]), float(x[1:])).values)
    # same prior and K as the site, only a multiplier (smallest change)
    mg = {m: ll((1 - np.exp(-lam_of(d, "prior54", 20, 0.5, m).values))[fm], y[fm]) for m in (1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.4)}
    mb = min(mg, key=mg.get)
    pm = 1 - np.exp(-lam_of(d, "prior54", 20, 0.5, mb).values)
    rep["best"] = {"key": best, "all": score(pb_[tm], y[tm]), "calib": calib(pb_, y, tm), "mean_pred": float(pb_[tm].mean())}
    rep["mult_only"] = {"mult": mb, "all": score(pm[tm], y[tm]), "calib": calib(pm, y, tm), "mean_pred": float(pm[tm].mean())}
    rep["top_grid"] = sorted(grid.items(), key=lambda z: z[1])[:8]
    (out_dir / "ppp.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "ppp.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    def cal(rows):
        L = ["| Model chance | n | Model | Actual | t |", "|---|---|---|---|---|"]
        L += [f"| {x['range']} | {x['n']:,} | {x['pred']:.1%} | {x['act']:.1%} | {x['t']:+.1f} |" for x in rows]
        return L
    s = r["site"]
    L = [f"# Power-play points: fit on {r['fit']}, tested on the rest of {r['seasons']} ({r['rows_test']:,} test player-games)", "",
         f"## Site model today: predicted {s['mean_pred']:.2%}, actual {s['mean_act']:.2%} · skill {s['all']['skill']*100:+.2f}%", ""]
    L += cal(s["calib"]) + ["", "Forwards:", ""] + cal(s["calib_F"]) + ["", "Defencemen:", ""] + cal(s["calib_D"])
    m = r["mult_only"]
    L += ["", f"## Same model x {m['mult']}: predicted {m['mean_pred']:.2%} · skill {m['all']['skill']*100:+.2f}%", ""] + cal(m["calib"])
    b = r["best"]
    L += ["", f"## Best variant {b['key']}: predicted {b['mean_pred']:.2%} · skill {b['all']['skill']*100:+.2f}%", ""] + cal(b["calib"])
    L += ["", "Top of the grid (fit seasons, log loss):", ""] + [f"- {k}: {v:.5f}" for k, v in r["top_grid"]]
    return "\n".join(L) + "\n"
