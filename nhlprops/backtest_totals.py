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


def totals_dist(mh, ma):
    ph, pa = pois(mh * 0.985), pois(ma * 0.985)
    j = np.outer(ph, pa)
    tot = np.zeros(34)
    for i in range(16):
        for k in range(16):
            t = i + k + (1 if i == k else 0)
            tot[t] += j[i, k]
    return {L: float(tot[int(L) + 1:].sum()) for L in LINES}, float((tot * np.arange(34)).sum())


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
        lg_sh = 0.09
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
        rows.append({"season": season, "id": g.id, "date": g.date, "home": H, "away": A,
                     "total": int(g.hs + g.as_), "exp": exp_total, "mh": mh, "ma": ma,
                     **{f"p{L}": po[L] for L in LINES}, **{f"h{L}": hist_p[L] for L in LINES},
                     "recent5": float(np.mean(recent)) if len(recent) >= 8 else np.nan,
                     "recent_lg": 2 * lg_now, "gp_min": min(len(hh_), len(ha_))})
        # update after the game
        hist.setdefault(H, []).append((g.hs, g.as_, g.hsog, g.asog, g.hs + g.as_))
        hist.setdefault(A, []).append((g.as_, g.hs, g.asog, g.hsog, g.hs + g.as_))
        h2h.setdefault(key, []).append(g.hs + g.as_)
        lg_sum += g.hs + g.as_; lg_n += 1
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
    d = pd.concat(frames)
    d = d[d.gp_min >= 5]
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
    (out_dir / "totals.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "totals.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


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
    return "\n".join(L) + "\n"
