"""Schedule & stakes backtest: does the calendar explain what the model gets wrong? (does not touch the website)

Starts from the point-in-time game projections of the totals backtest (backtest_totals.season_rows, which knows
nothing about the schedule) and asks, for each team in each game:
  1. Busy stretches: back-to-back, 3 games in 4 nights, 4 games in 6 nights (tonight included)
  2. Looking ahead: plays again tomorrow; next game (within 2 days) is against a top-third team
  3. Late-season stakes (team's last ~20 games): out of the race, on the bubble, or safe, from point-in-time
     standings (2 points a win, 1 an overtime/shootout loss; gap to 8th place in the conference)
Targets: goal margin (home minus away) and total goals, both minus the model; for stakes also the ice time and
points of each team's top forwards vs their own season-to-date averages.
All effects are fitted together (least squares) so a back-to-back isn't double counted as a 3-in-4.
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

WEST = {"ANA", "CGY", "EDM", "LAK", "SJS", "SEA", "VAN", "VGK", "ARI", "UTA", "CHI", "COL", "DAL", "MIN", "NSH", "STL", "WPG"}


def conf(t):
    return "W" if t in WEST else "E"


def ols(X: np.ndarray, y: np.ndarray, names: list[str]) -> list[dict]:
    ok = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
    X, y = X[ok], y[ok]
    X1 = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    res = y - X1 @ beta
    s2 = (res ** 2).sum() / max(len(y) - X1.shape[1], 1)
    cov = s2 * np.linalg.pinv(X1.T @ X1)
    out = []
    for i, n in enumerate(names, start=1):
        se = float(math.sqrt(max(cov[i, i], 0)))
        out.append({"factor": n, "n": int(X[:, i - 1].sum()) if set(np.unique(X[:, i - 1])) <= {0, 1} else int(len(X)),
                    "coef": float(beta[i]), "se": se, "t": float(beta[i] / se) if se else None})
    return out


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    first = min(seasons)
    prev = pd.DataFrame()
    for y in range(first - 3, first):
        g, _, _ = season_boxscores(src, y, cfg["workers"], log)
        prev = pd.concat([prev, g])
    rows, prow = [], []
    for y in sorted(seasons):
        df, games = season_rows(src, y, cfg, prev[prev.season >= y - 3] if len(prev) else prev, log)
        _, sk, _ = season_boxscores(src, y, cfg["workers"], log)
        games = games.sort_values(["date", "id"])
        df = df.merge(games[["id", "hs", "as_", "last"]], on="id", how="left").sort_values(["date", "id"])
        # every team's schedule (dates) for busy-stretch and look-ahead
        sched: dict[str, list] = {}
        for g in games.itertuples():
            d = pd.Timestamp(g.date)
            sched.setdefault(g.home, []).append((d, g.id, g.away))
            sched.setdefault(g.away, []).append((d, g.id, g.home))
        n_games = {t: len(v) for t, v in sched.items()}
        pos = {t: {gid: i for i, (_, gid, _) in enumerate(v)} for t, v in sched.items()}
        pts: dict[str, int] = {}
        gp: dict[str, int] = {}
        # top forwards' season-to-date ice time and points
        skf = sk[sk.posg == "F"].sort_values(["date", "gid"])
        pl_hist: dict[int, list] = {}
        sk_by_game = {k: v for k, v in skf.groupby(["gid", "team"])}

        def ptspct(t):
            return pts.get(t, 0) / (2 * gp[t]) if gp.get(t) else 0.5

        for r in df.itertuples():
            d = pd.Timestamp(r.date)
            # point-in-time conference standings (before this game)
            out = {"season": y, "id": r.id, "date": r.date, "home": r.home, "away": r.away, "total": r.total, "exp": r.exp,
                   "mh": r.mh, "ma": r.ma, "hs": r.hs, "as_": r.as_, "gp_min": r.gp_min}
            strong_cut = np.nanpercentile([ptspct(t) for t in sched], 67) if gp else 0.6
            for side, team in (("h", r.home), ("a", r.away)):
                s, i = sched[team], pos[team][r.id]
                dates = [x[0] for x in s]
                out[f"b2b_{side}"] = int(i > 0 and (d - dates[i - 1]).days == 1)
                out[f"d34_{side}"] = int(i >= 2 and (d - dates[i - 2]).days <= 3)       # 3 games in 4 nights
                out[f"d46_{side}"] = int(i >= 3 and (d - dates[i - 3]).days <= 5)       # 4 games in 6 nights
                nx = s[i + 1] if i + 1 < len(s) else None
                out[f"tmrw_{side}"] = int(bool(nx) and (nx[0] - d).days == 1)
                out[f"nxs_{side}"] = int(bool(nx) and (nx[0] - d).days <= 2 and gp.get(nx[2], 0) >= 10 and ptspct(nx[2]) >= strong_cut)
                # stakes: last 20 games of the team's season
                left = n_games[team] - i
                st = "early"
                if left <= 20 and gp.get(team, 0) >= 40:
                    c = conf(team)
                    table = sorted((pts.get(t, 0) for t in sched if conf(t) == c and t != team), reverse=True)
                    eighth = table[7] if len(table) >= 8 else 0
                    seventh = table[6] if len(table) >= 7 else 0
                    me = pts.get(team, 0)
                    gap = me - (eighth if me <= eighth else seventh)        # >0 ahead of the line, <0 behind
                    st = "out" if gap < -10 else ("safe" if gap > 10 else ("bubble" if abs(gap) <= 4 else "mid"))
                out[f"st_{side}"] = st
            rows.append(out)
            # top-3 forwards by season-to-date ice time: this game vs their own averages
            for side, team in (("h", r.home), ("a", r.away)):
                grp = sk_by_game.get((r.id, team))
                if grp is None:
                    continue
                cand = []
                for p in grp.itertuples():
                    h = pl_hist.get(p.pid, [])
                    if len(h) >= 20:
                        cand.append((np.mean([x[0] for x in h]), p, np.mean([x[1] for x in h])))
                for avg_toi, p, avg_p in sorted(cand, key=lambda z: -z[0])[:3]:
                    prow.append({"season": y, "st": out[f"st_{side}"], "toi_diff": p.toi - avg_toi, "p_diff": (p.g + p.a) - avg_p,
                                 "b2b": out[f"b2b_{side}"], "d34": out[f"d34_{side}"]})
                for p in grp.itertuples():
                    pl_hist.setdefault(p.pid, []).append((p.toi, p.g + p.a))
            # update standings after the game
            for t in (r.home, r.away):
                gp[t] = gp.get(t, 0) + 1
            win, lose = (r.home, r.away) if r.hs > r.as_ else (r.away, r.home)
            pts[win] = pts.get(win, 0) + 2
            if r.last != "REG":
                pts[lose] = pts.get(lose, 0) + 1
        prev = pd.concat([prev, games])
    d = pd.DataFrame(rows)
    d = d[d.gp_min >= 5].copy()
    d["res_mar"] = (d.hs - d.as_) - (d.mh - d.ma)
    d["res_tot"] = d.total - d.exp
    P = pd.DataFrame(prow)
    rep = {"seasons": seasons, "games": int(len(d)), "mean_abs_margin_res": float(d.res_mar.abs().mean())}

    # 1+2: busy stretches and look-ahead, fitted together, home-minus-away margin
    fac = ["b2b", "d34", "d46", "tmrw", "nxs"]
    lab = {"b2b": "back-to-back", "d34": "3 games in 4 nights", "d46": "4 games in 6 nights", "tmrw": "plays again tomorrow",
           "nxs": "next game soon vs a top-third team"}
    Xm = np.column_stack([d[f"{f}_h"] - d[f"{f}_a"] for f in fac]).astype(float)
    rep["margin_fit"] = ols(Xm, d.res_mar.values.astype(float), [lab[f] for f in fac])
    Xt = np.column_stack([d[f"{f}_h"] + d[f"{f}_a"] for f in fac]).astype(float)
    rep["total_fit"] = ols(Xt, d.res_tot.values.astype(float), [lab[f] for f in fac])
    # raw group means for the busy flags (team perspective: own goals minus opp, minus the model)
    team_rows = []
    for side, sgn in (("h", 1), ("a", -1)):
        for r in d.itertuples():
            team_rows.append({**{f: getattr(r, f"{f}_{side}") for f in fac}, "st": getattr(r, f"st_{side}"),
                              "opp_st": getattr(r, f"st_{'a' if side == 'h' else 'h'}"), "res": sgn * r.res_mar, "res_tot": r.res_tot})
    T = pd.DataFrame(team_rows)

    def grp(mask, name, target="res"):
        m = T[mask]
        return {"group": name, "n": int(len(m)), "mean": float(m[target].mean()) if len(m) else 0.0,
                "se": float(m[target].std() / math.sqrt(max(len(m), 1))) if len(m) > 1 else 0.0}
    rep["team_groups"] = [grp((T.b2b == 0) & (T.d34 == 0), "rested, not busy"), grp(T.b2b == 1, "back-to-back"),
                          grp((T.d34 == 1) & (T.b2b == 0), "3 in 4, not a back-to-back"), grp(T.d34 == 1, "3 in 4 (all)"),
                          grp(T.d46 == 1, "4 in 6"), grp(T.tmrw == 1, "plays again tomorrow"), grp(T.nxs == 1, "next game soon vs top-third team")]
    # 3: stakes (team perspective margin, and totals)
    stl = {"out": "out of the race (10+ pts behind)", "bubble": "on the bubble (within 4 pts)", "safe": "safe (10+ pts ahead)",
           "mid": "between", "early": "earlier in the season"}
    rep["stakes_margin"] = [grp(T.st == k, stl[k]) for k in ["early", "safe", "mid", "bubble", "out"]]
    rep["stakes_matchups"] = [grp((T.st == "out") & (T.opp_st == "bubble"), "out vs bubble team"),
                              grp((T.st == "out") & (T.opp_st == "safe"), "out vs safe team"),
                              grp((T.st == "safe") & (T.opp_st == "bubble"), "safe vs bubble team"),
                              grp((T.st == "bubble") & (T.opp_st == "bubble"), "bubble vs bubble")]
    gt = []
    for k in ["early", "safe", "bubble", "out"]:
        m = d[(d.st_h == k) | (d.st_a == k)]
        gt.append({"group": f"at least one team {stl[k]}", "n": int(len(m)), "mean": float(m.res_tot.mean()) if len(m) else 0.0,
                   "se": float(m.res_tot.std() / math.sqrt(max(len(m), 1))) if len(m) > 1 else 0.0})
    rep["stakes_total"] = gt
    # top forwards
    tf = []
    if len(P):
        for k in ["early", "safe", "mid", "bubble", "out"]:
            m = P[P.st == k]
            if len(m) > 1:
                tf.append({"group": stl[k], "n": int(len(m)), "toi": float(m.toi_diff.mean()), "toi_se": float(m.toi_diff.std() / math.sqrt(len(m))),
                           "pts": float(m.p_diff.mean()), "pts_se": float(m.p_diff.std() / math.sqrt(len(m)))})
        for name, mask in (("back-to-back", P.b2b == 1), ("3 in 4", P.d34 == 1), ("neither", (P.b2b == 0) & (P.d34 == 0))):
            m = P[mask]
            if len(m) > 1:
                tf.append({"group": f"(all season) {name}", "n": int(len(m)), "toi": float(m.toi_diff.mean()), "toi_se": float(m.toi_diff.std() / math.sqrt(len(m))),
                           "pts": float(m.p_diff.mean()), "pts_se": float(m.p_diff.std() / math.sqrt(len(m)))})
    rep["top_forwards"] = tf
    (out_dir / "schedule.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "schedule.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    def fit(rows, what):
        L = [f"| Factor | Games | {what} | ± | t |", "|---|---|---|---|---|"]
        L += [f"| {x['factor']} | {x['n']:,} | {x['coef']:+.3f} | {x['se']:.3f} | {x['t']:+.1f} |" for x in rows]
        return L

    def tb(rows, what):
        L = [f"| Group | Team-games | {what} | ± |", "|---|---|---|---|"]
        L += [f"| {x['group']} | {x['n']:,} | {x['mean']:+.3f} | {x['se']:.3f} |" for x in rows]
        return L
    L = [f"# Schedule & stakes: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}",
         f"Games: {r['games']:,} · average miss on goal margin: {r['mean_abs_margin_res']:.2f} goals", "",
         "## Busy stretches and looking ahead, fitted together",
         "Goal margin vs the model, per game, for the team with the flag (home minus away difference in flags):", ""]
    L += fit(r["margin_fit"], "Goals vs model") + ["", "Total goals vs the model (per team in the game with the flag):", ""]
    L += fit(r["total_fit"], "Total goals vs model") + ["", "Raw groups (team's goal margin vs the model):", ""]
    L += tb(r["team_groups"], "Margin vs model") + ["", "## Late-season stakes (team's last 20 games)", ""]
    L += tb(r["stakes_margin"], "Margin vs model") + [""] + tb(r["stakes_matchups"], "Margin vs model") + [""]
    L += tb(r["stakes_total"], "Total goals vs model") + ["", "## Top-3 forwards (by ice time so far): this game vs their own average", "",
         "| Group | Player-games | Ice time diff (min) | ± | Points diff | ± |", "|---|---|---|---|---|---|"]
    L += [f"| {x['group']} | {x['n']:,} | {x['toi']:+.2f} | {x['toi_se']:.2f} | {x['pts']:+.3f} | {x['pts_se']:.3f} |" for x in r["top_forwards"]]
    return "\n".join(L) + "\n"
