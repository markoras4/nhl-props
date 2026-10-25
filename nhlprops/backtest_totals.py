"""Game-totals backtest: replay past seasons game by game (does not touch the website).

For each game, using only earlier information:
  * model expected goals for each team (attack x opponent defence x goalie x home ice)
  * historical over/under rates (team season-to-date, last 3 seasons, head-to-head)
  * each team's recent scoring run (total goals in its last 5 games)
Questions answered:
  1. Can the model separate low-scoring from high-scoring games?
  2. After a run of high- or low-scoring games, does the next game come in lower/higher than the model expected?
  3. Does blending in over/under history improve the totals?
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames
from .grade import summarize
from .model import _hours, _num, _sit

LINES = (5.5, 6.5)


def shrink(x, n, prior, k):
    return (x + k * prior) / (n + k)


def pois(lam, n=16):
    out = np.zeros(n)
    out[0] = math.exp(-lam)
    for i in range(1, n):
        out[i] = out[i - 1] * lam / i
    return out


def totals_dist(mh, ma, tie_f=1.0):
    ph, pa = pois(mh * 0.985), pois(ma * 0.985)
    j = np.outer(ph, pa)
    if tie_f != 1.0:
        j[np.diag_indices(16)] *= tie_f
        j /= j.sum()
    tot = np.zeros(34)
    hw = tie = 0.0
    for i in range(16):
        for k in range(16):
            t = i + k + (1 if i == k else 0)
            tot[t] += j[i, k]
            if i == k:
                tie += j[i, k]
            elif i > k:
                hw += j[i, k]
    po = {L: float(tot[int(L) + 1:].sum()) for L in LINES}
    po["res"] = (hw, tie, 1 - hw - tie)          # regulation: home win, tie, away win
    return po, float((tot * np.arange(34)).sum())


def team_prior(src, season):
    d = src.moneypuck("teams", season - 1, False)
    if d is None:
        return {}
    a = d[_sit(d) == "all"]
    gp = _num(a, "games_played").clip(lower=1)
    return {t: {"xgf": float(xf / g), "xga": float(xa / g)}
            for t, xf, xa, g in zip(a.team.astype(str), _num(a, "xGoalsFor"), _num(a, "xGoalsAgainst"), gp)}


def season_rows(src, season, cfg, prev_games: pd.DataFrame, log=print) -> pd.DataFrame:
    games, sk, go = season_boxscores(src, season, cfg["workers"], log)
    pri = prior_frames(src, season)
    tp = team_prior(src, season)
    G = pri["go"]
    lg_ratio = float(G.ga.sum() / G.xga.sum()) if len(G) and G.xga.sum() else 1.0
    gr = G.groupby("pid")[["ga", "xga"]].sum() if len(G) else pd.DataFrame(columns=["ga", "xga"])
    gratio = (shrink(gr.ga, gr.xga, lg_ratio, cfg["k_goalie_xga"]) / lg_ratio).to_dict() if len(gr) else {}
    starters = go[go.starter].drop_duplicates(["gid", "team"]).set_index(["gid", "team"]).pid.to_dict()

    lg_team_gpg = float((games.hs.sum() + games.as_.sum()) / (2 * len(games)))     # season level (known after the fact,
    prev_tot = prev_games.hs.astype(float) + prev_games.as_.astype(float) if len(prev_games) else pd.Series(dtype=float)
    lg_prior_gpg = float(prev_tot.mean() / 2) if len(prev_tot) else 3.05            # ...so we use last season + to-date)
    xg_prior_lg = np.mean([v["xgf"] for v in tp.values()]) if tp else lg_prior_gpg

    rows, hist = [], {}          # hist[team] = list of (gf, ga, sf, sa, total)
    if len(prev_games) and "hsog" in prev_games:
        pg_ = prev_games.dropna(subset=["hsog", "asog"])
        sh_prior = float((pg_.hs.sum() + pg_.as_.sum()) / max(pg_.hsog.sum() + pg_.asog.sum(), 1))
    else:
        sh_prior = 0.10
    lg_goals, lg_shots = 0.0, 0.0
    h2h = {}
    lg_sum, lg_n, lg_over = 0.0, 0, {L: 0 for L in LINES}
    prev_team_over = {}
    if len(prev_games):
        for L in LINES:
            for side in ("home", "away"):
                pass
    # last-3-seasons over rates per team (from earlier seasons, passed in)
    prior_over = {}
    if len(prev_games):
        pg = prev_games.assign(total=prev_tot)
        for t in set(pg.home) | set(pg.away):
            m = pg[(pg.home == t) | (pg.away == t)]
            prior_over[t] = {L: float((m.total > L).mean()) for L in LINES}
        for (a, b), m in pg.groupby([pg[["home", "away"]].min(axis=1), pg[["home", "away"]].max(axis=1)]):
            h2h[(a, b)] = list(m.total.values[-6:])
    lg_hist_over = {L: float((prev_tot > L).mean()) if len(prev_tot) else 0.5 for L in LINES}

    for g in games.sort_values(["date", "id"]).itertuples():
        H, A = g.home, g.away
        lg_now = shrink(lg_sum / 2, lg_n, lg_prior_gpg, 200) if lg_n else lg_prior_gpg   # league goals per team-game so far
        lg_sh = shrink(lg_goals, lg_shots, sh_prior, 3000)   # league goals per shot so far (empty-net goals included)
        def rating(t):
            h = hist.get(t, [])
            n = len(h)
            gf = sum(x[0] for x in h); ga = sum(x[1] for x in h)
            sf = sum(x[2] or 0 for x in h); sa = sum(x[3] or 0 for x in h)
            p = tp.get(t, {"xgf": xg_prior_lg, "xga": xg_prior_lg})
            scale = lg_now / xg_prior_lg if xg_prior_lg else 1
            att_obs = 0.5 * gf + 0.5 * sf * lg_sh
            def_obs = 0.5 * ga + 0.5 * sa * lg_sh
            att = shrink(att_obs, n, p["xgf"] * scale, 20)
            dfn = shrink(def_obs, n, p["xga"] * scale, 20)
            return att, dfn, h
        ah, dh, hh_ = rating(H)
        aa, da, ha_ = rating(A)
        gh = gratio.get(starters.get((g.id, A)), 1.0)   # goalie facing the home attack
        ga_ = gratio.get(starters.get((g.id, H)), 1.0)
        mh = ah * da / lg_now * cfg["home_adv"] * gh
        ma = aa * dh / lg_now / cfg["home_adv"] * ga_
        po, exp_total = totals_dist(mh, ma)

        # history signals
        def team_over(h, t, L):
            n = len(h); o = sum(1 for x in h if x[4] > L)
            prior = 0.5 * prior_over.get(t, lg_hist_over)[L] + 0.5 * (lg_over[L] / lg_n if lg_n else lg_hist_over[L])
            return shrink(o, n, prior, cfg["k_team_ou_games"])
        key = (min(H, A), max(H, A))
        hm = h2h.get(key, [])
        hist_p = {L: float(np.mean([team_over(hh_, H, L), team_over(ha_, A, L),
                                    prior_over.get(H, lg_hist_over)[L], prior_over.get(A, lg_hist_over)[L],
                                    shrink(sum(1 for x in hm if x > L), len(hm), lg_hist_over[L], cfg["k_h2h_games"])]))
                  for L in LINES}
        recent = [x[4] for x in hh_[-5:]] + [x[4] for x in ha_[-5:]]
        def state(h):
            n = len(h)
            since_tie = next((i for i, x in enumerate(reversed(h)) if x[5]), n)
            over_run = next((i for i, x in enumerate(reversed(h)) if x[4] <= 6), n)    # games in a row with 7+ goals
            under_run = next((i for i, x in enumerate(reversed(h)) if x[4] >= 6), n)   # games in a row with 5 or fewer
            hgames = [x for x in h if x[6]]; agames = [x for x in h if not x[6]]
            return {"n": n, "since_tie": since_tie, "ties": sum(x[5] for x in h), "over_run": over_run,
                    "under_run": under_run,
                    "home_n": len(hgames), "home_w": sum(1 for x in hgames if not x[5] and x[0] > x[1]),
                    "away_n": len(agames), "away_w": sum(1 for x in agames if not x[5] and x[0] > x[1])}
        sH, sA = state(hh_), state(ha_)
        tie_act = g.last != "REG"
        res = "T" if tie_act else ("H" if g.hs > g.as_ else "A")
        rows.append({"season": season, "id": g.id, "date": g.date, "home": H, "away": A,
                     "total": int(g.hs + g.as_), "exp": exp_total, "mh": mh, "ma": ma,
                     **{f"p{L}": po[L] for L in LINES}, **{f"h{L}": hist_p[L] for L in LINES},
                     "recent5": float(np.mean(recent)) if len(recent) >= 8 else np.nan,
                     "recent_lg": 2 * lg_now, "gp_min": min(len(hh_), len(ha_)),
                     "pH": po["res"][0], "pT": po["res"][1], "pA": po["res"][2], "res": res,
                     **{f"H_{k}": v for k, v in sH.items()}, **{f"A_{k}": v for k, v in sA.items()}})
        # update after the game
        hist.setdefault(H, []).append((g.hs, g.as_, g.hsog, g.asog, g.hs + g.as_, g.last != "REG", True))
        hist.setdefault(A, []).append((g.as_, g.hs, g.asog, g.hsog, g.hs + g.as_, g.last != "REG", False))
        h2h.setdefault(key, []).append(g.hs + g.as_)
        lg_sum += g.hs + g.as_; lg_n += 1
        if g.hsog is not None and g.asog is not None and not (pd.isna(g.hsog) or pd.isna(g.asog)):
            lg_goals += g.hs + g.as_; lg_shots += g.hsog + g.asog
        for L in LINES:
            lg_over[L] += int(g.hs + g.as_ > L)
    return pd.DataFrame(rows), games


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames, prev = [], pd.DataFrame()
    first = min(seasons)
    # last 3 seasons of results before the first test season, for over/under history
    for y in range(first - 3, first):
        g, _, _ = season_boxscores(src, y, cfg["workers"], log)
        prev = pd.concat([prev, g])
    for y in sorted(seasons):
        df, games = season_rows(src, y, cfg, prev[prev.season >= y - 3] if len(prev) else prev, log)
        frames.append(df)
        prev = pd.concat([prev, games])
    d_all = pd.concat(frames)
    d = d_all[d_all.gp_min >= 5].copy()
    rep = {"seasons": seasons, "games": int(len(d))}

    # 1. can the model separate low and high scoring games?
    bins = [0, 5.6, 5.85, 6.05, 6.25, 6.5, 99]
    d["bucket"] = pd.cut(d.exp, bins)
    rep["by_model_total"] = [{"range": f"{b.left:.2f}-{b.right:.2f}" if b.right < 99 else f"{b.left:.2f}+",
                              "games": int(len(m)), "model_avg": float(m.exp.mean()), "actual_avg": float(m.total.mean()),
                              "model_over55": float(m["p5.5"].mean()), "actual_over55": float((m.total > 5.5).mean())}
                             for b, m in d.groupby("bucket", observed=True)]
    # 2. recent scoring runs
    d["run"] = d.recent5 - d.recent_lg
    rb = [-99, -1.5, -0.75, 0.75, 1.5, 99]
    names = ["very low-scoring run", "low-scoring run", "normal", "high-scoring run", "very high-scoring run"]
    d["runb"] = pd.cut(d.run, rb, labels=names)
    rep["by_recent_run"] = [{"run": str(k), "games": int(len(m)), "recent_avg": float(m.recent5.mean()),
                             "model_avg": float(m.exp.mean()), "actual_avg": float(m.total.mean()),
                             "actual_minus_model": float((m.total - m.exp).mean()),
                             "std_error": float((m.total - m.exp).std() / math.sqrt(max(len(m), 1)))}
                            for k, m in d.groupby("runb", observed=True)]
    # naive "recent form" forecast vs model
    rep["recent_form_vs_model"] = {"model_mae": float((d.total - d.exp).abs().mean()),
                                   "recent_form_mae": float((d.total - d.recent5).abs().mean()),
                                   "season_league_avg_mae": float((d.total - d.recent_lg).abs().mean())}
    # 3. history blends + calibration
    for L in LINES:
        y = (d.total > L).astype(int)
        res = []
        for w in np.arange(0, 1.01, 0.1):
            p = (1 - w) * d[f"p{L}"] + w * d[f"h{L}"]
            res.append({"w": round(float(w), 1), "brier": float(((p - y) ** 2).mean())})
        rep[f"blend_{L}"] = res
        rep[f"model_{L}"] = summarize(list(zip(d[f"p{L}"].clip(1e-4, 1 - 1e-4), y)))
        rep[f"hist_{L}"] = summarize(list(zip(d[f"h{L}"].clip(1e-4, 1 - 1e-4), y)))
    rep.update(results_tests(d_all, d))
    (out_dir / "totals.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "totals.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def _rate_rows(m, key, act, pred):
    """grouped actual rate vs model rate with standard error"""
    out = []
    for k, x in m.groupby(key, observed=True):
        a = x[act].astype(float)
        out.append({"group": str(k), "n": int(len(x)), "actual": float(a.mean()), "model": float(x[pred].mean()),
                    "diff": float((a - x[pred]).mean()), "se": float(a.std() / math.sqrt(max(len(x), 1)))})
    return out


def _qlabel(series, q=5):
    b = pd.qcut(series, q, duplicates="drop")
    return b.map(lambda iv: f"{max(iv.left, 0):.0%}-{iv.right:.0%}")


def results_tests(d_all, d):
    r = {}
    # 4. league home / tie / away by season (regulation result)
    r["league_1x2"] = [{"season": int(y), "games": int(len(m)), "home": float((m.res == "H").mean()),
                        "tie": float((m.res == "T").mean()), "away": float((m.res == "A").mean()),
                        "model_home": float(m.pH.mean()), "model_tie": float(m.pT.mean()), "model_away": float(m.pA.mean()),
                        "goals": float(m.total.mean())}
                       for y, m in d_all.groupby("season")]
    # 5. do early-season team rates hold up? first 18 games (~6 weeks) vs the rest of the season
    split = []
    for (y, t), _ in d_all.groupby(["season", "home"]):
        m = d_all[(d_all.season == y) & ((d_all.home == t) | (d_all.away == t))].sort_values("date")
        first, rest = m.iloc[:18], m.iloc[18:]
        if len(rest) < 30:
            continue
        def rates(x):
            hm = x[x.home == t]; aw = x[x.away == t]
            return {"home_win": float((hm.res == "H").mean()), "away_win": float((aw.res == "A").mean()),
                    "tie": float((x.res == "T").mean()), "goals": float(x.total.mean())}
        split.append({"season": y, "team": t, **{f"f_{k}": v for k, v in rates(first).items()},
                      **{f"r_{k}": v for k, v in rates(rest).items()}})
    sp = pd.DataFrame(split)
    persist = {}
    for k in ("home_win", "away_win", "tie", "goals"):
        f, rr = sp[f"f_{k}"], sp[f"r_{k}"]
        slope = float(np.cov(f, rr)[0, 1] / f.var()) if f.var() > 0 else 0.0
        hi, lo = sp[f >= f.quantile(0.8)], sp[f <= f.quantile(0.2)]
        persist[k] = {"teams": int(len(sp)), "league_rest": float(rr.mean()), "carry_over": slope,
                      "top20_first": float(hi[f"f_{k}"].mean()), "top20_rest": float(hi[f"r_{k}"].mean()),
                      "bottom20_first": float(lo[f"f_{k}"].mean()), "bottom20_rest": float(lo[f"r_{k}"].mean())}
    r["early_season_persistence"] = persist
    # team-level rows (each game counted once per team)
    tm = pd.concat([pd.DataFrame({"since_tie": d[f"{s}_since_tie"], "over_run": d[f"{s}_over_run"],
                                  "under_run": d[f"{s}_under_run"], "ties": d[f"{s}_ties"], "n": d[f"{s}_n"],
                                  "tie": (d.res == "T").astype(int), "pT": d.pT, "total": d.total, "exp": d.exp,
                                  "o65": (d.total > 6.5).astype(int), "u55": (d.total < 5.5).astype(int),
                                  "p65": d["p6.5"], "p55u": 1 - d["p5.5"]})
                    for s in ("H", "A")])
    tm["tie_bucket"] = pd.cut(tm.since_tie, [-1, 0, 2, 5, 8, 11, 999],
                              labels=["tied last game", "1-2 games", "3-5", "6-8", "9-11", "12+ games"])
    r["games_since_tie"] = _rate_rows(tm, "tie_bucket", "tie", "pT")
    tm["tie_rate"] = tm.ties / tm.n.clip(lower=1)
    tm["tie_rate_b"] = pd.cut(tm.tie_rate, [-0.01, 0.12, 0.18, 0.26, 0.32, 1.0],
                              labels=["under 12%", "12-18%", "18-26%", "26-32%", "over 32%"])
    r["tie_rate_to_date"] = _rate_rows(tm[tm.n >= 15], "tie_rate_b", "tie", "pT")
    tm["over_b"] = pd.cut(tm.over_run, [-1, 0, 1, 2, 3, 99], labels=["0", "1", "2", "3", "4+"])
    tm["under_b"] = pd.cut(tm.under_run, [-1, 0, 1, 2, 3, 99], labels=["0", "1", "2", "3", "4+"])
    r["after_over_run"] = _rate_rows(tm, "over_b", "o65", "p65")
    r["after_under_run"] = _rate_rows(tm, "under_b", "u55", "p55u")
    r["over_run_goals"] = _rate_rows(tm, "over_b", "total", "exp")
    r["under_run_goals"] = _rate_rows(tm, "under_b", "total", "exp")
    # 8. home team's home record / away team's road record so far vs what the model expected
    hr = d[d.H_home_n >= 6].copy()
    hr["hw_rate"] = hr.H_home_w / hr.H_home_n
    hr["hw_b"] = _qlabel(hr.hw_rate)
    hr["home_won"] = (hr.res == "H").astype(int)
    r["home_record_to_date"] = _rate_rows(hr, "hw_b", "home_won", "pH")
    ar = d[d.A_away_n >= 6].copy()
    ar["aw_rate"] = ar.A_away_w / ar.A_away_n
    ar["aw_b"] = _qlabel(ar.aw_rate)
    ar["away_won"] = (ar.res == "A").astype(int)
    r["away_record_to_date"] = _rate_rows(ar, "aw_b", "away_won", "pA")
    # 9. calibration slope of the projected total, and blends after compressing it
    x = d.exp - d.exp.mean()
    slope = float((x * (d.total - d.total.mean())).sum() / (x ** 2).sum())
    r["total_slope"] = slope
    # 10. tie boost x total compression grid
    lg = d.exp.mean()
    y = {L: (d.total > L).astype(int).values for L in LINES}
    res_i = d.res.map({"H": 0, "T": 1, "A": 2}).values
    grid = []
    for tf in (1.0, 1.2, 1.35, 1.5, 1.65):
        for c in (1.0, 0.85, 0.7):
            P = {L: [] for L in LINES}; R = []; E = []
            for mh, ma, ex in zip(d.mh.values, d.ma.values, d.exp.values):
                sc = (lg + c * (ex - lg)) / ex
                po, e = totals_dist(mh * sc, ma * sc, tf)
                for L in LINES:
                    P[L].append(po[L])
                R.append(po["res"]); E.append(e)
            R = np.clip(np.array(R), 1e-4, 1)
            row = {"tie_f": tf, "c": c, "tie": float(R[:, 1].mean()), "exp": float(np.mean(E)),
                   "logloss_1x2": float(-np.log(R[np.arange(len(R)), res_i]).mean())}
            for L in LINES:
                row[f"brier_{L}"] = float(((np.array(P[L]) - y[L]) ** 2).mean())
            grid.append(row)
    r["tie_grid"] = grid
    return r


def to_md(r):
    L = [f"# Game totals backtest: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}", f"Games: {r['games']:,}", "",
         "## 1. Model projected total vs what happened", "",
         "| Model range | Games | Model avg | Actual avg | Model over 5.5 | Actual over 5.5 |", "|---|---|---|---|---|---|"]
    for x in r["by_model_total"]:
        L.append(f"| {x['range']} | {x['games']} | {x['model_avg']:.2f} | {x['actual_avg']:.2f} | {x['model_over55'] * 100:.1f}% | {x['actual_over55'] * 100:.1f}% |")
    L += ["", "## 2. After a scoring run (both teams' last 5 games vs league)", "",
          "| Run | Games | Their last-5 avg | Model avg | Actual avg | Actual − model | ± |", "|---|---|---|---|---|---|---|"]
    for x in r["by_recent_run"]:
        L.append(f"| {x['run']} | {x['games']} | {x['recent_avg']:.2f} | {x['model_avg']:.2f} | {x['actual_avg']:.2f} | {x['actual_minus_model']:+.2f} | {x['std_error']:.2f} |")
    f = r["recent_form_vs_model"]
    L += ["", f"Average miss (goals): model {f['model_mae']:.3f} · recent form {f['recent_form_mae']:.3f} · league average {f['season_league_avg_mae']:.3f}", ""]
    for Lx in LINES:
        best = min(r[f"blend_{Lx}"], key=lambda x: x["brier"])
        m, h = r[f"model_{Lx}"], r[f"hist_{Lx}"]
        L += [f"## 3. Over {Lx}: history blend", "",
              f"Model Brier {m['brier']:.4f} (skill {m['skill'] * 100:+.1f}%) · history only {h['brier']:.4f} (skill {h['skill'] * 100:+.1f}%) · best weight {best['w'] * 100:.0f}% (Brier {best['brier']:.4f})", "",
              " · ".join(f"{x['w'] * 100:.0f}%: {x['brier']:.4f}" for x in r[f"blend_{Lx}"]), ""]
    L += ["## 4. League results by season (after 60 minutes)", "",
          "| Season | Games | Home win | Tie | Away win | Model home | Model tie | Model away | Goals/game |", "|---|---|---|---|---|---|---|---|---|"]
    for x in r["league_1x2"]:
        L.append(f"| {x['season']} | {x['games']} | {x['home']:.1%} | {x['tie']:.1%} | {x['away']:.1%} | {x['model_home']:.1%} | {x['model_tie']:.1%} | {x['model_away']:.1%} | {x['goals']:.2f} |")
    L += ["", "## 5. First 18 games vs rest of season (team level)", "",
          "| Stat | Teams | Carry-over | Top 20% first | → rest | Bottom 20% first | → rest | League rest |", "|---|---|---|---|---|---|---|---|"]
    for k, x in r["early_season_persistence"].items():
        f = (lambda v: f"{v:.2f}") if k == "goals" else (lambda v: f"{v:.1%}")
        L.append(f"| {k} | {x['teams']} | {x['carry_over']:.2f} | {f(x['top20_first'])} | {f(x['top20_rest'])} | {f(x['bottom20_first'])} | {f(x['bottom20_rest'])} | {f(x['league_rest'])} |")
    def tbl(title, rows, pct=True):
        f = (lambda v: f"{v:.1%}") if pct else (lambda v: f"{v:.2f}")
        out = ["", f"## {title}", "", "| Group | Team-games | Actual | Model | Actual − model | ± |", "|---|---|---|---|---|---|"]
        for x in rows:
            out.append(f"| {x['group']} | {x['n']} | {f(x['actual'])} | {f(x['model'])} | {x['diff'] * (100 if pct else 1):+.{1 if pct else 2}f}{' pts' if pct else ''} | {x['se'] * (100 if pct else 1):.{1 if pct else 2}f} |")
        return out
    L += tbl("6. Games since the team's last tie → tie in next game", r["games_since_tie"])
    L += tbl("6b. Team tie rate so far (15+ games) → tie in next game", r["tie_rate_to_date"])
    L += tbl("7. Games in a row with 7+ goals → next game over 6.5", r["after_over_run"])
    L += tbl("7b. Games in a row with 7+ goals → next game goals", r["over_run_goals"], False)
    L += tbl("7c. Games in a row with 5 or fewer → next game under 5.5", r["after_under_run"])
    L += tbl("7d. Games in a row with 5 or fewer → next game goals", r["under_run_goals"], False)
    L += tbl("8. Home team's home win rate so far (6+ home games) → home win", r["home_record_to_date"])
    L += tbl("8b. Away team's road win rate so far (6+ road games) → away win", r["away_record_to_date"])
    L += ["", "## 10. Tie boost x total compression", "", "| Tie boost | Compression | Model tie | Model avg total | 1X2 log loss | Brier 5.5 | Brier 6.5 |", "|---|---|---|---|---|---|---|"]
    for x in r["tie_grid"]:
        L.append(f"| {x['tie_f']} | {x['c']} | {x['tie']:.1%} | {x['exp']:.2f} | {x['logloss_1x2']:.4f} | {x['brier_5.5']:.4f} | {x['brier_6.5']:.4f} |")
    L += ["", f"## 9. Calibration slope of projected total: {r['total_slope']:.2f} (1.00 = perfect spread)", ""]
    return "\n".join(L) + "\n"
