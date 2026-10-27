"""Season simulation backtest (does not touch the website).

1) Calibration: from several dates in past seasons, simulate the rest of the season and check how often teams
   given X% to make the playoffs (and to win their division) really did. Fits the strength-uncertainty sigma.
2) Game importance: from weekly dates after New Year, the playoff-chance swing of each game for both teams, and
   whether the team for whom the game mattered more won more often than the model expected.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .backtest_totals import team_prior
from .season import outcome_probs, ratings, simulate, shrink

SIGMAS = [0.15, 0.3]
RATING = [(1.0, 20.0), (0.7, 20.0), (0.5, 20.0), (0.7, 40.0), (0.5, 40.0), (0.35, 40.0)]   # (prior_r, k)
CHECKPOINTS = [(0, 11, 15), (0, 12, 15), (1, 1, 20), (1, 2, 25), (1, 3, 25)]


def state_at(games: pd.DataFrame, cut: str, prior: dict, prev_lg: float, prior_r: float = 1.0, k: float = 20.0):
    past = games[games.date < cut]
    gp, gf, ga, sf, sa = {}, {}, {}, {}, {}
    for r in past.itertuples():
        for t, f, a, s1, s2 in ((r.home, r.hs, r.as_, r.hsog, r.asog), (r.away, r.as_, r.hs, r.asog, r.hsog)):
            gp[t] = gp.get(t, 0) + 1; gf[t] = gf.get(t, 0) + f; ga[t] = ga.get(t, 0) + a
            sf[t] = sf.get(t, 0) + (s1 or 0); sa[t] = sa.get(t, 0) + (s2 or 0)
    tg, ts = sum(gf.values()), sum(sf.values())
    lg_sh = tg / ts if ts else 0.095
    lg = shrink(tg, sum(gp.values()), prev_lg, 400)
    stats = {t: (gp[t], gf[t], ga[t], sf[t] * lg_sh, sa[t] * lg_sh) for t in gp}
    rat = ratings(stats, prior, lg, k=k, prior_r=prior_r)
    played = [(r.home, r.away, r.hs, r.as_, r.last) for r in past.itertuples()]
    return rat, played, lg


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cal = {(r, s): [] for r in RATING for s in SIGMAS}
    gl = {r: [] for r in RATING}
    imp_rows = []
    for season in sorted(seasons):
        games, _, _ = season_boxscores(src, season, cfg["workers"], log)
        games = games.copy()
        games["date"] = games.date.astype(str).str[:10]
        tp = team_prior(src, season)
        prior = {t: (v["xgf"], v["xga"]) for t, v in tp.items()}
        prev_lg = float(np.mean([v[0] for v in prior.values()])) if prior else 3.05
        teams = sorted(set(games.home) | set(games.away))
        # what really happened: final standings through the same playoff rules (no games left, no uncertainty)
        rat_all, played_all, _ = state_at(games, "9999", prior, prev_lg)
        fin = simulate(teams, {t: rat_all.get(t, (3, 3, 82)) for t in teams}, played_all, [], cfg, n=50, sigma0=0.0)
        actual_po = {t: fin["teams"][t]["playoffs"] >= 0.5 for t in teams}
        actual_div = {t: fin["teams"][t]["div"] >= 0.5 for t in teams}
        for yo, m, d in CHECKPOINTS + [(0, 10, 31)]:
            cut = dt.date(season + yo, m, d).isoformat()
            for rc in RATING:
                rat, played, lg = state_at(games, cut, prior, prev_lg, prior_r=rc[0], k=rc[1])
                rat = {t: rat.get(t, (prev_lg, prev_lg, 0)) for t in teams}
                remdf = games[games.date >= cut]
                rem = [(int(r.id), r.home, r.away) for r in remdf.itertuples()]
                # game level: home win chance for the next 30 days vs what happened
                lgm = float(np.mean([rat[t][0] for t in teams]))
                nxt = (dt.date.fromisoformat(cut) + dt.timedelta(days=30)).isoformat()
                for r in remdf[remdf.date < nxt].itertuples():
                    mh = rat[r.home][0] * rat[r.away][1] / lgm * cfg.get("home_adv", 1.05)
                    ma = rat[r.away][0] * rat[r.home][1] / lgm / cfg.get("home_adv", 1.05)
                    pH, pT, pA = outcome_probs(mh, ma, cfg.get("tie_boost", 1.0))
                    gl[rc].append((pH + pT * pH / (pH + pA), float(r.hs > r.as_), cut[5:]))
                for sg in SIGMAS:
                    res = simulate(teams, rat, played, rem, cfg, n=2000, sigma0=sg, seed=season * 100 + m)
                    for t in teams:
                        cal[(rc, sg)].append({"season": season, "cut": cut, "team": t, "p": res["teams"][t]["playoffs"],
                                              "pd": res["teams"][t]["div"], "y": actual_po[t], "yd": actual_div[t],
                                              "cup": res["teams"][t]["cup"]})
        # importance: weekly from 1 Jan, games in the following 7 days
        best_guess = 0.15
        day = dt.date(season + 1, 1, 1)
        end = dt.date.fromisoformat(games.date.max())
        while day < end:
            cut, nxt = day.isoformat(), (day + dt.timedelta(days=7)).isoformat()
            rat, played, lg = state_at(games, cut, prior, prev_lg)
            rat = {t: rat.get(t, (prev_lg, prev_lg, 0)) for t in teams}
            rem_df = games[games.date >= cut]
            week = rem_df[rem_df.date < nxt]
            rem = [(int(r.id), r.home, r.away) for r in rem_df.itertuples()]
            res = simulate(teams, rat, played, rem, cfg, n=3000, sigma0=best_guess, seed=int(day.strftime("%Y%m%d")),
                           track={int(i) for i in week.id})
            lgm = float(np.mean([rat[t][0] for t in teams]))
            for r in week.itertuples():
                gi = res["games"].get(int(r.id))
                if not gi:
                    continue
                sw = {t: (gi[t]["win"] - gi[t]["lose"]) if gi[t]["win"] is not None and gi[t]["lose"] is not None else 0.0 for t in (r.home, r.away)}
                mh = rat[r.home][0] * rat[r.away][1] / lgm * cfg.get("home_adv", 1.05)
                ma = rat[r.away][0] * rat[r.home][1] / lgm / cfg.get("home_adv", 1.05)
                pH, pT, pA = outcome_probs(mh, ma, cfg.get("tie_boost", 1.0))
                imp_rows.append({"season": season, "p": pH + pT * pH / (pH + pA), "y": float(r.hs > r.as_),
                                 "sh": sw[r.home], "sa": sw[r.away]})
            day += dt.timedelta(days=7)
    # calibration summary
    rep = {"seasons": seasons, "sigma": {}, "games": {}}
    for rc, rows in gl.items():
        a = np.array([(p, y) for p, y, _ in rows]); p = np.clip(a[:, 0], 1e-3, 1 - 1e-3); y = a[:, 1]
        rep["games"][f"r{rc[0]}_k{rc[1]:.0f}"] = {"n": int(len(a)), "logloss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
                                                  "brier": float(((p - y) ** 2).mean()), "spread": float(np.std(p)),
                                                  "fav_pred": float(p[p >= 0.6].mean()) if (p >= 0.6).any() else None, "fav_act": float(y[p >= 0.6].mean()) if (p >= 0.6).any() else None}
    for key, rows in cal.items():
        rc, sg = key
        sg = f"r{rc[0]}_k{rc[1]:.0f}_s{sg}"
        d = pd.DataFrame(rows)
        p = d.p.clip(1e-3, 1 - 1e-3); y = d.y.astype(float)
        pdv = d.pd.clip(1e-3, 1 - 1e-3); yd = d.yd.astype(float)
        bins = pd.cut(d.p, [-0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 1.01])
        rep["sigma"][str(sg)] = {"brier": float(((p - y) ** 2).mean()), "logloss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
                                 "brier_div": float(((pdv - yd) ** 2).mean()),
                                 "bins": [{"bin": str(k), "n": int(len(g)), "pred": float(g.p.mean()), "act": float(g.y.mean())} for k, g in d.groupby(bins, observed=True)],
                                 "by_cut": {c: float(((g.p - g.y.astype(float)) ** 2).mean()) for c, g in d.assign(c=d.cut.str[5:]).groupby("c")},
                                 "top_cup": float(d.groupby(["season", "cut"]).cup.max().mean())}
    rep["best_sigma"] = min(rep["sigma"], key=lambda k: rep["sigma"][k]["logloss"])
    # importance test: logistic with the model as offset + beta x (home swing - away swing)
    D = pd.DataFrame(imp_rows)
    rep["imp_n"] = int(len(D))
    if len(D):
        off = np.log(D.p / (1 - D.p)).values; x = (D.sh - D.sa).values; y = D.y.values
        b = 0.0
        for _ in range(30):
            pr = 1 / (1 + np.exp(-(off + b * x)))
            g_ = ((y - pr) * x).sum(); h_ = -(pr * (1 - pr) * x * x).sum()
            if h_ == 0:
                break
            b -= g_ / h_
        pr = 1 / (1 + np.exp(-(off + b * x)))
        se = 1 / math.sqrt(max((pr * (1 - pr) * x * x).sum(), 1e-9))
        rep["imp_beta"], rep["imp_se"] = float(b), float(se)
        diff = D.sh - D.sa
        grp = pd.cut(diff, [-1, -0.07, -0.03, 0.03, 0.07, 1], labels=["away much more", "away more", "even", "home more", "home much more"])
        rep["imp_bins"] = [{"group": str(k), "n": int(len(g)), "pred": float(g.p.mean()), "act": float(g.y.mean())} for k, g in D.groupby(grp, observed=True)]
        mx = np.maximum(D.sh, D.sa)
        rep["imp_levels"] = {lvl: int((mx >= lo).sum()) for lvl, lo in (("medium", 0.03), ("high", 0.06), ("critical", 0.10))}
        rep["imp_quantiles"] = {q: float(np.quantile(mx, q / 100)) for q in (25, 50, 75, 90, 97)}
    (out_dir / "season.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "season.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# Season simulation backtest: {r['seasons']}", "", "## Game level (next 30 days after each date): home win chance vs result",
         "| rating (prior pull r, k games) | n | Log loss | Brier | spread of chances | favourites 60%+: predicted / won |", "|---|---|---|---|---|---|"]
    for k_, v in r["games"].items():
        L.append(f"| {k_} | {v['n']:,} | {v['logloss']:.4f} | {v['brier']:.4f} | {v['spread']:.3f} | {(v['fav_pred'] or 0):.1%} / {(v['fav_act'] or 0):.1%} |")
    L += ["", "## Playoff chances by rating and sigma", "| setting | Brier playoffs | Log loss | Brier division | avg top Cup chance | by date (Brier) |", "|---|---|---|---|---|---|"]
    for sg, v in r["sigma"].items():
        L.append(f"| {sg} | {v['brier']:.4f} | {v['logloss']:.4f} | {v['brier_div']:.4f} | {v['top_cup']:.1%} | " + " · ".join(f"{c}: {b:.3f}" for c, b in v["by_cut"].items()) + " |")
    b = r["sigma"][r["best_sigma"]]
    L += ["", f"Best sigma: {r['best_sigma']}", "", "| Predicted | n | Avg predicted | Made playoffs |", "|---|---|---|---|"]
    L += [f"| {x['bin']} | {x['n']} | {x['pred']:.1%} | {x['act']:.1%} |" for x in b["bins"]]
    if r.get("imp_n"):
        L += ["", f"## Game importance (games after 1 Jan, n={r['imp_n']:,})",
              f"Effect of (home swing - away swing) on home win, beyond the model: beta {r['imp_beta']:+.3f} (se {r['imp_se']:.3f}, t {r['imp_beta'] / r['imp_se']:+.1f})",
              f"Games with at least one team at level: {r['imp_levels']} · biggest swing per game, quantiles: {r.get('imp_quantiles')}", "",
              "| Who it mattered more for | n | Model: home win | Actual home win |", "|---|---|---|---|"]
        L += [f"| {x['group']} | {x['n']} | {x['pred']:.1%} | {x['act']:.1%} |" for x in r["imp_bins"]]
    return "\n".join(L) + "\n"
