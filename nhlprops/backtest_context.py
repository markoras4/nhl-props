"""Context backtest: referees and rest/travel (does not touch the website).

Starts from the point-in-time game projections of the totals backtest (backtest_totals.season_rows) and asks
whether things the model doesn't use yet explain what it gets wrong:
  * referees: each crew's penalty rate in its earlier games (shrunk to the league) -> penalties called, and the
    total-goals residual (actual - model)
  * rest and travel: days of rest, back-to-backs, distance flown since the previous game and time zones crossed ->
    the goal-margin residual and the total-goals residual
A factor is worth adding only if its effect is clearly larger than its noise (about 2 standard errors) and big
enough to matter for a price.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .backtest_totals import season_rows
from .sources import NHL

ARENA = {  # lat, lon, standard UTC offset
    "ANA": (33.807, -117.876, -8), "BOS": (42.366, -71.062, -5), "BUF": (42.875, -78.876, -5), "CGY": (51.037, -114.051, -7),
    "CAR": (35.803, -78.722, -5), "CHI": (41.880, -87.674, -6), "COL": (39.749, -105.008, -7), "CBJ": (39.969, -83.006, -5),
    "DAL": (32.790, -96.810, -6), "DET": (42.341, -83.055, -5), "EDM": (53.547, -113.498, -7), "FLA": (26.158, -80.326, -5),
    "LAK": (34.043, -118.267, -8), "MIN": (44.945, -93.101, -6), "MTL": (45.496, -73.569, -5), "NSH": (36.159, -86.779, -6),
    "NJD": (40.733, -74.171, -5), "NYI": (40.711, -73.726, -5), "NYR": (40.750, -73.993, -5), "OTT": (45.297, -75.927, -5),
    "PHI": (39.901, -75.172, -5), "PIT": (40.439, -79.989, -5), "SJS": (37.333, -121.901, -8), "SEA": (47.622, -122.354, -8),
    "STL": (38.627, -90.203, -6), "TBL": (27.943, -82.452, -5), "TOR": (43.643, -79.379, -5), "UTA": (40.768, -111.901, -7),
    "VAN": (49.278, -123.109, -8), "VGK": (36.103, -115.178, -8), "WSH": (38.898, -77.021, -5), "WPG": (49.893, -97.144, -6),
    "ARI": (33.532, -112.261, -7)}


def km(a, b):
    if a not in ARENA or b not in ARENA:
        return np.nan
    (la1, lo1, _), (la2, lo2, _) = ARENA[a], ARENA[b]
    p1, p2 = math.radians(la1), math.radians(la2)
    d = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(d))


def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def officials(src, gid):
    """referee names and penalties called (minors and majors) for one game"""
    rr = src.json(f"{NHL}/gamecenter/{gid}/right-rail", f"nhl/rr/{gid}.json", None) or {}
    refs = _find(rr, "referees") or []
    names = sorted({(r.get("default") if isinstance(r, dict) else str(r)) for r in refs if r})
    land = src.json(f"{NHL}/gamecenter/{gid}/landing", f"nhl/land/{gid}.json", None) or {}
    pens = 0
    for per in ((land.get("summary") or {}).get("penalties") or []):
        for p in per.get("penalties") or []:
            if (p.get("duration") or 0) in (2, 4, 5):     # minors, double minors, majors (not misconducts)
                pens += 1
    return names, pens


def prefetch(src, ids, workers, log=print):
    from concurrent.futures import ThreadPoolExecutor
    log(f"  officials and penalties for {len(ids)} games ...")
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda i: officials(src, int(i)), ids))


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    first = min(seasons)
    prev = pd.DataFrame()
    for y in range(first - 3, first):
        g, _, _ = season_boxscores(src, y, cfg["workers"], log)
        prev = pd.concat([prev, g])
    # referee history starts one season before the test seasons
    ref_hist: dict[str, list] = {}
    lg_p = [0.0, 0]
    warm = prev[prev.season == first - 1].sort_values("date")
    ids = list(warm.id)
    for y in sorted(seasons):
        g_, _, _ = season_boxscores(src, y, cfg["workers"], log)
        ids += list(g_.id)
    prefetch(src, ids, cfg["workers"], log)
    log(f"  referee warm-up: {len(warm)} games")
    for gm in warm.itertuples():
        names, pens = officials(src, int(gm.id))
        for n in names:
            ref_hist.setdefault(n, []).append(pens)
        lg_p[0] += pens; lg_p[1] += 1
    rows = []
    for y in sorted(seasons):
        df, games = season_rows(src, y, cfg, prev[prev.season >= y - 3] if len(prev) else prev, log)
        df = df.merge(games[["id", "hs", "as_"]], on="id", how="left").sort_values(["date", "id"])
        last: dict[str, tuple] = {}            # team -> (date, venue team)
        for r in df.itertuples():
            names, pens = officials(src, int(r.id))
            lg = lg_p[0] / lg_p[1] if lg_p[1] else 8.0
            crew = [((sum(ref_hist.get(n, [])) + 20 * lg) / (len(ref_hist.get(n, [])) + 20)) for n in names]
            out = {"season": y, "id": r.id, "date": r.date, "home": r.home, "away": r.away, "total": r.total, "exp": r.exp,
                   "mh": r.mh, "ma": r.ma, "hs": r.hs, "as_": r.as_, "gp_min": r.gp_min, "pens": pens,
                   "crew": float(np.mean(crew)) - lg if len(crew) == 2 else np.nan, "nrefs": len(names)}
            d = pd.Timestamp(r.date)
            for side, team in (("h", r.home), ("a", r.away)):
                if team in last:
                    pd_, pv = last[team]
                    out[f"rest_{side}"] = (d - pd_).days - 1
                    out[f"km_{side}"] = km(pv, r.home)
                    out[f"tz_{side}"] = abs(ARENA.get(pv, (0, 0, 0))[2] - ARENA.get(r.home, (0, 0, 0))[2])
                else:
                    out[f"rest_{side}"] = np.nan; out[f"km_{side}"] = np.nan; out[f"tz_{side}"] = np.nan
            rows.append(out)
            for n in names:
                ref_hist.setdefault(n, []).append(pens)
            lg_p[0] += pens; lg_p[1] += 1
            last[r.home] = (d, r.home); last[r.away] = (d, r.home)
        prev = pd.concat([prev, games])
    d = pd.DataFrame(rows)
    d = d[d.gp_min >= 5].copy()
    d["res_tot"] = d.total - d.exp
    d["res_mar"] = (d.hs - d.as_) - (d.mh - d.ma)
    rep = {"seasons": seasons, "games": int(len(d)), "with_refs": int((d.nrefs == 2).sum())}

    def slope(x, yv):
        ok = ~(np.isnan(x) | np.isnan(yv))
        x, yv = x[ok], yv[ok]
        if len(x) < 50:
            return None
        xm = x - x.mean()
        b = float((xm * (yv - yv.mean())).sum() / (xm ** 2).sum())
        resid = yv - yv.mean() - b * xm
        se = float(math.sqrt((resid ** 2).sum() / (len(x) - 2) / (xm ** 2).sum()))
        return {"n": int(len(x)), "slope": b, "se": se, "t": b / se if se else None}

    def groups(col, edges, labels, target):
        g = pd.cut(d[col], edges, labels=labels)
        return [{"group": str(k), "n": int(len(m)), "mean": float(m[target].mean()),
                 "se": float(m[target].std() / math.sqrt(max(len(m), 1)))} for k, m in d.groupby(g, observed=True)]

    c = d.crew.values.astype(float)
    rep["crew_vs_penalties"] = slope(c, d.pens.values.astype(float))
    rep["crew_vs_total_residual"] = slope(c, d.res_tot.values.astype(float))
    rep["crew_groups_total_residual"] = groups("crew", [-99, -0.75, -0.25, 0.25, 0.75, 99],
                                               ["much fewer calls", "fewer", "average", "more", "much more calls"], "res_tot")
    rep["pens_mean"] = float(d.pens.mean())
    d["rest_diff"] = d.rest_h.clip(upper=3) - d.rest_a.clip(upper=3)
    rep["rest_diff_groups_margin"] = groups("rest_diff", [-9, -1.5, -0.5, 0.5, 1.5, 9],
                                            ["away 2+ days more rest", "away 1 more", "equal", "home 1 more", "home 2+ more"], "res_mar")
    rep["away_b2b_margin"] = groups("rest_a", [-9, 0.5, 9], ["away on back-to-back", "away rested"], "res_mar")
    rep["home_b2b_margin"] = groups("rest_h", [-9, 0.5, 9], ["home on back-to-back", "home rested"], "res_mar")
    rep["away_travel_margin"] = groups("km_a", [-1, 1, 800, 1600, 3000, 99999],
                                       ["no travel (consecutive road game in same city?)", "under 800 km", "800-1,600 km", "1,600-3,000 km", "3,000+ km"], "res_mar")
    rep["away_tz_margin"] = groups("tz_a", [-1, 0.5, 1.5, 2.5, 9], ["same time zone", "1 zone", "2 zones", "3 zones"], "res_mar")
    d["tired"] = (d.rest_h == 0).astype(int) + (d.rest_a == 0).astype(int)
    rep["b2b_total_residual"] = groups("tired", [-1, 0.5, 1.5, 3], ["neither on b2b", "one team on b2b", "both on b2b"], "res_tot")
    rep["away_km_vs_margin"] = slope(d.km_a.values.astype(float) / 1000, d.res_mar.values.astype(float))
    (out_dir / "context.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "context.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    def sl(x):
        return "n/a" if not x else f"slope {x['slope']:+.3f} ± {x['se']:.3f} (t = {x['t']:+.1f}, n = {x['n']:,})"

    def tb(rows, what):
        L = [f"| Group | Games | {what} | ± |", "|---|---|---|---|"]
        L += [f"| {x['group']} | {x['n']:,} | {x['mean']:+.3f} | {x['se']:.3f} |" for x in rows]
        return L
    L = [f"# Referees, rest & travel: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}",
         f"Games: {r['games']:,} · with both referees known: {r['with_refs']:,} · penalties per game: {r['pens_mean']:.2f}", "",
         "## Referees", f"Crew rate vs penalties called: {sl(r['crew_vs_penalties'])}",
         f"Crew rate vs total-goals residual (goals per extra penalty in the crew's rate): {sl(r['crew_vs_total_residual'])}", ""]
    L += tb(r["crew_groups_total_residual"], "Total goals minus model") + ["", "## Rest & travel (goal margin, home minus away, vs model)"]
    L += tb(r["rest_diff_groups_margin"], "Margin minus model") + [""] + tb(r["away_b2b_margin"], "Margin minus model") + [""]
    L += tb(r["home_b2b_margin"], "Margin minus model") + [""] + tb(r["away_travel_margin"], "Margin minus model") + [""]
    L += tb(r["away_tz_margin"], "Margin minus model") + ["", f"Away distance (per 1,000 km) vs margin: {sl(r['away_km_vs_margin'])}", "",
         "## Back-to-backs and total goals"] + tb(r["b2b_total_residual"], "Total goals minus model")
    return "\n".join(L) + "\n"
