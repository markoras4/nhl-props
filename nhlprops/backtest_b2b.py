"""Back-to-backs: is the live adjustment the right size? (does not touch the website)

The live game model scales a team's expected goals on the 2nd night of a back-to-back: away x b2b_factor_away,
home x b2b_factor_home, and the opponent's goals by the inverse. The replay projections (backtest_totals) don't use
it, so here every factor pair is applied on top of them and judged on the outcome (home win incl. OT/SO, log loss)
and on goals. Fitted on the older seasons, judged on the newer ones; the live setting is shown for comparison.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .backtest_totals import season_rows
from .backtest_markets import markets

FA = [1.0, 0.97, 0.955, 0.945, 0.93, 0.915, 0.90, 0.88]
FH = [1.0, 0.984, 0.97, 0.955, 0.94]


def rest_days(games):
    g = games.sort_values(["date", "id"])
    long = pd.concat([g[["id", "date", "home"]].rename(columns={"home": "team"}).assign(side="h"),
                      g[["id", "date", "away"]].rename(columns={"away": "team"}).assign(side="a")]).sort_values(["date", "id"])
    long["d"] = pd.to_datetime(long.date)
    long["rest"] = long.groupby("team").d.diff().dt.days - 1
    return long.pivot_table(index="id", columns="side", values="rest", aggfunc="first").rename(columns={"h": "rest_h", "a": "rest_a"})


def probs(d, fa, fh, tb):
    out = np.empty(len(d))
    for i, r in enumerate(d.itertuples()):
        bh = fh if r.b2b_h else 1.0
        ba = fa if r.b2b_a else 1.0
        mh, ma = r.mh * bh / ba, r.ma * ba / bh
        m = markets(mh, ma, tb, 0.3, 1.0)
        out[i] = m["h1"] + m["x"] * mh / (mh + ma)
    return np.clip(out, 1e-4, 1 - 1e-4)


def ll(p, y):
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    first = min(seasons)
    prev = pd.DataFrame()
    for y in range(first - 3, first):
        g, _, _ = season_boxscores(src, y, cfg["workers"], log)
        prev = pd.concat([prev, g])
    frames = []
    for y in sorted(seasons):
        df, games = season_rows(src, y, cfg, prev[prev.season >= y - 3] if len(prev) else prev, log)
        df = df.merge(games[["id", "hs", "as_"]], on="id", how="left").join(rest_days(games), on="id")
        df["season"] = y
        frames.append(df)
        prev = pd.concat([prev, games])
    d = pd.concat(frames)
    d = d[(d.gp_min >= 5) & d.hs.notna()].copy()
    d["b2b_h"], d["b2b_a"] = d.rest_h == 0, d.rest_a == 0
    d["y"] = (d.hs > d.as_).astype(float)
    tb = cfg.get("tie_boost", 1.0)
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fit, test = d[d.season.isin(half)], d[~d.season.isin(half)]
    fit_b, test_b = fit[fit.b2b_h | fit.b2b_a], test[test.b2b_h | test.b2b_a]   # only these games change
    grid = []
    for fa, fh in itertools.product(FA, FH):
        grid.append({"fa": fa, "fh": fh, "ll_fit": ll(probs(fit_b, fa, fh, tb), fit_b.y.values)})
    best = min(grid, key=lambda r: r["ll_fit"])
    live = (cfg.get("b2b_factor_away", 0.945), cfg.get("b2b_factor_home", 0.984))

    def judge(fa, fh):
        p = probs(test_b, fa, fh, tb)
        ph = pd.Series(p, index=test_b.index)
        res = {}
        for lab, m, team_home in (("away team on a back-to-back", test_b.b2b_a & ~test_b.b2b_h, False),
                                  ("home team on a back-to-back", test_b.b2b_h & ~test_b.b2b_a, True),
                                  ("both on a back-to-back", test_b.b2b_h & test_b.b2b_a, True)):
            sub = test_b[m]
            if len(sub) < 30:
                continue
            pw = ph[m] if team_home else 1 - ph[m]
            yw = sub.y if team_home else 1 - sub.y
            se = float(np.sqrt((pw * (1 - pw)).sum()) / len(sub))
            res[lab] = {"n": int(len(sub)), "won": float(yw.mean()), "model": float(pw.mean()), "res": float((yw - pw).mean()), "t": float((yw - pw).mean() / se)}
        return {"ll": ll(p, test_b.y.values), "brier": float(((p - test_b.y.values) ** 2).mean()), "groups": res}

    # goals: actual / expected for the tired team and its opponent (no factor applied)
    def ratio(sub, a, e):
        return float(sub[a].sum() / sub[e].sum()) if len(sub) else float("nan")
    rest = d[~d.b2b_h & ~d.b2b_a]
    base_h, base_a = ratio(rest, "hs", "mh"), ratio(rest, "as_", "ma")
    ab, hb = d[d.b2b_a & ~d.b2b_h], d[d.b2b_h & ~d.b2b_a]
    goals = {"away b2b: away goals vs expected": ratio(ab, "as_", "ma") / base_a, "away b2b: home goals vs expected": ratio(ab, "hs", "mh") / base_h,
             "home b2b: home goals vs expected": ratio(hb, "hs", "mh") / base_h, "home b2b: away goals vs expected": ratio(hb, "as_", "ma") / base_a,
             "n_away_b2b": int(len(ab)), "n_home_b2b": int(len(hb))}
    rep = {"seasons": seasons, "fit": half, "best": best, "live": {"fa": live[0], "fh": live[1]},
           "grid_top": sorted(grid, key=lambda r: r["ll_fit"])[:10], "goals": goals,
           "test_none": judge(1.0, 1.0), "test_live": judge(*live), "test_best": judge(best["fa"], best["fh"]),
           "n_test_b2b": int(len(test_b))}
    (out_dir / "b2b.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "b2b.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    b, lv = r["best"], r["live"]
    L = [f"# Back-to-backs: fitted on {r['fit']}, tested on the rest of {r['seasons']}", "",
         f"Best on fit seasons: away x{b['fa']}, home x{b['fh']} · live: away x{lv['fa']}, home x{lv['fh']}", "",
         "Top (fit log loss, b2b games only): " + " · ".join(f"a{g['fa']}/h{g['fh']}: {g['ll_fit']:.5f}" for g in r["grid_top"][:6]), "",
         "## Goals vs the replay's expectation (all seasons, no b2b factor; 1.00 = as expected, normalised to rested games)", ""]
    for k, v in r["goals"].items():
        L.append(f"- {k}: {v:.3f}" if isinstance(v, float) else f"- {k}: {v}")
    L += ["", f"## Test seasons ({r['n_test_b2b']:,} games with a back-to-back)", "| Setting | Log loss | Brier |", "|---|---|---|"]
    for k, lab in (("test_none", "no adjustment"), ("test_live", f"live (a{lv['fa']}/h{lv['fh']})"), ("test_best", f"best fit (a{b['fa']}/h{b['fh']})")):
        L.append(f"| {lab} | {r[k]['ll']:.5f} | {r[k]['brier']:.5f} |")
    for k, lab in (("test_none", "no adjustment"), ("test_live", "live"), ("test_best", "best fit")):
        L += ["", f"### Tired team's results, test seasons: {lab}", "| Group | n | Tired team won | Model | Residual | t |", "|---|---|---|---|---|---|"]
        for g, x in r[k]["groups"].items():
            L.append(f"| {g} | {x['n']:,} | {x['won']:.1%} | {x['model']:.1%} | {x['res']*100:+.1f} pts | {x['t']:+.1f} |")
    return "\n".join(L) + "\n"
