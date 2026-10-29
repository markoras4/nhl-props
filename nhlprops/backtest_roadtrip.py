"""Road trips: is the last game of a road trip often a loss? Does the length of the trip matter? (does not touch the website)

For every team, a road trip is a run of consecutive away games between home games. Each away game gets its trip
length and its place in the trip. Two questions:
  1. Raw: how often does the road team win (incl. OT/SO) in the last game of a trip vs the other road games?
  2. Beyond strength: the same, against the model's point-in-time win chance (backtest_totals projections), so a
     trip that happens to end against a strong team isn't counted as a "last-game effect".
Residual = actual wins - model wins; a real effect has to be clearly bigger than its standard error.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .backtest_totals import season_rows
from .backtest_markets import markets


def trips(games: pd.DataFrame) -> pd.DataFrame:
    """one row per away game: game id, team, trip length, place in trip, days since the previous game"""
    g = games.sort_values(["date", "id"])
    long = pd.concat([g.assign(team=g.home, at_home=True), g.assign(team=g.away, at_home=False)])
    out = []
    for team, s in long.sort_values(["date", "id"]).groupby("team"):
        s = s.reset_index(drop=True)
        dates = pd.to_datetime(s.date)
        i = 0
        while i < len(s):
            if s.at_home[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(s) and not s.at_home[j + 1]:
                j += 1
            L = j - i + 1
            for k in range(i, j + 1):
                rest = (dates[k] - dates[k - 1]).days - 1 if k > 0 else np.nan
                nxt = (dates[k + 1] - dates[k]).days - 1 if k + 1 < len(s) else np.nan
                out.append({"id": s.id[k], "team": team, "L": L, "pos": k - i + 1, "last": k == j, "first": k == i,
                            "rest": rest, "b2b": rest == 0, "rest_after": nxt})
            i = j + 1
    return pd.DataFrame(out)


def stat(sub):
    n = len(sub)
    if n < 30:
        return None
    y, p = sub.win.astype(float), sub.p
    res = float((y - p).mean())
    se = float(np.sqrt((p * (1 - p)).sum()) / n)
    return {"n": n, "win": float(y.mean()), "model": float(p.mean()), "res": res, "se": se, "t": res / se if se else 0.0,
            "pts": float(sub.pts.mean())}


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    first = min(seasons)
    prev = pd.DataFrame()
    for y in range(first - 3, first):
        g, _, _ = season_boxscores(src, y, cfg["workers"], log)
        prev = pd.concat([prev, g])
    rows = []
    tb = cfg.get("tie_boost", 1.0)
    for y in sorted(seasons):
        df, games = season_rows(src, y, cfg, prev[prev.season >= y - 3] if len(prev) else prev, log)
        tr = trips(games)
        df = df.merge(games[["id", "hs", "as_", "last"]].rename(columns={"last": "lastp"}), on="id", how="left")
        df = df.merge(tr, left_on=["id", "away"], right_on=["id", "team"], how="inner")
        for r in df.itertuples():
            if pd.isna(r.hs) or pd.isna(r.as_):
                continue
            m = markets(r.mh, r.ma, tb, 0.3, 1.0)
            share_a = r.ma / (r.mh + r.ma)
            p_win = m["a2"] + m["x"] * share_a            # road team wins incl. OT/SO
            win = r.as_ > r.hs
            pts = 2 if win else (1 if r.lastp in ("OT", "SO") else 0)
            rows.append({"season": y, "team": r.away, "L": r.L, "pos": r.pos, "last": r.last, "first": r.first,
                         "rest": r.rest, "b2b": r.b2b, "rest_after": r.rest_after, "p": p_win, "win": win, "pts": pts,
                         "gp_min": r.gp_min})
        prev = pd.concat([prev, games])
    d = pd.DataFrame(rows)
    d = d[d.gp_min >= 5].copy()
    Lb = lambda L: "5+" if L >= 5 else str(int(L))
    d["Lb"] = d.L.map(Lb)
    rep = {"seasons": seasons, "n": int(len(d)),
           "all_road": stat(d),
           "last_vs_rest": {"last game of a trip (trips of 2+)": stat(d[(d.L >= 2) & d["last"]]),
                            "other games of trips of 2+": stat(d[(d.L >= 2) & ~d["last"]]),
                            "single road games (trip of 1)": stat(d[d.L == 1])},
           "by_length_last": {f"trip of {k}": stat(d[(d.Lb == k) & d["last"]]) for k in ["2", "3", "4", "5+"]},
           "by_length_other": {f"trip of {k}": stat(d[(d.Lb == k) & ~d["last"]]) for k in ["2", "3", "4", "5+"]},
           "by_position": {f"game {k} of the trip": stat(d[(d.L >= 3) & (d.pos == k) & ~d["last"]]) for k in [1, 2, 3, 4]}
                          | {"last game (trips of 3+)": stat(d[(d.L >= 3) & d["last"]])},
           "last_b2b": {"last game, back-to-back": stat(d[(d.L >= 2) & d["last"] & d.b2b]),
                        "last game, rested": stat(d[(d.L >= 2) & d["last"] & ~d.b2b]),
                        "other road games, back-to-back": stat(d[~((d.L >= 2) & d["last"]) & d.b2b]),
                        "other road games, rested": stat(d[~((d.L >= 2) & d["last"]) & ~d.b2b])},
           "by_season_last": {str(y): stat(d[(d.season == y) & (d.L >= 2) & d["last"]]) for y in sorted(seasons)},
           "b2b_share": {"last game": float(d[(d.L >= 2) & d["last"]].b2b.mean()), "other road": float(d[~((d.L >= 2) & d["last"])].b2b.mean())}}
    (out_dir / "roadtrip.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "roadtrip.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# Road trips, seasons {r['seasons']} ({r['n']:,} road games)", "",
         "win = road team won (incl. OT/SO) · model = the model's point-in-time chance · residual = actual − model (t = residual / standard error)", ""]
    def tb(title, rows):
        out = [f"## {title}", "| Group | n | Road win % | Model | Residual | t | Points/game |", "|---|---|---|---|---|---|---|"]
        for k, x in rows.items():
            if x:
                out.append(f"| {k} | {x['n']:,} | {x['win']:.1%} | {x['model']:.1%} | {x['res']*100:+.1f} pts | {x['t']:+.1f} | {x['pts']:.2f} |")
        return out + [""]
    L += tb("All road games", {"all": r["all_road"]})
    L += tb("Last game of a trip vs the rest", r["last_vs_rest"])
    L += tb("Last game, by trip length", r["by_length_last"])
    L += tb("Other (not last) games, by trip length", r["by_length_other"])
    L += tb("Place in the trip (trips of 3+ games)", r["by_position"])
    L += tb("Back-to-backs", r["last_b2b"])
    L += tb("Last game, by season", r["by_season_last"])
    L += [f"Share of back-to-backs: last game {r['b2b_share']['last game']:.1%}, other road games {r['b2b_share']['other road']:.1%}", ""]
    return "\n".join(L) + "\n"
