"""SHL model: player scoring rates, team strength, goalies, game projections and player-prop chances.

Everything is computed "as of" a date from games before it, so the same code serves tonight's slate and the
game-by-game backtest. Structure mirrors the NHL model (rates per 60 shrunk toward a role-based prior, opponent and
goalie adjustments, Poisson game model) but every constant here is SHL-specific and fitted in backtest.py.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

CFG = {
    "season_w": {0: 1.0, 1: 0.55, 2: 0.30},     # this season, last season, two seasons ago
    "k_s": 6.0, "k_ixg": 10.0, "k_a": 12.0, "k_fin": 18.0,   # shrinkage, in hours (shots, ixG, assists) and in xG (finishing)
    "prior_toi_beta": 0.6,                      # role-based prior: rate x (TOI / position average) ^ beta
    "toi_recent_n": 5, "toi_recent_w": 0.6,
    "k_team_games": 14.0, "team_prior_w": 0.5,  # team strength: shrink to league, last season as a prior
    "k_goalie_xga": 60.0,                       # goalie: GA vs xGA, shrunk with this many xGA of league average
    "home_adv": 1.04,
    "tie_home_bonus": 0.10,                     # home teams win more ties (3-on-3 OT, shootout): fitted 2024/25, confirmed 2025/26
    "role_slope": 0.048, "role_mid": 14.5, "role_cap": (0.72, 1.20),   # forwards' goals/assists x role factor by projected TOI
    "tie_boost": 1.10,
    "ot_home_share": 0.5,
    "fin_cap": (0.6, 1.6),
    "goals_scale": 1.0,                         # calibration multipliers, fitted in the backtest
    "assists_scale": 1.0,
    "shots_scale": 0.974,                       # backtest 2024/25: shot counts ran 2.6% below the rates
    "shots_r": 15.0,                            # negative binomial spread for shots on goal (backtest 2024/25)
    "k_env_games": 60.0,                        # league shot environment: this season shrunk toward the weighted history
}


TIE_HOME_BONUS = [0.10]


# ------------------------------------------------------------------ helpers
def poisson_pmf(lam, n=15):
    out = np.zeros(n)
    out[0] = math.exp(-lam)
    for i in range(1, n):
        out[i] = out[i - 1] * lam / i
    return out


def p_over_nb(lam, line, r):
    """P(X > line) for a negative binomial with mean lam and size r"""
    k = int(math.floor(line))
    p = r / (r + max(lam, 1e-9))
    pmf = p ** r
    cdf = pmf
    for i in range(1, k + 1):
        pmf = pmf * (i + r - 1) / i * (1 - p)
        cdf += pmf
    return float(max(0.0, 1 - cdf))


def p_over(lam, line):
    k = int(math.floor(line))
    pmf = poisson_pmf(max(lam, 1e-9), max(k + 2, 25))
    return float(max(0.0, 1 - pmf[:k + 1].sum()))


def game_dist(mh, ma, tie_boost, ot_share):
    N = 13
    j = np.outer(poisson_pmf(mh, N), poisson_pmf(ma, N))
    j[np.diag_indices(N)] *= tie_boost
    j /= j.sum()
    i_, k_ = np.indices(j.shape)
    hw, tie, aw = j[i_ > k_].sum(), j[i_ == k_].sum(), j[i_ < k_].sum()
    share = mh / (mh + ma) if mh + ma > 0 else 0.5
    share = min(0.9, share + TIE_HOME_BONUS[0])
    out = {"p_home": hw + tie * share, "p_away": aw + tie * (1 - share), "p_tie_reg": tie, "p00": j[0, 0],
           "exp_total": mh + ma + tie, "proj_h": mh, "proj_a": ma}
    tot = np.zeros(2 * N + 1)
    for a in range(N):
        for b in range(N):
            tot[a + b + (1 if a == b else 0)] += j[a, b]      # a regulation tie gets one more goal (OT/shootout winner)
    for L in (4.5, 5.5, 6.5):
        out[f"over_{L}"] = float(tot[int(L) + 1:].sum())
    reg = {"1": hw, "X": tie, "2": aw}
    out["mk"] = {"1": hw, "X": tie, "2": aw, "hpl": j[i_ - k_ >= 2].sum(), "apl": j[k_ - i_ >= 2].sum()}
    return out, j


# ------------------------------------------------------------------ state as of a date
def season_weight(season, cur, cfg):
    return cfg["season_w"].get(cur - season, 0.0)


def build_state(P, G, S, sch, asof: str, cfg=CFG, xg_w=None):
    """ratings from every game strictly before `asof` (YYYY-MM-DD)."""
    from . import xg as xgm
    cur = int(sch[sch.date >= asof].season.min()) if (sch.date >= asof).any() else int(sch.season.max())
    p = P[(P.date < asof) & (P.season >= cur - 2)].copy()
    s = S[(S.date < asof) & (S.season >= cur - 2)].copy()
    g = G[(G.date < asof) & (G.season >= cur - 2)].copy()
    if "xg" not in s:
        s["xg"] = xgm.predict(s, xg_w)
    for df in (p, s, g):
        df["w"] = df.season.map(lambda y: season_weight(y, cur, cfg))
    # individual xG per player-game (unblocked, not empty net)
    ix = s[xgm.model_rows(s) | s.pen_shot].groupby(["uuid", "pid"]).xg.sum()
    p["ixg"] = [ix.get((u, i), 0.0) for u, i in zip(p.uuid, p.pid)]
    # league environment (this season so far, padded with last season)
    gs = sch[(sch.date < asof) & (sch.state == "post-game") & (sch.season >= cur - 1)]
    cur_g = gs[gs.season == cur]
    gpg_cur = float((cur_g.hs + cur_g["as"]).mean()) if len(cur_g) else np.nan
    gpg_prev = float((gs[gs.season == cur - 1].hs + gs[gs.season == cur - 1]["as"]).mean()) if (gs.season == cur - 1).any() else 5.5
    n_cur = len(cur_g)
    gpg = (gpg_cur * n_cur + gpg_prev * 60) / (n_cur + 60) if n_cur else gpg_prev
    # league per-60 rates by position (weighted)
    pw = p[p.toi > 0]
    lg = {}
    for pos in ("F", "D"):
        q = pw[pw.posg == pos]
        h = (q.toi * q.w).sum() / 60
        lg[pos] = {"s60": (q.s * q.w).sum() / h, "ixg60": (q.ixg * q.w).sum() / h, "a60": (q.a * q.w).sum() / h,
                   "g60": (q.g * q.w).sum() / h, "toi": float(np.average(q.toi, weights=q.w)) if len(q) else 15.0,
                   "hit60": (q.hit * q.w).sum() / h, "blk60": (q.blk * q.w).sum() / h}
    fin_lg = (pw.g * pw.w).sum() / max(1e-6, (pw.ixg * pw.w).sum())
    # league shot environment: this season's shots per 60 (shrunk) vs the weighted history the player rates come from
    s60_hist = (pw.s * pw.w).sum() / ((pw.toi * pw.w).sum() / 60)
    qc_ = pw[pw.season == cur]
    n_games_cur = qc_.uuid.nunique()
    s60_cur = qc_.s.sum() / (qc_.toi.sum() / 60) if len(qc_) else s60_hist
    s60_prev = pw[pw.season == cur - 1]
    s60_prev = s60_prev.s.sum() / (s60_prev.toi.sum() / 60) if len(s60_prev) else s60_hist
    env_s = ((s60_cur * n_games_cur + s60_prev * cfg["k_env_games"]) / (n_games_cur + cfg["k_env_games"])) / s60_hist
    # ---- players
    players = {}
    last_game = p.sort_values("date").groupby("pid").tail(1).set_index("pid")
    for pid, q in pw.groupby("pid"):
        pos = q.posg.iloc[-1]
        if pos not in lg:
            continue
        L = lg[pos]
        qc = q[q.season == cur].sort_values("date")
        toi_season = float(qc.toi.mean()) if len(qc) else float(q[q.season == q.season.max()].toi.mean())
        toi_recent = float(qc.toi.tail(cfg["toi_recent_n"]).mean()) if len(qc) else toi_season
        toi_proj = cfg["toi_recent_w"] * toi_recent + (1 - cfg["toi_recent_w"]) * toi_season
        tf = min(1.8, max(0.4, toi_proj / L["toi"])) ** cfg["prior_toi_beta"]
        h = float((q.toi * q.w).sum() / 60)
        s60 = ((q.s * q.w).sum() + cfg["k_s"] * L["s60"] * tf) / (h + cfg["k_s"])
        ixg60 = ((q.ixg * q.w).sum() + cfg["k_ixg"] * L["ixg60"] * tf) / (h + cfg["k_ixg"])
        a60 = ((q.a * q.w).sum() + cfg["k_a"] * L["a60"] * tf) / (h + cfg["k_a"])
        xg_sum = float((q.ixg * q.w).sum())
        fin = ((q.g * q.w).sum() + cfg["k_fin"] * fin_lg) / (xg_sum + cfg["k_fin"]) / fin_lg
        fin = min(cfg["fin_cap"][1], max(cfg["fin_cap"][0], fin))
        g60 = ixg60 * fin_lg * fin
        lgm = last_game.loc[pid]
        players[int(pid)] = {"pos": lgm.pos, "posg": pos, "team": lgm.team, "num": lgm.num, "line": lgm.line, "last": lgm.date,
                             "toi_proj": toi_proj, "toi_recent": toi_recent, "toi_season": toi_season,
                             "g60": g60, "a60": a60, "s60": s60, "ixg60": ixg60, "fin": fin, "hours": h,
                             "hit60": ((q.hit * q.w).sum() + 4 * L["hit60"]) / (h + 4), "blk60": ((q.blk * q.w).sum() + 4 * L["blk60"]) / (h + 4),
                             "gp_cur": int(len(qc)), "gp_all": int(len(q))}
    # ---- teams: xG for / against per game, shrunk, last season as prior
    teams = {}
    tg = s.groupby(["uuid", "team"]).xg.sum().rename("xgf").reset_index()
    ga = s.groupby(["uuid", "opp"]).xg.sum().rename("xga").reset_index().rename(columns={"opp": "team"})
    tg = tg.merge(ga, on=["uuid", "team"], how="outer").fillna(0).merge(sch[["uuid", "season", "date"]], on="uuid")
    gres = sch[(sch.date < asof) & (sch.state == "post-game")]
    goals_for = pd.concat([gres[["uuid", "home", "hs", "as"]].rename(columns={"home": "team", "hs": "gf", "as": "ga"}),
                           gres[["uuid", "away", "as", "hs"]].rename(columns={"away": "team", "as": "gf", "hs": "ga"})])
    tg = tg.merge(goals_for, on=["uuid", "team"], how="left")
    lg_xg = float(tg[tg.season == cur].xgf.mean()) if (tg.season == cur).any() else float(tg.xgf.mean())
    for team, q in tg.groupby("team"):
        qc, qp = q[q.season == cur], q[q.season == cur - 1]
        n = len(qc)
        def rate(col):
            prior = (qp[col].mean() if len(qp) else lg_xg) * cfg["team_prior_w"] + lg_xg * (1 - cfg["team_prior_w"])
            return (qc[col].sum() + cfg["k_team_games"] * prior) / (n + cfg["k_team_games"])
        teams[team] = {"xgf": rate("xgf"), "xga": rate("xga"), "gp": n, "gf": int(qc.gf.sum()) if n else 0, "ga": int(qc.ga.sum()) if n else 0,
                       "s_xgf": float(qc.xgf.sum()), "s_xga": float(qc.xga.sum())}
    lg_team = np.mean([t["xgf"] for t in teams.values()]) if teams else lg_xg
    for t in teams.values():
        t["att"], t["def"] = t["xgf"] / lg_team, t["xga"] / lg_team
    # ---- goalies: goals allowed vs expected (share of the team's xGA by shots faced)
    goalies = {}
    txga = s.groupby(["uuid", "opp"]).xg.sum()
    tsa = g.groupby(["uuid", "team"]).sa.sum()
    g["xga"] = [txga.get((u, t), 0.0) * (sa / max(1, tsa.get((u, t), 1))) for u, t, sa in zip(g.uuid, g.team, g.sa)]
    lg_ratio = float((g.ga * g.w).sum() / max(1e-6, (g.xga * g.w).sum()))
    for pid, q in g[g.sa > 0].groupby("pid"):
        r = ((q.ga * q.w).sum() + cfg["k_goalie_xga"] * lg_ratio) / ((q.xga * q.w).sum() + cfg["k_goalie_xga"]) / lg_ratio
        qc = q[q.season == cur]
        goalies[int(pid)] = {"ratio": float(r), "team": q.sort_values("date").team.iloc[-1], "gp_cur": int(len(qc)),
                             "starts_cur": int(qc.started.sum()) if "started" in qc else int(len(qc)),
                             "sv_cur": float(qc.sv.sum() / qc.sa.sum()) if qc.sa.sum() else None, "ga_cur": int(qc.ga.sum()), "sa_cur": int(qc.sa.sum()),
                             "xga_cur": float(qc.xga.sum()), "gsax_cur": float(qc.xga.sum() * lg_ratio - qc.ga.sum()),
                             "gaa_cur": float(qc.ga.sum() / max(1, len(qc))) if len(qc) else None,
                             "sv_3": float(q.sv.sum() / q.sa.sum()) if q.sa.sum() else None, "gaa_3": float(q.ga.sum() / max(1, len(q))),
                             "gsax_3": float(q.xga.sum() * lg_ratio - q.ga.sum()), "gp_3": int(len(q)),
                             "last": q.date.max()}
    return {"asof": asof, "season": cur, "gpg": gpg, "env_s": float(env_s), "lg": lg, "lg_xg_team": lg_team, "fin_lg": fin_lg, "lg_ratio": lg_ratio,
            "players": players, "teams": teams, "goalies": goalies}


def project(state, home, away, lineup_h, lineup_a, starter_h, starter_a, cfg=CFG):
    """game distribution and per-player expected counts; lineup_* = list of player ids playing tonight."""
    T = state["teams"]
    th, ta = T.get(home, {"att": 1, "def": 1}), T.get(away, {"att": 1, "def": 1})
    half = state["gpg"] / 2
    gr = lambda gid: state["goalies"].get(gid, {}).get("ratio", 1.0) if gid else 1.0
    gh, ga_ = gr(starter_h), gr(starter_a)                 # goalie the attack faces
    mh = half * th["att"] * ta["def"] * cfg["home_adv"] * ga_
    ma = half * ta["att"] * th["def"] / cfg["home_adv"] * gh
    TIE_HOME_BONUS[0] = cfg.get("tie_home_bonus", 0.0)
    dist, J = game_dist(mh, ma, cfg["tie_boost"], cfg["ot_home_share"])
    props = []
    for team, opp, lu, mu_team, venue, gk_opp in ((home, away, lineup_h, mh, cfg["home_adv"], ga_), (away, home, lineup_a, ma, 1 / cfg["home_adv"], gh)):
        o = T.get(opp, {"def": 1})
        for pid in lu:
            pl = state["players"].get(pid)
            if not pl or pl["posg"] == "G":
                continue
            hrs = pl["toi_proj"] / 60
            role = 1.0
            if pl["posg"] == "F":
                role = min(cfg["role_cap"][1], max(cfg["role_cap"][0], 1 + cfg["role_slope"] * (pl["toi_proj"] - cfg["role_mid"])))
            lam_g = pl["g60"] * hrs * o["def"] * venue * gk_opp * cfg["goals_scale"] * role
            lam_a = pl["a60"] * hrs * o["def"] * venue * gk_opp * cfg["assists_scale"] * role
            lam_s = pl["s60"] * hrs * venue * cfg["shots_scale"] * state.get("env_s", 1.0)
            props.append({"id": pid, "team": team, "opp": opp, "home": team == home,
                          "base": {"g": lam_g / gk_opp, "a": lam_a / gk_opp, "s": lam_s, "hit": pl["hit60"] * hrs, "blk": pl["blk60"] * hrs},
                          "lam": {"g": lam_g, "a": lam_a, "s": lam_s},
                          "factors": {"opp_def": o["def"], "venue": venue, "goalie": gk_opp, "b2b": 1.0, "env": 1.0, "role": role, "opp_shots": 1.0}})
    # keep player goals consistent with the team total: scale each side's goal expectations to the game model
    for team, mu_team in ((home, mh), (away, ma)):
        side = [x for x in props if x["team"] == team]
        tot = sum(x["lam"]["g"] for x in side)
        if tot > 0:
            k = (mu_team * 0.97) / tot                   # ~3% of goals by players outside the projected lineup / own goals
            k = min(1.25, max(0.8, k))
            for x in side:
                x["lam"]["g"] *= k; x["base"]["g"] *= k
                x["lam"]["a"] *= k; x["base"]["a"] *= k
    for x in props:
        lg_, la, ls = x["lam"]["g"], x["lam"]["a"], x["lam"]["s"]
        x["p"] = {"g": 1 - math.exp(-lg_), "a": 1 - math.exp(-la), "pt": 1 - math.exp(-(lg_ + la)),
                  "g2": p_over(lg_, 1.5), "g3": p_over(lg_, 2.5), "a2": p_over(la, 1.5), "pt2": p_over(lg_ + la, 1.5), "pt3": p_over(lg_ + la, 2.5),
                  **{f"s{L}": p_over_nb(ls, L, cfg["shots_r"]) for L in (1.5, 2.5, 3.5)},
                  **{f"h{L}": p_over(x["base"]["hit"], L) for L in (0.5, 1.5, 2.5)},
                  **{f"b{L}": p_over(x["base"]["blk"], L) for L in (0.5, 1.5)}}
    # first goalscorer: chance of a goal before a shootout x his team scores first x his share of its goals
    p_any = 1 - dist["p00"] * 0.4
    for team, mu_team in ((home, mh), (away, ma)):
        side = [x for x in props if x["team"] == team]
        tot = sum(x["lam"]["g"] for x in side) or 1
        share = mu_team / (mh + ma)
        for x in side:
            x["p"]["fg"] = p_any * share * x["lam"]["g"] / tot
    return {"mu": {"h": mh, "a": ma}, "default": dist, "props": props}
