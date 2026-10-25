"""The model: regression to the mean + Poisson probabilities.

Player props
    lambda = projected TOI x regressed rate/60 x opponent defence x opponent goalie
             x home/away x back-to-back x league environment (x optional streak rule)
    P(at least one) = 1 - exp(-lambda)

Game totals
    each team's expected goals = 5v5 part + power-play part + "other" (EN, 4v4, 3v3, SH)
    distribution: independent Poisson; a tie after regulation adds 1 goal
    (OT winner or shootout winner), which is how bookmakers settle totals.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd


# ----------------------------------------------------------------- helpers
def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=df.index)


def _sit(df: pd.DataFrame) -> pd.Series:
    if "situation" in df.columns:
        return df["situation"].astype(str)
    return pd.Series("all", index=df.index)


def _hours(df: pd.DataFrame, col: str) -> pd.Series:
    """MoneyPuck ice time is in seconds; detect and convert to hours."""
    t = _num(df, col)
    gp = _num(df, "games_played").clip(lower=1)
    per_game = float((t / gp).median()) if len(df) else 0.0
    return t / 3600.0 if per_game > 200 else t / 60.0


def shrink(x: float, n: float, prior: float, k: float) -> float:
    """Regression to the mean: (events + k*prior) / (exposure + k)."""
    return (x + k * prior) / (n + k) if (n + k) > 0 else prior


def p_at_least_one(lam: float) -> float:
    return 1.0 - math.exp(-max(lam, 0.0))


def p_over_poisson(lam: float, line: float) -> float:
    k = int(math.floor(line))  # P(X > line) = 1 - P(X <= k)
    term, cdf = math.exp(-lam), 0.0
    for i in range(k + 1):
        if i > 0:
            term *= lam / i
        cdf += term
    return max(0.0, 1.0 - cdf)


def poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k + 1):
        if i > 0:
            term *= lam / i
        cdf += term
    return min(cdf, 1.0)


def pois_pmf(lam: float, n: int = 16) -> np.ndarray:
    out = np.zeros(n)
    out[0] = math.exp(-lam)
    for i in range(1, n):
        out[i] = out[i - 1] * lam / i
    return out


def game_distribution(mu_h: float, mu_a: float, lines: list[float]) -> dict:
    """Totals with the 'tie -> +1 goal' settlement rule, plus win probabilities."""
    reg_scale = 0.985  # ~1.5% of goals are scored in OT; keep regulation separate
    ph, pa = pois_pmf(mu_h * reg_scale), pois_pmf(mu_a * reg_scale)
    joint = np.outer(ph, pa)
    n = len(ph)
    tot = np.zeros(2 * n + 1)
    p_tie = 0.0
    p_home_reg = 0.0
    for i in range(n):
        for j in range(n):
            t = i + j
            if i == j:
                p_tie += joint[i, j]
                t += 1
            elif i > j:
                p_home_reg += joint[i, j]
            tot[t] += joint[i, j]
    share_h = mu_h / (mu_h + mu_a) if (mu_h + mu_a) > 0 else 0.5
    p_home = p_home_reg + p_tie * share_h
    out = {"p_home": p_home, "p_away": 1 - p_home, "p_tie_reg": p_tie,
           "exp_total": float((tot * np.arange(len(tot))).sum())}
    for L in lines:
        out[f"over_{L}"] = float(tot[int(math.floor(L)) + 1:].sum())
    return out


def fair_odds(p: float) -> float | None:
    return round(1 / p, 2) if p > 0 else None


# ------------------------------------------------------------- team stats
TEAM_COLS = {
    "xGF": "xGoalsFor", "xGA": "xGoalsAgainst", "GF": "goalsFor", "GA": "goalsAgainst",
    "SF": "shotsOnGoalFor", "SA": "shotsOnGoalAgainst",
    "HDSF": "highDangerShotsFor", "HDSA": "highDangerShotsAgainst",
    "HDxGF": "highDangerxGoalsFor", "HDxGA": "highDangerxGoalsAgainst",
}


def team_frame(mp_teams: dict[int, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for y, df in mp_teams.items():
        d = pd.DataFrame({"team": df["team"].astype(str), "season": y,
                          "sit": _sit(df),
                          "gp": _num(df, "games_played")})
        d["h"] = _hours(df, "iceTime" if "iceTime" in df.columns else "icetime")
        for k, c in TEAM_COLS.items():
            d[k] = _num(df, c)
        frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["team", "season", "sit", "gp", "h", *TEAM_COLS])
    return pd.concat(frames, ignore_index=True).groupby(["team", "season", "sit"], as_index=False).sum()


class TeamModel:
    def __init__(self, tf: pd.DataFrame, cur: int, cfg: dict):
        self.tf, self.cur, self.cfg = tf, cur, cfg
        pw = cfg["team_prior_season_weight"]
        self.w = {cur: 1.0, cur - 1: pw, cur - 2: pw * 0.4}
        self.lg = {}
        for sit in ("all", "5on5", "5on4", "4on5"):
            s = tf[(tf.sit == sit) & tf.season.isin(self.w)]
            ws = s.season.map(self.w)
            tot = {c: float((s[c] * ws).sum()) for c in ["gp", "h", *TEAM_COLS]}
            self.lg[sit] = tot
        a, e, p = self.lg["all"], self.lg["5on5"], self.lg["5on4"]
        gp = max(a["gp"], 1)
        self.lg_ev_min = e["h"] / gp * 60 if e["h"] else 48.0
        self.lg_other_gpg = max((a["GF"] - e["GF"] - p["GF"]) / gp, 0.25) if a["gp"] else 0.55
        self.fin_ev = e["GF"] / e["xGF"] if e["xGF"] else 1.0
        self.fin_pp = p["GF"] / p["xGF"] if p["xGF"] else 1.0
        self.lg_pp_min = p["h"] / gp * 60 if p["h"] else 5.5
        self.lg_pk_min = self.lg["4on5"]["h"] / gp * 60 if self.lg["4on5"]["h"] else 5.5

    def lg_rate(self, sit, col):
        L = self.lg[sit]
        return L[col] / L["h"] if L["h"] else 0.0

    def _sum(self, team, sit):
        s = self.tf[(self.tf.team == team) & (self.tf.sit == sit) & self.tf.season.isin(self.w)]
        ws = s.season.map(self.w)
        return {c: float((s[c] * ws).sum()) for c in ["gp", "h", *TEAM_COLS]}

    def rate(self, team, sit, col, k):
        s = self._sum(team, sit)
        return shrink(s[col], s["h"], self.lg_rate(sit, col), k)

    def minutes_pg(self, team, sit, lg_min, k_games=10):
        s = self._sum(team, sit)
        return shrink(s["h"] * 60, s["gp"], lg_min, k_games)

    def profile(self, team: str) -> dict:
        c = self.cfg
        kt, kp = c["k_team_hours"], c["k_team_pp_hours"]
        cur = self.tf[(self.tf.team == team) & (self.tf.season == self.cur)]
        e = cur[cur.sit == "5on5"].sum(numeric_only=True)
        a = cur[cur.sit == "all"].sum(numeric_only=True)
        pp = cur[cur.sit == "5on4"].sum(numeric_only=True)
        pk = cur[cur.sit == "4on5"].sum(numeric_only=True)

        def div(x, y):
            return float(x) / float(y) if float(y) else None

        sh = div(e.get("GF", 0), e.get("SF", 0))
        sv = 1 - div(e.get("GA", 0), e.get("SA", 0)) if e.get("SA", 0) else None
        return {
            "xgf5": self.rate(team, "5on5", "xGF", kt), "xga5": self.rate(team, "5on5", "xGA", kt),
            "xgf_pp": self.rate(team, "5on4", "xGF", kp), "xga_pk": self.rate(team, "4on5", "xGA", kp),
            "xga_all": self.rate(team, "all", "xGA", kt), "sa_all": self.rate(team, "all", "SA", kt),
            "pp_min": self.minutes_pg(team, "5on4", self.lg_pp_min),
            "pk_min": self.minutes_pg(team, "4on5", self.lg_pk_min),
            # current-season display numbers
            "gp": int(a.get("gp", 0)),
            "gf_pg": div(a.get("GF", 0), a.get("gp", 0)), "ga_pg": div(a.get("GA", 0), a.get("gp", 0)),
            "xgf_pct5": div(e.get("xGF", 0), e.get("xGF", 0) + e.get("xGA", 0)),
            "pdo": (sh + sv) if (sh is not None and sv is not None) else None,
            "sh5": sh, "sv5": sv,
            "hdsf60": div(e.get("HDSF", 0), e.get("h", 0)), "hdsa60": div(e.get("HDSA", 0), e.get("h", 0)),
            "hdxgf60": div(e.get("HDxGF", 0), e.get("h", 0)), "hdxga60": div(e.get("HDxGA", 0), e.get("h", 0)),
            "pp_gf60": div(pp.get("GF", 0), pp.get("h", 0)), "pk_ga60": div(pk.get("GA", 0), pk.get("h", 0)),
            "pp_xgf60_cur": div(pp.get("xGF", 0), pp.get("h", 0)), "pk_xga60_cur": div(pk.get("xGA", 0), pk.get("h", 0)),
        }

    def mu_parts(self, att: dict, deff: dict) -> dict:
        """Expected goals for `att` against `deff` (before goalie, venue, streak)."""
        lg5 = self.lg_rate("5on5", "xGA") or 2.5
        lgpk = self.lg_rate("4on5", "xGA") or 6.5
        ev = self.lg_ev_min / 60 * att["xgf5"] * deff["xga5"] / lg5 * self.fin_ev
        pp_h = 0.5 * (att["pp_min"] + deff["pk_min"]) / 60
        pp = pp_h * att["xgf_pp"] * deff["xga_pk"] / lgpk * self.fin_pp
        return {"ev": ev, "pp": pp, "other": self.lg_other_gpg}


# ---------------------------------------------------------- team history
def team_history(lg: pd.DataFrame, cur: int, date: str, cfg: dict) -> dict:
    """Results-based context: streaks, form, O/U rates, strength, back-to-back."""
    lines = cfg["ou_lines"]
    g = lg[(lg["final"]) & (lg["date"] < date)].copy()
    g["total"] = g["hs"].astype(float) + g["as_"].astype(float)

    league = {}
    for y, s in g.groupby("season"):
        league[int(y)] = {"games": int(len(s)), "gpg": float(s.total.mean()),
                          **{f"over_{L}": float((s.total > L).mean()) for L in lines}}
    hist_years = [y for y in league if y < cur]
    lg_hist = {f"over_{L}": float(np.mean([league[y][f"over_{L}"] for y in hist_years])) if hist_years else 0.5
               for L in lines}

    teams = {}
    for t in set(g.home) | set(g.away):
        m = g[(g.home == t) | (g.away == t)].sort_values("date")
        home = m.home == t
        gf = np.where(home, m.hs, m.as_).astype(float)
        ga = np.where(home, m.as_, m.hs).astype(float)
        res = pd.DataFrame({"season": m.season.values, "date": m.date.values, "home": home.values,
                            "opp": np.where(home, m.away, m.home), "gf": gf, "ga": ga,
                            "win": gf > ga, "otl": (gf < ga) & m["last"].isin(["OT", "SO"]).values,
                            "total": m.total.values})
        teams[t] = res

    out = {"league": league, "league_hist_over": lg_hist, "teams": {}}
    for t, res in teams.items():
        c = res[res.season == cur]
        p = res[res.season == cur - 1]
        pts = lambda d: (2 * d.win.sum() + d.otl.sum())
        prev_pct = pts(p) / (2 * len(p)) if len(p) else 0.5
        strength = shrink(pts(c), 2 * len(c), prev_pct, 2 * 20)

        streak, kind = 0, None
        for w in c.win.values[::-1]:
            k = "W" if w else "L"
            if kind is None:
                kind = k
            if k != kind:
                break
            streak += 1

        if strength >= cfg["streak_strong_pct"]:
            tier = "strong"
        elif strength <= cfg["streak_weak_pct"]:
            tier = "weak"
        else:
            tier = "average"
        thr = cfg["streak_thresholds"][tier]
        thr_k = thr["win"] if kind == "W" else thr["loss"]
        excess = max(0, streak - thr_k + 1) if kind else 0

        l5 = c.tail(5)
        last3 = res[res.season.between(cur - 3, cur - 1)]
        rec = {
            "gp": int(len(c)), "w": int(c.win.sum()), "l": int((~c.win & ~c.otl).sum()), "otl": int(c.otl.sum()),
            "pts_pct": float(pts(c) / (2 * len(c))) if len(c) else None,
            "strength": float(strength), "tier": tier,
            "streak": int(streak), "streak_kind": kind, "streak_threshold": int(thr_k) if kind else None,
            "streak_excess": int(excess),
            "last5": "".join("W" if w else ("O" if o else "L") for w, o in zip(l5.win, l5.otl)),
            "l5_gf": float(l5.gf.mean()) if len(l5) else None, "l5_ga": float(l5.ga.mean()) if len(l5) else None,
            "gf_home": float(c[c.home].gf.mean()) if c.home.any() else None,
            "ga_home": float(c[c.home].ga.mean()) if c.home.any() else None,
            "gf_away": float(c[~c.home].gf.mean()) if (~c.home).any() else None,
            "ga_away": float(c[~c.home].ga.mean()) if (~c.home).any() else None,
            "last_date": c.date.values[-1] if len(c) else None,
        }
        for L in lines:
            lg_cur = league.get(cur, {}).get(f"over_{L}", lg_hist[f"over_{L}"])
            prior = 0.5 * lg_cur + 0.5 * lg_hist[f"over_{L}"]
            rec[f"over_{L}"] = float((c.total > L).mean()) if len(c) else None
            rec[f"over_{L}_reg"] = shrink(float((c.total > L).sum()), len(c), prior, cfg["k_team_ou_games"])
            rec[f"over_{L}_3y"] = float((last3.total > L).mean()) if len(last3) else prior
            rec[f"over_{L}_home"] = float((c[c.home].total > L).mean()) if c.home.any() else None
            rec[f"over_{L}_away"] = float((c[~c.home].total > L).mean()) if (~c.home).any() else None
        out["teams"][t] = rec
    out["_results"] = teams
    return out


def h2h(results: dict, a: str, b: str, cur: int, cfg: dict, prior: dict) -> dict:
    r = results.get(a)
    if r is None:
        return {"n": 0}
    m = r[(r.opp == b) & (r.season >= cur - 3)]
    out = {"n": int(len(m)), "a_w": int(m.win.sum()), "avg_total": float(m.total.mean()) if len(m) else None,
           "games": [{"date": d, "score": f"{int(gf)}-{int(ga)}", "home": bool(h)}
                     for d, gf, ga, h in zip(m.date.values[-6:], m.gf.values[-6:], m.ga.values[-6:], m.home.values[-6:])][::-1]}
    for L in cfg["ou_lines"]:
        out[f"over_{L}_reg"] = shrink(float((m.total > L).sum()), len(m), prior[f"over_{L}"], cfg["k_h2h_games"])
    return out


# ------------------------------------------------------------- goalies
def goalie_frame(mp_goalies: dict[int, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for y, df in mp_goalies.items():
        d = df[_sit(df) == "all"]
        frames.append(pd.DataFrame({
            "pid": pd.to_numeric(d["playerId"], errors="coerce"), "season": y,
            "gp": _num(d, "games_played"), "h": _hours(d, "icetime"),
            "xga": _num(d, "xGoals"), "ga": _num(d, "goals"), "sa": _num(d, "ongoal"),
        }))
    if not frames:
        return pd.DataFrame(columns=["pid", "season", "gp", "h", "xga", "ga", "sa"])
    return pd.concat(frames).groupby(["pid", "season"], as_index=False).sum()


def goalie_profile(pid: int, gf: pd.DataFrame, logs: list[list[dict]], cur: int, cfg: dict,
                   league_ratio: float = 1.0) -> dict:
    g = gf[gf.pid == pid]
    gw_list = cfg["goalie_weights"]
    weights = {cur - i: w / max(gw_list) for i, w in enumerate(gw_list[:cfg["goalie_seasons"]])}
    gw = g[g.season.isin(weights)]
    ws = gw.season.map(weights)
    # --- 2. relative to the league: the league GA/xGA level is already in the team model
    raw_ratio = shrink(float((gw.ga * ws).sum()), float((gw.xga * ws).sum()), league_ratio, cfg["k_goalie_xga"])
    ratio = raw_ratio / league_ratio

    def window(seasons):
        s = g[g.season.isin(seasons)].sum(numeric_only=True)
        if s.get("gp", 0) <= 0:
            return None
        return {"gp": int(s.gp), "sv": 1 - s.ga / s.sa if s.sa else None,
                "gaa": s.ga / s.h if s.h else None, "xga": float(s.xga), "ga": float(s.ga),
                "gsax": float(s.xga - s.ga)}

    return {
        "ratio": float(ratio),  # >1 = lets in more than an average goalie would on the same shots
        "raw_ratio": float(raw_ratio), "league_ratio": float(league_ratio),
        "cur": window([cur]), "last3": window([cur - 1, cur - 2, cur - 3]),
        "logs": logs,
    }


def goalie_vs(logs: list[list[dict]], opp: str) -> dict | None:
    games = [g for season in logs for g in season if g["opp"] == opp and g["toi"] > 20]
    if not games:
        return None
    sa = sum(g["sa"] for g in games)
    ga = sum(g["ga"] for g in games)
    mins = sum(g["toi"] for g in games)
    return {"gp": len(games), "sv": 1 - ga / sa if sa else None, "gaa": ga / mins * 60 if mins else None}


def probable_starter(goalie_ids: list[int], glogs: dict, team_dates: list[str], b2b: bool, yesterday: str) -> int | None:
    recent = set(team_dates[-10:])
    starts = {}
    for pid in goalie_ids:
        cur = glogs.get(pid, [[]])[0]
        starts[pid] = sum(1 for g in cur if (g["gs"] or g["toi"] >= 40) and g["date"] in recent)
    last = {pid: sum(1 for g in (glogs.get(pid, [[], []]) + [[]])[1] if g["gs"] or g["toi"] >= 40) for pid in goalie_ids}
    order = sorted(goalie_ids, key=lambda p: (starts[p], last[p]), reverse=True)
    if not order:
        return None
    first = order[0]
    if b2b and len(order) > 1:
        cur = glogs.get(first, [[]])[0]
        if any((g["gs"] or g["toi"] >= 40) and g["date"] == yesterday for g in cur):
            return order[1]
    return first


# ------------------------------------------------------------- skaters
def skater_frame(mp_sk: dict[int, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for y, df in mp_sk.items():
        d = df[_sit(df) == "all"]
        frames.append(pd.DataFrame({
            "pid": pd.to_numeric(d["playerId"], errors="coerce"), "season": y,
            "posg": np.where(d["position"].astype(str) == "D", "D", "F") if "position" in d.columns else "F",
            "gp": _num(d, "games_played"), "h": _hours(d, "icetime"),
            "s": _num(d, "I_F_shotsOnGoal"), "g": _num(d, "I_F_goals"), "xg": _num(d, "I_F_xGoals"),
            "a": _num(d, "I_F_primaryAssists") + _num(d, "I_F_secondaryAssists"),
        }))
    if not frames:
        return pd.DataFrame(columns=["pid", "season", "posg", "gp", "h", "s", "g", "xg", "a"])
    f = pd.concat(frames)
    return f.groupby(["pid", "season", "posg"], as_index=False).sum()


def league_pos_rates(sf: pd.DataFrame, cur: int) -> dict:
    out = {}
    s = sf[sf.season.isin([cur, cur - 1, cur - 2])]
    for pg in ("F", "D"):
        d = s[s.posg == pg].sum(numeric_only=True)
        h, sh_, g_, gp_ = (float(d.get(k, 0) or 0) for k in ("h", "s", "g", "gp"))
        if h <= 0:
            continue
        out[pg] = {"s60": sh_ / h, "xg60": float(d.get("xg", 0)) / h, "a60": float(d.get("a", 0)) / h,
                   "sh": g_ / sh_ if sh_ else 0.0, "toi": h * 60 / gp_ if gp_ else 0.0}
    defaults = {"F": {"s60": 7.0, "xg60": 0.75, "a60": 1.1, "sh": 0.11, "toi": 15.0},
                "D": {"s60": 4.5, "xg60": 0.25, "a60": 0.9, "sh": 0.05, "toi": 20.0}}
    for pg in defaults:
        out.setdefault(pg, {})
        for k, v in defaults[pg].items():
            if not out[pg].get(k) or not np.isfinite(out[pg][k]):
                out[pg][k] = v
    return out


def _nhl_seasons(land: dict | None) -> dict[int, dict]:
    out = {}
    for s in (land or {}).get("seasonTotals", []) or []:
        if s.get("leagueAbbrev") != "NHL" or s.get("gameTypeId") != 2:
            continue
        y = int(str(s.get("season"))[:4])
        d = out.setdefault(y, {"gp": 0, "g": 0, "a": 0, "p": 0, "s": 0})
        d["gp"] += s.get("gamesPlayed", 0) or 0
        d["g"] += s.get("goals", 0) or 0
        d["a"] += s.get("assists", 0) or 0
        d["p"] += s.get("points", 0) or 0
        d["s"] += s.get("shots", 0) or 0
    return out


def _window(seasons: dict[int, dict], years: list[int] | None) -> dict | None:
    rows = [v for y, v in seasons.items() if (years is None or y in years) and v["gp"] > 0]
    if not rows:
        return None
    t = {k: sum(r[k] for r in rows) for k in ("gp", "g", "a", "p", "s")}
    n = len(rows)
    t.update({"seasons": n,
              "g_pg": t["g"] / t["gp"], "a_pg": t["a"] / t["gp"], "p_pg": t["p"] / t["gp"], "s_pg": t["s"] / t["gp"],
              "g_ps": t["g"] / n, "a_ps": t["a"] / n, "p_ps": t["p"] / n, "s_ps": t["s"] / n,
              "sh": t["g"] / t["s"] if t["s"] else None})
    return t


def skater_profile(p: dict, sf: pd.DataFrame, lgpos: dict, land: dict | None, logs: dict,
                   team_dates: list[str], cur: int, cfg: dict) -> dict:
    pid = p["id"]
    posg = "D" if p["pos"] == "D" else "F"
    L = lgpos[posg]
    years = [cur - i for i in range(cfg["lookback_seasons"])]
    sw = cfg["season_weights"]
    weights = {y: w / max(sw) for y, w in zip(years, sw)}
    me = sf[sf.pid == pid]
    mw = me[me.season.isin(weights)]
    ws = mw.season.map(weights).astype(float)
    W = {c: float((mw[c] * ws).sum()) for c in ("h", "s", "g", "xg", "a")}

    s60 = shrink(W["s"], W["h"], L["s60"], cfg["k_shots_hours"])
    xg60 = shrink(W["xg"], W["h"], L["xg60"], cfg["k_ixg_hours"])
    a60 = shrink(W["a"], W["h"], L["a60"], cfg["k_assist_hours"])

    seasons = _nhl_seasons(land)
    career_raw = ((land or {}).get("careerTotals") or {}).get("regularSeason") or {}
    c_g = career_raw.get("goals")
    c_s = career_raw.get("shots")
    if c_g is None or not c_s:
        c_g = sum(v["g"] for v in seasons.values()) or float(me.g.sum())
        c_s = sum(v["s"] for v in seasons.values()) or float(me.s.sum())
    career_sh = shrink(float(c_g), float(c_s), L["sh"], cfg["k_career_sh_shots"])
    recent_sh = shrink(float((mw.g * ws).sum()), float((mw.s * ws).sum()), career_sh, cfg["k_recent_sh_shots"])
    finish = shrink(W["g"], W["xg"], 1.0, cfg["k_finish_xg"])
    al = cfg["alpha_shots_vs_xg"]
    g60 = al * s60 * recent_sh + (1 - al) * xg60 * finish

    # ---- game logs: TOI, form, shots since last goal
    lc, lp = logs.get("cur", []), logs.get("prev", [])
    recent_team = team_dates[-cfg["active_last_n_team_games"]:]
    played_recent = (bool(lc) and lc[0]["date"] >= recent_team[0]) if recent_team else True
    recent = (lc + lp)[: cfg["toi_recent_games"]]
    toi_recent = float(np.mean([g["toi"] for g in recent])) if recent else None
    if len(lc) >= 5:
        toi_season = float(np.mean([g["toi"] for g in lc]))
    else:
        last = me[me.season == cur - 1]
        toi_season = float(last.h.sum() * 60 / last.gp.sum()) if len(last) and last.gp.sum() else None
    parts = [(v, w) for v, w in ((toi_recent, cfg["toi_recent_weight"]), (toi_season, 1 - cfg["toi_recent_weight"])) if v]
    toi_proj = sum(v * w for v, w in parts) / sum(w for _, w in parts) if parts else L["toi"]

    l5 = lc[:5]
    form = {"gp": len(l5), "g": sum(g["g"] for g in l5), "a": sum(g["a"] for g in l5),
            "p": sum(g["p"] for g in l5), "s": sum(g["s"] for g in l5),
            "toi": float(np.mean([g["toi"] for g in l5])) if l5 else None,
            "games": [{"d": g["date"], "o": g["opp"], "h": g["home"], "g": g["g"], "a": g["a"],
                       "s": g["s"], "t": round(g["toi"], 1)} for g in lc[:10]]}
    shots_since, games_since = 0, 0
    found = False
    for g in lc + lp:
        if g["g"] > 0:
            found = True
            break
        shots_since += g["s"]
        games_since += 1

    # ---- regression check on this season (talent from prior seasons only, so the
    #      result being tested doesn't pull its own expectation toward itself)
    mp_ = mw[mw.season != cur]
    wp = mp_.season.map(weights).astype(float)
    prior_sh = shrink(float((mp_.g * wp).sum()), float((mp_.s * wp).sum()), career_sh, cfg["k_recent_sh_shots"])
    prior_fin = shrink(float((mp_.g * wp).sum()), float((mp_.xg * wp).sum()), 1.0, cfg["k_finish_xg"])
    cs = me[me.season == cur].sum(numeric_only=True)
    cur_s = float(cs.get("s", 0)) or float(sum(g["s"] for g in lc))
    cur_g = float(cs.get("g", 0)) or float(sum(g["g"] for g in lc))
    cur_xg = float(cs.get("xg", 0))
    exp_g = al * cur_s * prior_sh + (1 - al) * (cur_xg * prior_fin if cur_xg else cur_s * prior_sh)
    z = (cur_g - exp_g) / math.sqrt(exp_g) if exp_g > 0.3 else 0.0
    # Poisson tail probabilities: how unusual is this season's goal total for his true talent?
    p_low = poisson_cdf(int(cur_g), exp_g)                                # P(X <= goals)
    p_high = 1 - poisson_cdf(int(cur_g) - 1, exp_g) if cur_g >= 1 else 1.0  # P(X >= goals)
    tail = cfg["regression_tail"]
    flag = None
    if cur_s >= cfg["regression_min_shots"]:
        if p_low < tail:
            flag = "due"   # scoring below talent -> positive regression expected
        elif p_high < tail:
            flag = "hot"   # scoring above talent -> negative regression expected

    def xg_window(yrs):
        s = me[me.season.isin(yrs)].sum(numeric_only=True)
        return {"xg": float(s.get("xg", 0)), "g": float(s.get("g", 0)), "s": float(s.get("s", 0)),
                "gp": float(s.get("gp", 0))} if s.get("gp", 0) else None

    if cur not in seasons and lc:
        seasons[cur] = {"gp": len(lc), "g": sum(g["g"] for g in lc), "a": sum(g["a"] for g in lc),
                        "p": sum(g["p"] for g in lc), "s": sum(g["s"] for g in lc)}

    birth = (land or {}).get("birthDate")
    age = None
    if birth:
        try:
            age = (dt.date.today() - dt.date.fromisoformat(birth)).days // 365
        except ValueError:
            pass

    return {
        "id": pid, "name": p["name"], "team": p["team"], "pos": p["pos"], "posg": posg, "num": p.get("num"),
        "age": age, "active": played_recent, "toi_proj": toi_proj, "toi_recent": toi_recent, "toi_season": toi_season,
        "rates": {"s60": s60, "xg60": xg60, "a60": a60, "g60": g60, "career_sh": career_sh,
                  "recent_sh": recent_sh, "finish": finish, "lg_sh": L["sh"]},
        "form": form,
        "drought": {"shots": shots_since, "games": games_since, "found": found,
                    "shots_per_goal": 1 / recent_sh if recent_sh else None,
                    # chance a shooter of his talent goes this many shots without scoring
                    "p": (1 - recent_sh) ** shots_since},
        "regress": {"g": cur_g, "exp": exp_g, "z": z, "flag": flag, "p_low": p_low, "p_high": p_high, "s": cur_s,
                    "sh": cur_g / cur_s if cur_s else None, "xg": cur_xg},
        "windows": {
            "cur": _window(seasons, [cur]),
            "prev3": _window(seasons, [cur - 1, cur - 2, cur - 3]),
            "prev5": _window(seasons, [cur - i for i in range(1, 6)]),
            "career": _window(seasons, None),
        },
        "xg_windows": {"cur": xg_window([cur]), "prev3": xg_window([cur - 1, cur - 2, cur - 3])},
    }


# ---------------------------------------------------------------- slate
def build_slate(raw: dict, cfg: dict, log=print) -> dict:
    cur, date = raw["season"], raw["date"]
    yesterday = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()

    tf = team_frame(raw["mp"]["teams"])
    if tf.empty or tf[tf.sit == "5on5"]["h"].sum() <= 0:
        raise RuntimeError("MoneyPuck team data could not be loaded (teams.csv). "
                           "Run `python -m nhlprops check` to see which download failed.")
    if not raw["mp"]["skaters"]:
        log("WARNING: MoneyPuck skater data missing - player rates fall back to league averages.")
    tm = TeamModel(tf, cur, cfg)
    hist = team_history(raw["league_games"], cur, date, cfg)
    results = hist.pop("_results")
    gf = goalie_frame(raw["mp"]["goalies"])
    sf = skater_frame(raw["mp"]["skaters"])
    lgpos = league_pos_rates(sf, cur)

    # league scoring environment (for player props)
    lt = tf[tf.sit == "all"].groupby("season")[["GF", "gp"]].sum()
    env_years = [cur - i for i in range(1, cfg["league_env_seasons"])]
    hist_gpg = [2 * lt.loc[y, "GF"] / lt.loc[y, "gp"] for y in env_years if y in lt.index and lt.loc[y, "gp"]]
    base_gpg = float(np.mean(hist_gpg)) if hist_gpg else 6.1
    if cur in lt.index and lt.loc[cur, "gp"]:
        cur_gpg_raw = 2 * lt.loc[cur, "GF"] / lt.loc[cur, "gp"]
        cur_gpg = shrink(lt.loc[cur, "GF"], lt.loc[cur, "gp"], base_gpg / 2, cfg["k_env_team_games"]) * 2
    else:
        cur_gpg_raw, cur_gpg = None, base_gpg
    env = float(np.clip(cur_gpg / base_gpg, 1 - cfg["env_cap"], 1 + cfg["env_cap"]))
    league = {
        "gpg_by_season": {int(y): round(2 * r.GF / r.gp, 3) for y, r in lt.iterrows() if r.gp},
        "cur_gpg": cur_gpg_raw, "cur_gpg_reg": cur_gpg, "base_gpg": base_gpg, "env": env,
        "results": hist["league"], "hist_over": hist["league_hist_over"],
        "fin_ev": tm.fin_ev, "fin_pp": tm.fin_pp, "ev_min": tm.lg_ev_min, "other_gpg": tm.lg_other_gpg,
        "pos": lgpos,
    }

    team_dates = {t: list(r[r.season == cur].date.values) for t, r in results.items()}
    teams_out, games_out, players_out, goalies_out = {}, [], [], {}

    def team_ctx(t):
        if t in teams_out:
            return teams_out[t]
        th = hist["teams"].get(t, {})
        teams_out[t] = {"abbr": t, **tm.profile(t), **th, "b2b": th.get("last_date") == yesterday}
        return teams_out[t]

    # goalies: league GA/xGA over the same window is the "average goalie" level
    gwin = gf[gf.season.isin([cur - i for i in range(cfg["goalie_seasons"])])]
    lg_ratio = float(gwin.ga.sum() / gwin.xga.sum()) if gwin.xga.sum() > 0 else 1.0
    league["goalie_ga_xga"] = lg_ratio
    for g in raw["goalies"]:
        prof = goalie_profile(g["id"], gf, raw["glogs"].get(g["id"], [[], [], []]), cur, cfg, lg_ratio)
        goalies_out[g["id"]] = {**g, **{k: v for k, v in prof.items() if k != "logs"}}

    sk_by_team: dict[str, list] = {}
    for p in raw["skaters"]:
        sk_by_team.setdefault(p["team"], []).append(p)

    for gm in raw["games"]:
        H, A = gm["home"], gm["away"]
        th, ta = team_ctx(H), team_ctx(A)
        starters = {}
        for t, ctx in ((H, th), (A, ta)):
            ids = [g["id"] for g in raw["goalies"] if g["team"] == t]
            starters[t] = probable_starter(ids, raw["glogs"], team_dates.get(t, []), ctx["b2b"], yesterday)
            for gid in ids:
                goalies_out[gid].setdefault("vs", {})[H if t == A else A] = goalie_vs(
                    raw["glogs"].get(gid, [[], [], []]), H if t == A else A)

        mh, ma = tm.mu_parts(th, ta), tm.mu_parts(ta, th)
        ha = cfg["home_adv"]
        fh = ha * (cfg["b2b_factor"] if th["b2b"] else 1) / (cfg["b2b_factor"] if ta["b2b"] else 1)
        fa = (1 / ha) * (cfg["b2b_factor"] if ta["b2b"] else 1) / (cfg["b2b_factor"] if th["b2b"] else 1)
        mu = {
            "home": {"g": (mh["ev"] + mh["pp"]) * fh, "o": mh["other"], "ev": mh["ev"] * fh, "pp": mh["pp"] * fh},
            "away": {"g": (ma["ev"] + ma["pp"]) * fa, "o": ma["other"], "ev": ma["ev"] * fa, "pp": ma["pp"] * fa},
        }
        hh = h2h(results, H, A, cur, cfg, hist["league_hist_over"])
        hist_p = {}
        for L in cfg["ou_lines"]:
            terms = [x for x in (th.get(f"over_{L}_reg"), ta.get(f"over_{L}_reg"),
                                 th.get(f"over_{L}_3y"), ta.get(f"over_{L}_3y"), hh.get(f"over_{L}_reg")) if x is not None]
            hist_p[str(L)] = float(np.mean(terms)) if terms else hist["league_hist_over"][f"over_{L}"]

        # default-setting prediction (saved to history for grading)
        gr_h = goalies_out[starters[A]]["ratio"] if starters.get(A) else 1.0
        gr_a = goalies_out[starters[H]]["ratio"] if starters.get(H) else 1.0
        dist = game_distribution(mu["home"]["g"] * gr_h + mu["home"]["o"], mu["away"]["g"] * gr_a + mu["away"]["o"],
                                 cfg["ou_lines"])
        games_out.append({**gm, "mu": mu, "starters": starters, "h2h": hh, "hist_over": hist_p, "default": dist,
                          "goalies": {t: [g["id"] for g in raw["goalies"] if g["team"] == t] for t in (H, A)}})

        # skaters
        lg_xga = tm.lg_rate("all", "xGA") or 2.9
        lg_sa = tm.lg_rate("all", "SA") or 30
        for t, opp, is_home in ((H, A, True), (A, H, False)):
            octx = team_ctx(opp)
            own = team_ctx(t)
            venue = cfg["home_adv"] if is_home else 1 / cfg["home_adv"]
            b2b = (cfg["b2b_factor"] if own["b2b"] else 1) / (cfg["b2b_factor"] if octx["b2b"] else 1)
            d_xg = octx["xga_all"] / lg_xga
            d_s = octx["sa_all"] / lg_sa
            for p in sk_by_team.get(t, []):
                prof = skater_profile(p, sf, lgpos, raw["landing"].get(p["id"]), raw["logs"].get(p["id"], {}),
                                      team_dates.get(t, []), cur, cfg)
                r = prof["rates"]
                hrs = prof["toi_proj"] / 60
                base = {
                    "g": r["g60"] * hrs * d_xg * venue * b2b * env,
                    "a": r["a60"] * hrs * d_xg * venue * b2b * env,
                    "s": r["s60"] * hrs * d_s * venue * b2b,
                }
                gr = goalies_out[starters[opp]]["ratio"] if starters.get(opp) else 1.0
                lam_g, lam_a = base["g"] * gr, base["a"] * gr
                players_out.append({
                    **prof, "game": gm["id"], "opp": opp, "home": is_home, "base": base,
                    "factors": {"opp_def": d_xg, "opp_shots": d_s, "venue": venue, "b2b": b2b, "env": env,
                                "goalie": gr},
                    "p": {"g": p_at_least_one(lam_g), "a": p_at_least_one(lam_a), "pt": p_at_least_one(lam_g + lam_a),
                          **{f"s{L}": p_over_poisson(base["s"], L) for L in cfg["sog_lines"]}},
                })

    log(f"Model built: {len(games_out)} games, {len(players_out)} skaters "
        f"({sum(p['active'] for p in players_out)} active)")
    return {"date": date, "season": cur, "league": league, "teams": teams_out, "games": games_out,
            "players": players_out, "goalies": {str(k): v for k, v in goalies_out.items()}}
