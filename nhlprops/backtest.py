"""Point-in-time backtest: replay past seasons game by game.

For every player-game we predict using ONLY information available before that game:
  * earlier games of the same season (from boxscores, cumulative up to the day before)
  * complete earlier seasons (MoneyPuck season summaries)
and compare with what happened. Nothing here touches the website or its history.

Differences from the live model (documented in the report):
  * in-season expected goals (ixG) aren't available game by game, so ixG comes from prior seasons only
  * the actual starting goalie and actual lineup are used (live: projected / confirmed)
  * opponent defence = goals allowed so far (regressed) blended with last season's xGA
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .gather import parse_club_games
from .grade import summarize
from .model import _hours, _num, _sit
from .sources import Source, teams_for_season


def _toi(s) -> float:
    try:
        m, sec = str(s).split(":")
        return int(m) + int(sec) / 60
    except Exception:
        return 0.0


# ------------------------------------------------------------------ data
def season_boxscores(src: Source, season: int, workers: int, log=print) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda t: src.club_schedule(t, season, False), teams_for_season(season)))
    games = pd.DataFrame([g for js in res for g in parse_club_games(js, season)]).drop_duplicates("id")
    games = games[games.final].sort_values("date")
    log(f"  {season}: {len(games)} final games, downloading boxscores ...")
    with ThreadPoolExecutor(workers) as ex:
        boxes = list(ex.map(src.boxscore, games.id.tolist()))
    sk, go = [], []
    for gm, box in zip(games.itertuples(), boxes):
        if not box:
            continue
        pbg = box.get("playerByGameStats") or {}
        for side, team, opp, home in (("homeTeam", gm.home, gm.away, True), ("awayTeam", gm.away, gm.home, False)):
            grp = pbg.get(side) or {}
            for key in ("forwards", "defense", "defensemen"):
                for p in grp.get(key) or []:
                    sk.append({"gid": gm.id, "date": gm.date, "pid": int(p["playerId"]), "team": team, "opp": opp,
                               "home": home, "posg": "D" if key != "forwards" else "F",
                               "g": p.get("goals", 0) or 0, "a": p.get("assists", 0) or 0,
                               "s": p.get("sog", p.get("shots", 0)) or 0, "toi": _toi(p.get("toi"))})
            gl = grp.get("goalies") or []
            for p in gl:
                go.append({"gid": gm.id, "date": gm.date, "pid": int(p["playerId"]), "team": team, "opp": opp,
                           "starter": bool(p.get("starter")) if "starter" in p else None,
                           "toi": _toi(p.get("toi")), "ga": p.get("goalsAgainst", 0) or 0})
    sk, go = pd.DataFrame(sk), pd.DataFrame(go)
    if len(go) and go["starter"].isna().any():  # fallback: most ice time
        go["starter"] = go.groupby(["gid", "team"])["toi"].transform("max") == go["toi"]
    return games, sk, go


def prior_frames(src: Source, season: int) -> dict:
    sk, gl, tm = [], [], []
    for y in range(season - 8, season):
        d = src.moneypuck("skaters", y, False)
        if d is not None:
            d = d[_sit(d) == "all"]
            sk.append(pd.DataFrame({"pid": pd.to_numeric(d.playerId, errors="coerce"), "season": y,
                                    "posg": np.where(d.get("position", pd.Series("F", index=d.index)).astype(str) == "D", "D", "F"),
                                    "gp": _num(d, "games_played"), "h": _hours(d, "icetime"),
                                    "s": _num(d, "I_F_shotsOnGoal"), "g": _num(d, "I_F_goals"), "xg": _num(d, "I_F_xGoals"),
                                    "a": _num(d, "I_F_primaryAssists") + _num(d, "I_F_secondaryAssists")}))
        if y >= season - 3:
            d = src.moneypuck("goalies", y, False)
            if d is not None:
                d = d[_sit(d) == "all"]
                gl.append(pd.DataFrame({"pid": pd.to_numeric(d.playerId, errors="coerce"), "season": y,
                                        "xga": _num(d, "xGoals"), "ga": _num(d, "goals")}))
        if y == season - 1:
            d = src.moneypuck("teams", y, False)
            if d is not None:
                d = d[_sit(d) == "all"]
                tm.append(pd.DataFrame({"team": d.team.astype(str), "h": _hours(d, "iceTime"), "xga": _num(d, "xGoalsAgainst")}))
    return {"sk": pd.concat(sk).groupby(["pid", "season", "posg"], as_index=False).sum() if sk else pd.DataFrame(),
            "go": pd.concat(gl).groupby(["pid", "season"], as_index=False).sum() if gl else pd.DataFrame(),
            "tm": pd.concat(tm).groupby("team", as_index=False).sum() if tm else pd.DataFrame()}


# ------------------------------------------------------------------ model
def shrink(x, n, prior, k):
    return (x + k * prior) / (n + k)


def predict(sk: pd.DataFrame, pri: dict, games: pd.DataFrame, go: pd.DataFrame, season: int, cfg: dict,
            weights=(5, 4, 3, 2, 1), kmult=1.0) -> pd.DataFrame:
    """Return sk with lambda_g / lambda_a / lambda_s columns, using info strictly before each game."""
    df = sk.sort_values(["pid", "date", "gid"]).copy()
    g = df.groupby("pid")
    for c in ("g", "a", "s", "toi"):
        df[f"c_{c}"] = g[c].cumsum() - df[c]           # season to date, excluding this game
    df["c_gp"] = g.cumcount()
    df["toi10"] = g["toi"].transform(lambda x: x.shift(1).rolling(10, min_periods=1).mean())

    P = pri["sk"]
    W = {season - i: w / max(weights) for i, w in enumerate(weights)}  # current season = weights[0]/max
    w_cur = W[season]
    prior_w = P[P.season.isin([y for y in W if y < season])].copy()
    prior_w["w"] = prior_w.season.map(W)
    agg = prior_w.assign(**{f"w{c}": prior_w[c] * prior_w["w"] for c in ("h", "s", "g", "xg", "a")}) \
                 .groupby("pid")[["wh", "ws", "wg", "wxg", "wa"]].sum()
    career = P.groupby("pid")[["s", "g"]].sum()
    last = P[P.season == season - 1].groupby("pid")[["h", "gp"]].sum()
    df = df.join(agg, on="pid").join(career.add_prefix("car_"), on="pid").join(last.add_prefix("ls_"), on="pid")
    df = df.fillna({c: 0.0 for c in ("wh", "ws", "wg", "wxg", "wa", "car_s", "car_g", "ls_h", "ls_gp")})

    # league position averages from the 3 prior seasons
    L = P[P.season >= season - 3].groupby("posg")[["h", "s", "g", "xg", "a", "gp"]].sum()
    lg = {pg: {"s60": r.s / r.h, "xg60": r.xg / r.h, "a60": r.a / r.h, "sh": r.g / r.s, "toi": r.h * 60 / r.gp}
          for pg, r in L.iterrows()}
    col = lambda k: df.posg.map({pg: v[k] for pg, v in lg.items()})

    k = cfg
    h_cur = df.c_toi / 60
    eh = w_cur * h_cur + df.wh
    s60 = shrink(w_cur * df.c_s + df.ws, eh, col("s60"), k["k_shots_hours"] * kmult)
    xg60 = shrink(df.wxg, df.wh, col("xg60"), k["k_ixg_hours"] * kmult)              # prior seasons only
    a60 = shrink(w_cur * df.c_a + df.wa, eh, col("a60"), k["k_assist_hours"] * kmult)
    career_sh = shrink(df.car_g + df.c_g, df.car_s + df.c_s, col("sh"), k["k_career_sh_shots"] * kmult)
    recent_sh = shrink(w_cur * df.c_g + df.wg, w_cur * df.c_s + df.ws, career_sh, k["k_recent_sh_shots"] * kmult)
    finish = shrink(df.wg, df.wxg, 1.0, k["k_finish_xg"] * kmult)
    al = k["alpha_shots_vs_xg"]
    g60 = al * s60 * recent_sh + (1 - al) * xg60 * finish

    toi_season = np.where(df.c_gp >= 5, df.c_toi / df.c_gp.clip(lower=1),
                          np.where(df.ls_gp > 0, df.ls_h * 60 / df.ls_gp.clip(lower=1), col("toi")))
    toi_recent = df.toi10.fillna(pd.Series(toi_season, index=df.index))
    toi_proj = k["toi_recent_weight"] * toi_recent + (1 - k["toi_recent_weight"]) * toi_season

    # opponent defence: goals allowed so far (regressed 20 games) x last season's xGA rate
    tg = games.melt(id_vars=["id", "date"], value_vars=["home", "away"], value_name="team")
    tg["ga"] = np.where(tg.variable == "home", games.set_index("id").loc[tg.id, "as_"].values,
                        games.set_index("id").loc[tg.id, "hs"].values).astype(float)
    tg = tg.sort_values(["team", "date"])
    tg["c_ga"] = tg.groupby("team").ga.cumsum() - tg.ga
    tg["c_gp"] = tg.groupby("team").cumcount()
    lg_ga = float(tg.ga.mean())
    tg["ga_pg"] = shrink(tg.c_ga, tg.c_gp, lg_ga, 20)
    T = pri["tm"]
    if len(T):
        T = T.assign(xga60=T.xga / T.h)
        prior_def = (T.set_index("team").xga60 / (T.xga.sum() / T.h.sum())).to_dict()
    else:
        prior_def = {}
    tg["def"] = 0.5 * tg.ga_pg / lg_ga + 0.5 * tg.team.map(prior_def).fillna(1.0)
    df = df.join(tg.set_index(["id", "team"])["def"].rename("opp_def"), on=["gid", "opp"])

    # opposing starting goalie: GA / xGA over the 3 prior seasons vs league
    G = pri["go"]
    lg_ratio = float(G.ga.sum() / G.xga.sum()) if len(G) and G.xga.sum() else 1.0
    gr = G.groupby("pid")[["ga", "xga"]].sum()
    gratio = (shrink(gr.ga, gr.xga, lg_ratio, k["k_goalie_xga"]) / lg_ratio).to_dict() if len(gr) else {}
    st = go[go.starter].set_index(["gid", "team"]).pid
    st = st[~st.index.duplicated()]
    df["opp_goalie"] = [gratio.get(st.get((a, b)), 1.0) for a, b in zip(df.gid, df.opp)]

    venue = np.where(df.home, k["home_adv"], 1 / k["home_adv"])
    hrs = toi_proj / 60
    base = hrs * df.opp_def.fillna(1.0) * venue
    df["lam_g"] = g60 * base * df.opp_goalie
    df["lam_a"] = a60 * base * df.opp_goalie
    df["lam_s"] = s60 * hrs * venue
    df["due"] = (df.c_s >= 15) & (df.c_g < (df.c_s * recent_sh - 1.0 * np.sqrt(df.c_s * recent_sh)))
    return df


def r_now(d, m, y):
    p = {"Goal": 1 - np.exp(-d.lam_g), "Assist": 1 - np.exp(-d.lam_a), "Point": 1 - np.exp(-(d.lam_g + d.lam_a)),
         "SOG 2+": p_over(d.lam_s, 1.5), "SOG 3+": p_over(d.lam_s, 2.5)}[m]
    return float(((p - y.astype(float)) ** 2).mean())


def p_over(lam, line):
    lam = np.asarray(lam, float)
    k = int(math.floor(line))
    term = np.exp(-lam)
    cdf = term.copy()
    for i in range(1, k + 1):
        term = term * lam / i
        cdf = cdf + term
    return 1 - cdf


def evaluate(df: pd.DataFrame, min_gp: int = 5) -> dict:
    d = df[df.c_gp >= min_gp]
    y = {"Goal": (d.g >= 1), "Assist": (d.a >= 1), "Point": (d.g + d.a >= 1),
         "SOG 2+": (d.s >= 2), "SOG 3+": (d.s >= 3)}
    preds = {
        "Model": {"Goal": 1 - np.exp(-d.lam_g), "Assist": 1 - np.exp(-d.lam_a), "Point": 1 - np.exp(-(d.lam_g + d.lam_a)),
                  "SOG 2+": p_over(d.lam_s, 1.5), "SOG 3+": p_over(d.lam_s, 2.5)},
    }
    gp = d.c_gp.clip(lower=1)
    preds["Season average"] = {"Goal": 1 - np.exp(-d.c_g / gp), "Assist": 1 - np.exp(-d.c_a / gp),
                               "Point": 1 - np.exp(-(d.c_g + d.c_a) / gp), "SOG 2+": p_over(d.c_s / gp, 1.5),
                               "SOG 3+": p_over(d.c_s / gp, 2.5)}
    out = {}
    for name, pm in preds.items():
        out[name] = {}
        for m, p in pm.items():
            s = summarize(list(zip(np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4).tolist(), y[m].astype(int).tolist())))
            out[name][m] = s
    return out


def streak_check(games: pd.DataFrame, cfg: dict) -> list[dict]:
    """Does the next game go differently once a team reaches its streak threshold?"""
    rows = []
    t = games.melt(id_vars=["id", "date", "hs", "as_", "last"], value_vars=["home", "away"], value_name="team")
    t["win"] = np.where(t.variable == "home", t.hs > t.as_, t.as_ > t.hs)
    t = t.sort_values(["team", "date"])
    for team, d in t.groupby("team"):
        wins = d.win.tolist()
        streak, kind = 0, None
        for i, w in enumerate(wins):
            if i >= 10:
                pct = sum(wins[:i]) / i
                tier = "strong" if pct >= 0.60 else "weak" if pct <= 0.45 else "average"
                thr = cfg["streak_thresholds"][tier]["win" if kind == "W" else "loss"] if kind else 99
                rows.append({"tier": tier, "kind": kind, "len": streak, "at_rule": bool(kind) and streak >= thr,
                             "win": bool(w), "exp": pct})
            k = "W" if w else "L"
            streak = streak + 1 if k == kind else 1
            kind = k
    r = pd.DataFrame(rows)
    out = []
    for (kind, at), d in r[r.kind.notna()].groupby(["kind", "at_rule"]):
        out.append({"streak": "winning" if kind == "W" else "losing", "rule_reached": bool(at), "games": int(len(d)),
                    "won_next": float(d.win.mean()), "expected_from_win_pct": float(d.exp.mean())})
    return out


def run(src: Source, seasons: list[int], cfg: dict, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"seasons": seasons, "results": {}, "variants": {}, "streak": {}, "due": {}}
    all_df = []
    for season in seasons:
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        log(f"  {season}: {len(sk)} skater-games, {len(go)} goalie-games")
        if sk.empty:
            continue
        pri = prior_frames(src, season)
        df = predict(sk, pri, games, go, season, cfg)
        df["season"] = season
        all_df.append(df)
        report["streak"][season] = streak_check(games, cfg)
        for name, kw in {"3 seasons": {"weights": (5, 4, 3)}, "5 seasons": {"weights": (5, 4, 3, 2, 1)},
                         "weaker regression (k x0.5)": {"kmult": 0.5}, "stronger regression (k x2)": {"kmult": 2.0}}.items():
            v = evaluate(predict(sk, pri, games, go, season, cfg, **kw))["Model"]
            report["variants"].setdefault(name, {})[season] = {m: {"brier": s["brier"], "logloss": s["logloss"]}
                                                              for m, s in v.items()}
    df = pd.concat(all_df)
    report["results"] = evaluate(df)
    d = df[df.c_gp >= 5]
    for flag, sub in (("due", d[d.due]), ("not due", d[~d.due])):
        report["due"][flag] = {"n": int(len(sub)), "pred_goal": float((1 - np.exp(-sub.lam_g)).mean()),
                               "actual_goal": float((sub.g >= 1).mean())}
    report["n_player_games"] = int(len(d))
    # calibration check: which multiplier on each expected count fits best?
    ys = {"Goal": (d.g >= 1), "Assist": (d.a >= 1), "Point": (d.g + d.a >= 1), "SOG 2+": (d.s >= 2), "SOG 3+": (d.s >= 3)}
    fits = {}
    for m in ys:
        best = None
        for c in np.arange(0.80, 1.21, 0.02):
            if m == "Goal": p = 1 - np.exp(-d.lam_g * c)
            elif m == "Assist": p = 1 - np.exp(-d.lam_a * c)
            elif m == "Point": p = 1 - np.exp(-(d.lam_g + d.lam_a) * c)
            else: p = p_over(d.lam_s * c, 1.5 if m == "SOG 2+" else 2.5)
            b = float(((p - ys[m].astype(float)) ** 2).mean())
            if best is None or b < best[1]:
                best = (round(float(c), 2), b)
        fits[m] = {"best_multiplier": best[0], "brier_at_best": best[1], "brier_now": r_now(d, m, ys[m])}
    report["calibration_fit"] = fits
    # streak rule pooled over all seasons
    pooled = {}
    for rows in report["streak"].values():
        for x in rows:
            k = (x["streak"], x["rule_reached"])
            p = pooled.setdefault(k, {"games": 0, "won": 0.0, "exp": 0.0})
            p["games"] += x["games"]; p["won"] += x["won_next"] * x["games"]; p["exp"] += x["expected_from_win_pct"] * x["games"]
    report["streak_pooled"] = [{"streak": k[0], "rule_reached": k[1], "games": v["games"],
                                "won_next": v["won"] / v["games"], "expected": v["exp"] / v["games"],
                                "std_error": math.sqrt(0.25 / v["games"])} for k, v in pooled.items()]
    (out_dir / "results.json").write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    log((out_dir / "report.md").read_text())
    return report


def to_markdown(r: dict) -> str:
    L = [f"# Backtest: seasons {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}",
         f"Player-games evaluated: {r['n_player_games']:,} (players with at least 5 games played that season)", "",
         "## Model vs simple season average", "",
         "| Market | Model Brier | Season-avg Brier | Model skill | Predicted | Actual |", "|---|---|---|---|---|---|"]
    for m, s in r["results"]["Model"].items():
        b = r["results"]["Season average"][m]
        L.append(f"| {m} | {s['brier']:.4f} | {b['brier']:.4f} | {s['skill'] * 100:+.1f}% | {s['mean_pred'] * 100:.1f}% | {s['hit_rate'] * 100:.1f}% |")
    L += ["", "Lower Brier = better. Skill = improvement over always guessing the overall hit rate.", "",
          "## Calibration (model)", ""]
    for m, s in r["results"]["Model"].items():
        L.append(f"**{m}**: " + " · ".join(f"{b['pred'] * 100:.0f}%→{b['actual'] * 100:.0f}% (n={b['n']})" for b in s["bins"]))
    L += ["", "## Variants (Brier, lower is better)", "", "| Variant | Season | Goal | Assist | Point | SOG 2+ | SOG 3+ |",
          "|---|---|---|---|---|---|---|"]
    for name, by in r["variants"].items():
        for season, v in by.items():
            L.append(f"| {name} | {season} | " + " | ".join(f"{v[m]['brier']:.4f}" for m in ("Goal", "Assist", "Point", "SOG 2+", "SOG 3+")) + " |")
    L += ["", "## 'Due' players (scoring below true talent)", ""]
    for k, v in r["due"].items():
        L.append(f"- {k}: n={v['n']:,}, model predicted goal {v['pred_goal'] * 100:.1f}%, actual {v['actual_goal'] * 100:.1f}%")
    L += ["", "## 6-game rule: next game after a streak", "", "| Season | Streak | Rule reached | Games | Won next | Expected (team win %) |",
          "|---|---|---|---|---|---|"]
    for season, rows in r["streak"].items():
        for x in rows:
            L.append(f"| {season} | {x['streak']} | {'yes' if x['rule_reached'] else 'no'} | {x['games']} | {x['won_next'] * 100:.1f}% | {x['expected_from_win_pct'] * 100:.1f}% |")
    L += ["", "## 6-game rule, all seasons pooled", "", "| Streak | Rule reached | Games | Won next | Expected | ± (1 std error) |", "|---|---|---|---|---|---|"]
    for x in r.get("streak_pooled", []):
        L.append(f"| {x['streak']} | {'yes' if x['rule_reached'] else 'no'} | {x['games']} | {x['won_next'] * 100:.1f}% | {x['expected'] * 100:.1f}% | {x['std_error'] * 100:.1f}% |")
    L += ["", "## Best-fitting multiplier per market (1.00 = already calibrated)", ""]
    for m, f in r.get("calibration_fit", {}).items():
        L.append(f"- {m}: x{f['best_multiplier']:.2f} (Brier {f['brier_now']:.4f} -> {f['brier_at_best']:.4f})")
    return "\n".join(L) + "\n"
