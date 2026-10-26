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


def p_over_nb(lam: float, line: float, r: float = 10.0) -> float:
    """P(X > line) for a negative binomial with mean lam and size r (hits and blocks are overdispersed)"""
    if lam <= 0:
        return 0.0
    p = r / (r + lam)
    term = p ** r
    cdf = term
    for k in range(1, int(math.floor(line)) + 1):
        term *= (k + r - 1) / k * (1 - p)
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


def game_distribution(mu_h: float, mu_a: float, lines: list[float], tie_boost: float = 1.0) -> dict:
    """Totals with the 'tie -> +1 goal' settlement rule, plus win probabilities.
    Independent Poisson gives too few regulation ties (backtest 16% vs 22% real), so the diagonal is boosted."""
    reg_scale = 0.985  # ~1.5% of goals are scored in OT; keep regulation separate
    ph, pa = pois_pmf(mu_h * reg_scale), pois_pmf(mu_a * reg_scale)
    joint = np.outer(ph, pa)
    joint[np.diag_indices(len(ph))] *= tie_boost
    joint /= joint.sum()
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
    "CF": "shotAttemptsFor", "CA": "shotAttemptsAgainst",
    "FF": "unblockedShotAttemptsFor", "FA": "unblockedShotAttemptsAgainst",
    "HITF": "hitsFor", "HITA": "hitsAgainst", "BLKF": "blockedShotAttemptsFor", "BLKA": "blockedShotAttemptsAgainst",
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


def team_ranks(tf: pd.DataFrame, cur: int) -> dict:
    """League ranks (1 = best) for attack, defence, shot share, chance quality, special teams, goaltending, luck.
    Early in the season last season is blended in, worth ~10 games, fading out by game 30."""
    def sums(team, season, sit):
        r = tf[(tf.team == team) & (tf.season == season) & (tf.sit == sit)].sum(numeric_only=True)
        return r
    teams = sorted(set(tf[tf.season == cur].team))
    vals, blended = {}, False
    for t in teams:
        gp = float(sums(t, cur, "all").get("gp", 0))
        w = max(0.0, 10 * (1 - gp / 30))
        pgp = float(sums(t, cur - 1, "all").get("gp", 0))
        pw = (w / pgp) if pgp else 0.0
        blended |= pw > 0

        def S(sit):
            a, b = sums(t, cur, sit), sums(t, cur - 1, sit)
            return {k: float(a.get(k, 0)) + pw * float(b.get(k, 0)) for k in ("h", "gp", *TEAM_COLS)}
        e, a, pp, pk = S("5on5"), S("all"), S("5on4"), S("4on5")
        d = lambda x, y: x / y if y else None
        sh = d(e["GF"], e["SF"]); sv = 1 - d(e["GA"], e["SA"]) if e["SA"] else None
        vals[t] = {
            "att": d(e["xGF"], e["h"]), "def": d(e["xGA"], e["h"]),
            "cf": d(e["CF"], e["CF"] + e["CA"]) if e["CF"] + e["CA"] else None,
            "xgf": d(e["xGF"], e["xGF"] + e["xGA"]) if e["xGF"] + e["xGA"] else None,
            "hdcf": d(e["HDSF"], e["HDSF"] + e["HDSA"]) if e["HDSF"] + e["HDSA"] else None,
            "pp": d(pp["xGF"], pp["h"]), "pk": d(pk["xGA"], pk["h"]),
            "gsax": d(a["xGA"] - a["GA"], a["gp"]),
            "pdo": (sh + sv) if (sh is not None and sv is not None) else None,
        }
    lower_better = {"def", "pk"}
    out = {t: {} for t in teams}
    for m in ("att", "def", "cf", "xgf", "hdcf", "pp", "pk", "gsax", "pdo"):
        have = [t for t in teams if vals[t][m] is not None]
        order = sorted(have, key=lambda t: vals[t][m], reverse=m not in lower_better)
        for i, t in enumerate(order):
            out[t][m] = {"v": round(vals[t][m], 4), "rk": i + 1}
    return {"teams": out, "n": len(teams), "blended": blended}


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
                            "total": m.total.values, "last": m["last"].values})
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
            "recent6": [{"d": str(r.date)[:10], "o": r.opp, "h": bool(r.home), "gf": int(r.gf), "ga": int(r.ga),
                         "x": r.last if r.last in ("OT", "SO") else ""} for r in res.tail(6).iloc[::-1].itertuples()],
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
            "hit": _num(d, "I_F_hits"), "blk": _num(d, "shotsBlockedByPlayer"),
        }))
    if not frames:
        return pd.DataFrame(columns=["pid", "season", "posg", "gp", "h", "s", "g", "xg", "a", "hit", "blk"])
    f = pd.concat(frames)
    return f.groupby(["pid", "season", "posg"], as_index=False).sum()


def onice_impact(mp_sk: dict, cur: int) -> dict:
    """Per player at 5-on-5: expected goals for/against per 60 with him on the ice minus with him off it,
    shrunk toward zero by ice time (this season + 0.6 x last season)."""
    acc: dict[int, list] = {}
    for y, wgt in ((cur, 1.0), (cur - 1, 0.6)):
        df = (mp_sk or {}).get(y)
        if df is None or not len(df):
            continue
        d = df[_sit(df) == "5on5"]
        if not len(d) or "OnIce_F_xGoals" not in d.columns:
            continue
        on_h = _hours(d, "icetime")
        off_h = _num(d, "timeOnBench") / 3600
        for pid, a, b, c, e, f, g in zip(pd.to_numeric(d["playerId"], errors="coerce"), on_h, _num(d, "OnIce_F_xGoals"),
                                         _num(d, "OnIce_A_xGoals"), off_h, _num(d, "OffIce_F_xGoals"), _num(d, "OffIce_A_xGoals")):
            if pd.isna(pid):
                continue
            r = acc.setdefault(int(pid), [0.0] * 6)
            for i, v in enumerate((a, b, c, e, f, g)):
                r[i] += wgt * float(v)
    out = {}
    for pid, (onh, onf, ona, offh, offf, offa) in acc.items():
        if onh <= 0 or offh <= 0:
            continue
        k = onh / (onh + 15.0)
        out[pid] = {"rel_f": k * (onf / onh - offf / offh), "rel_a": k * (ona / onh - offa / offh), "h": onh}
    return out


def league_pos_rates(sf: pd.DataFrame, cur: int) -> dict:
    out = {}
    s = sf[sf.season.isin([cur, cur - 1, cur - 2])]
    for pg in ("F", "D"):
        d = s[s.posg == pg].sum(numeric_only=True)
        h, sh_, g_, gp_ = (float(d.get(k, 0) or 0) for k in ("h", "s", "g", "gp"))
        if h <= 0:
            continue
        out[pg] = {"s60": sh_ / h, "xg60": float(d.get("xg", 0)) / h, "a60": float(d.get("a", 0)) / h,
                   "sh": g_ / sh_ if sh_ else 0.0, "toi": h * 60 / gp_ if gp_ else 0.0,
                   "hit60": float(d.get("hit", 0)) / h, "blk60": float(d.get("blk", 0)) / h}
    defaults = {"F": {"s60": 7.0, "xg60": 0.75, "a60": 1.1, "sh": 0.11, "toi": 15.0, "hit60": 5.0, "blk60": 1.6},
                "D": {"s60": 4.5, "xg60": 0.25, "a60": 0.9, "sh": 0.05, "toi": 20.0, "hit60": 4.6, "blk60": 4.4}}
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
    # ---- hits and blocked shots per 60 (validated in backtest_hb): this season shrunk to last season, shrunk to position
    hb = {}
    for stat in ("hit", "blk"):
        pos60 = L.get(stat + "60") or 0.0
        prev = me[me.season == cur - 1]
        prior = shrink(float(prev[stat].sum()), float(prev.h.sum()), pos60, 8.0) if len(prev) else pos60
        curr = me[me.season == cur]
        hb[stat + "60"] = shrink(float(curr[stat].sum()), float(curr.h.sum()), prior, cfg["k_hb_hours"][stat])
    # ---- hit rates for the standard prop lines: last 5 / 10 / 20 games (this + last season) and this season
    allg = lc + lp
    hit_fns = hit_markets(cfg)
    wins = [allg[:5], allg[:10], allg[:20], lc]
    hr = {"n": [len(w) for w in wins]}
    for mk, fn in hit_fns.items():
        hr[mk] = [sum(1 for g in w if fn(g)) for w in wins]
    # ---- role: ice time over the last 5 games vs his usual (games 6-25)
    base_g = allg[5:25]
    role = None
    if len(l5) >= 4 and len(base_g) >= 8:
        t5 = float(np.mean([g["toi"] for g in l5]))
        tb = float(np.mean([g["toi"] for g in base_g]))
        role = {"toi5": round(t5, 1), "toi_base": round(tb, 1), "toi_delta": round(t5 - tb, 1),
                "ppp5": sum(g.get("ppp", 0) for g in l5)}
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

    def mp_season(y):
        r = me[me.season == y].sum(numeric_only=True)
        return {"xg": round(float(r.get("xg", 0)), 2), "xg_gp": int(r.get("gp", 0))} if r.get("gp", 0) else {}

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
        "rates": {"s60": s60, "xg60": xg60, "a60": a60, "g60": g60, "career_sh": career_sh, **hb,
                  "recent_sh": recent_sh, "finish": finish, "lg_sh": L["sh"]},
        "form": form, "hr": hr, "role": role,
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
        "xg_windows": {"cur": xg_window([cur]), "prev3": xg_window([cur - 1, cur - 2, cur - 3]),
                       "prev5": xg_window([cur - i for i in range(1, 6)])},
        "by_season": [{"y": y, **seasons[y], **mp_season(y)} for y in sorted(seasons, reverse=True)[:8] if y >= cur - 5],
    }


def shot_ratings(tf: pd.DataFrame, cur: int, cfg: dict) -> dict:
    """Shots on goal for / against per game for every team (as in backtest_shots): this season, shrunk toward
    last season (pulled 30% toward the league), scaled to this season's league level. Also league goals per shot."""
    out = {"teams": {}, "lg": 29.0, "gps": 0.095}
    try:
        a = tf[tf.sit == "all"]
        c, p = a[a.season == cur].set_index("team"), a[a.season == cur - 1].set_index("team")
        lg_prev = float(p.SF.sum() / p.gp.sum()) if len(p) and p.gp.sum() else 29.0
        n_cur = float(c.gp.sum()) if len(c) else 0.0
        lg = shrink(float(c.SF.sum()) if len(c) else 0.0, n_cur, lg_prev, 200.0)
        gps_prev = float(p.GF.sum() / p.SF.sum()) if len(p) and p.SF.sum() else 0.095
        gps = shrink(float(c.GF.sum()) if len(c) else 0.0, float(c.SF.sum()) if len(c) else 0.0, gps_prev, 3000.0)
        k = cfg["shots_k_games"]
        sc = lg / lg_prev if lg_prev else 1.0
        for t in set(c.index) | set(p.index):
            pf = (0.7 * float(p.SF[t] / p.gp[t]) + 0.3 * lg_prev) if t in p.index and p.gp[t] else lg_prev
            pa = (0.7 * float(p.SA[t] / p.gp[t]) + 0.3 * lg_prev) if t in p.index and p.gp[t] else lg_prev
            gp = float(c.gp[t]) if t in c.index else 0.0
            sf = float(c.SF[t]) if t in c.index else 0.0
            sa = float(c.SA[t]) if t in c.index else 0.0
            out["teams"][t] = {"sf": shrink(sf, gp, pf * sc, k), "sa": shrink(sa, gp, pa * sc, k),
                               "sf_pg": sf / gp if gp else None, "sa_pg": sa / gp if gp else None}
        out["lg"], out["gps"] = lg, gps
    except Exception:
        pass
    return out


def shots_block(sh: dict | None, gps: float, cfg: dict, ratio_h: float, ratio_a: float) -> dict | None:
    """Add expected saves for the default starters and line probabilities (used for grading; the site recomputes
    saves when you switch goalies)."""
    if not sh:
        return None
    play = cfg["saves_play_share"]
    sh["sv_h"] = sh["a"] * play * (1 - gps * ratio_h)     # home goalie faces the away team's shots
    sh["sv_a"] = sh["h"] * play * (1 - gps * ratio_a)
    r1, rt, rs = cfg["shots_dispersion"], cfg["total_shots_dispersion"], cfg["saves_dispersion"]

    def po(lam, L, r):
        return p_over_nb(lam, L, r) if r else p_over_poisson(lam, L)
    def around(lam, step):   # lines centred on the projection, as the site shows them
        c = math.floor(lam) + 0.5 - (1 if step == 2 and math.floor(lam) % 2 else 0)
        return [c + i * step for i in (-2, -1, 0, 1, 2)]
    sh["p"] = {**{f"h{L}": po(sh["h"], L, r1) for L in around(sh["h"], 1)},
               **{f"a{L}": po(sh["a"], L, r1) for L in around(sh["a"], 1)},
               **{f"t{L}": po(sh["h"] + sh["a"], L, rt) for L in around(sh["h"] + sh["a"], 2)},
               **{f"svh{L}": po(sh["sv_h"], L, rs) for L in around(sh["sv_h"], 1)},
               **{f"sva{L}": po(sh["sv_a"], L, rs) for L in around(sh["sv_a"], 1)}}
    return sh


def game_shots(shr: dict, H: str, A: str, cfg: dict) -> dict | None:
    """Expected shots on goal for each team in one game (the site turns these into line probabilities and saves)."""
    th, ta, lg = shr["teams"].get(H), shr["teams"].get(A), shr["lg"]
    if not th or not ta or not lg:
        return None
    ha = cfg["shots_home_adv"]
    c = cfg.get("shots_calibration", 1.0)
    return {"h": th["sf"] * ta["sa"] / lg * ha * c, "a": ta["sf"] * th["sa"] / lg / ha * c,
            "h_sf": th["sf_pg"], "h_sa": th["sa_pg"], "a_sf": ta["sf_pg"], "a_sa": ta["sa_pg"]}


def hb_factors(tf: pd.DataFrame, cur: int, venue_hits: dict) -> dict:
    """Matchup factors for hits and blocked shots (as in backtest_hb):
    arena = hits recorded per game in that building (scorer bias), opp_hit = hits made against the opponent,
    opp_blk = how many of the opponent's attempts get blocked. Each is shrunk to last season and then to 1.0."""
    out = {"arena": {}, "opp_hit": {}, "opp_blk": {}}
    try:
        a = tf[tf.sit == "all"]
        c, p = a[a.season == cur].set_index("team"), a[a.season == cur - 1].set_index("team")

        def per_game(df, col):
            return (df[col] / df.gp.where(df.gp > 0)).dropna()
        for key, col in (("opp_hit", "HITA"), ("opp_blk", "BLKF")):
            pc, pp = per_game(c, col), per_game(p, col)
            lg = float(pc.mean()) if len(pc) else float(pp.mean()) if len(pp) else 0.0
            if not lg:
                continue
            for t in set(pc.index) | set(pp.index):
                prior = 0.5 * float(pp[t]) + 0.5 * lg if t in pp.index else lg
                gp = float(c.gp.get(t, 0)) if t in c.index else 0.0
                val = shrink(float(pc.get(t, 0)) * gp, gp, prior, 10.0)
                out[key][t] = val / lg
        tot = [v[0] / v[1] for v in venue_hits.values() if v[1]]
        if tot:
            lg_h = float(np.mean(tot))
            for t, (h_, n_) in venue_hits.items():
                out["arena"][t] = shrink(h_, n_, lg_h, 8.0) / lg_h
    except Exception:
        pass
    return out


def hit_markets(cfg: dict) -> dict:
    """market -> test on one game log row (same keys the site uses for props)"""
    fns = {"g": lambda g: g["g"] >= 1, "a": lambda g: g["a"] >= 1, "pt": lambda g: g["g"] + g["a"] >= 1}
    for L in cfg["sog_lines"]:
        fns[f"s{L}"] = (lambda L_: (lambda g: g["s"] > L_))(L)
    return fns


def opponent_by_position(raw: dict) -> dict:
    """What each team has allowed this season to centres, wingers and defencemen, per game, with ranks
    (rank 1 = allows the most, i.e. the easiest matchup). Built from the skater game logs we already load."""
    pos_of = {p["id"]: ("D" if p["pos"] == "D" else "C" if p["pos"] == "C" else "W") for p in raw["skaters"]}
    tot: dict[str, dict] = {}
    games: dict[str, set] = {}
    for pid, lg in (raw.get("logs") or {}).items():
        grp = pos_of.get(int(pid) if not isinstance(pid, int) else pid)
        if not grp:
            continue
        for g in (lg or {}).get("cur", []):
            o = g.get("opp")
            if not o:
                continue
            games.setdefault(o, set()).add(g.get("game_id") or g.get("date"))
            t = tot.setdefault(o, {k: {"s": 0, "g": 0, "pt": 0} for k in ("C", "W", "D")})[grp]
            t["s"] += g["s"]; t["g"] += g["g"]; t["pt"] += g["g"] + g["a"]
    out = {}
    for o, by in tot.items():
        n = len(games.get(o, ()))
        if n < 3:
            continue
        out[o] = {"gp": n, **{k: {m: round(v / n, 2) for m, v in st.items()} for k, st in by.items()}}
    for k in ("C", "W", "D"):
        for m in ("s", "g", "pt"):
            order = sorted(out, key=lambda o: -out[o][k][m])
            for i, o in enumerate(order):
                out[o][k][m + "_rk"] = i + 1
            if order:
                lg = sum(out[o][k][m] for o in order) / len(order)
                for o in order:
                    out[o][k][m + "_lg"] = round(lg, 2)
    return out


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
    # every scheduled date per team (played or not) -> back-to-back detection for any future game
    lgall = raw["league_games"]
    sched_dates: dict[str, set] = {}
    for h_, a_, d_ in zip(lgall.home, lgall.away, lgall.date):
        sched_dates.setdefault(h_, set()).add(d_)
        sched_dates.setdefault(a_, set()).add(d_)
    for g_ in raw["games"]:
        sched_dates.setdefault(g_["home"], set()).add(g_["date"])
        sched_dates.setdefault(g_["away"], set()).add(g_["date"])

    def b2b_on(team, gdate):
        prev = (dt.date.fromisoformat(gdate) - dt.timedelta(days=1)).isoformat()
        return prev in sched_dates.get(team, set())

    teams_out, games_out, players_out, goalies_out = {}, [], {}, {}

    def team_ctx(t):
        if t not in teams_out:
            teams_out[t] = {"abbr": t, **tm.profile(t), **hist["teams"].get(t, {})}
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

    # ---------------- news: injuries, lineups
    from .news import NameIndex, norm
    news = raw.get("news") or {"injuries": {}, "goalies": {}, "lines": {}, "status": {}}
    idx = NameIndex(raw["skaters"] + raw["goalies"])
    injury_of: dict[int, dict] = {}
    team_injuries: dict[str, list] = {}
    for t, lst in news["injuries"].items():
        for inj in lst:
            m = idx.find(inj["name"], t)
            if m:
                injury_of[m["id"]] = inj
            team_injuries.setdefault(t, []).append({**inj, "id": m["id"] if m else None})

    # NHL.com game-day lineups: the final word for teams playing on the article's date
    nhl = news.get("nhl") or {}
    game_teams = {(gm["home"], gm["date"]) for gm in raw["games"]} | {(gm["away"], gm["date"]) for gm in raw["games"]}
    nhl_day = {t: r for t, r in (nhl.get("teams") or {}).items() if (t, nhl.get("date")) in game_teams}
    scratched: dict[int, bool] = {}
    for t, r in nhl_day.items():
        dressed_ids = {m["id"] for ln in r["lines"] if ln["pos"] in ("f", "d") for m in [idx.find(ln["name"], t)] if m}
        for inj in team_injuries.get(t, []):       # listed questionable but in tonight's lineup -> playing
            if inj["id"] in dressed_ids and inj["kind"] != "out":
                inj.update(kind="playing", status="Playing")
                injury_of[inj["id"]] = inj
        for x in r["injured"]:                     # NHL.com injured list -> out
            m = idx.find(x["name"], t)
            pid = m["id"] if m else None
            cur_inj = next((i for i in team_injuries.get(t, []) if (pid and i["id"] == pid) or norm(i["name"]) == norm(x["name"])), None)
            if cur_inj:
                if cur_inj["kind"] != "out":
                    cur_inj.update(kind="out", status="Out")
            else:
                cur_inj = {"name": m["name"] if m else x["name"], "status": "Out", "kind": "out",
                           "detail": x["detail"].capitalize(), "return": None, "id": pid}
                team_injuries.setdefault(t, []).append(cur_inj)
            if pid:
                injury_of[pid] = cur_inj
        for nm_ in r["scratched"]:
            m = idx.find(nm_, t)
            if m:
                scratched[m["id"]] = True

    profiles = {}
    for t, plist in sk_by_team.items():
        for p in plist:
            profiles[p["id"]] = skater_profile(p, sf, lgpos, raw["landing"].get(p["id"]), raw["logs"].get(p["id"], {}),
                                               team_dates.get(t, []), cur, cfg)
    # hits / blocks: recent box scores (the NHL game logs don't carry them)
    for pid_, prof_ in profiles.items():
        rows_ = (raw.get("hb_logs") or {}).get(pid_) or []
        rows_ = [r_ for r_ in rows_ if r_.get("toi", 0) > 0][:10]
        if not rows_:
            continue
        prof_["hb_games"] = [{"d": r_["date"], "o": r_["opp"], "h": r_["home"], "hit": r_["hit"], "blk": r_["blk"]} for r_ in rows_]
        w5, w10 = rows_[:5], rows_
        prof_["hr"]["hbn"] = [len(w5), len(w10)]
        for L in cfg["hit_lines"]:
            prof_["hr"][f"h{L}"] = [sum(1 for r_ in w if r_["hit"] > L) for w in (w5, w10)]
        for L in cfg["blk_lines"]:
            prof_["hr"][f"b{L}"] = [sum(1 for r_ in w if r_["blk"] > L) for w in (w5, w10)]

    def lineup_for(t):
        groups, source = {}, "estimated"
        lines = news["lines"].get(t) or []
        if t in nhl_day:
            lines = nhl_day[t]["lines"]
        for ln in lines:
            m = idx.find(ln["name"], t)
            if m:
                groups.setdefault(ln["group"], []).append(m["id"])
        import re as _re
        main = lambda k: bool(_re.fullmatch(r"[fd]\d", k))
        groups = {k: v for k, v in groups.items() if main(k) or _re.fullmatch(r"pp\d", k)}
        n_main = sum(len(v) for k, v in groups.items() if main(k))
        if n_main >= 15:
            source = "gameday" if t in nhl_day else "projected"
        else:
            groups = {}
            avail = [profiles[p["id"]] for p in sk_by_team.get(t, [])
                     if profiles[p["id"]]["active"] and injury_of.get(p["id"], {}).get("kind") != "out"]
            fw = sorted([q for q in avail if q["posg"] == "F"], key=lambda q: -q["toi_proj"])[:12]
            de = sorted([q for q in avail if q["posg"] == "D"], key=lambda q: -q["toi_proj"])[:6]
            for i in range(0, len(fw), 3):
                groups[f"f{i // 3 + 1}"] = [q["id"] for q in fw[i:i + 3]]
            for i in range(0, len(de), 2):
                groups[f"d{i // 2 + 1}"] = [q["id"] for q in de[i:i + 2]]
        import re as _re
        dressed = {pid for k, v in groups.items() if _re.fullmatch(r"[fd]\d", k) for pid in v}
        return {"source": source, "groups": groups, "dressed": sorted(dressed)}

    for t in sk_by_team:
        lu = lineup_for(t)
        ctx = team_ctx(t)
        ctx["lineup"] = lu
        ctx["injuries"] = team_injuries.get(t, [])
        ctx["news"] = ({"notes": nhl_day[t]["notes"], "updated": nhl.get("updated")} if t in nhl_day else None)
        dressed = set(lu["dressed"])
        for p in sk_by_team[t]:
            prof = profiles[p["id"]]
            inj = injury_of.get(p["id"])
            prof["injury"] = inj
            if inj and inj["kind"] == "out":
                prof["active"], prof["why"] = False, f"Out ({inj['status']})"
            elif lu["source"] == "gameday":
                prof["active"] = p["id"] in dressed
                prof["why"] = None if prof["active"] else ("Scratched" if scratched.get(p["id"]) else "Not in tonight's lineup")
            elif lu["source"] == "projected":
                prof["active"] = p["id"] in dressed
                prof["why"] = None if prof["active"] else "Not in projected lineup"
            else:
                prof["why"] = None if prof["active"] else "Not in recent lineups"
            players_out[p["id"]] = prof

    # ---------------- missing players: who's out, who gets their ice time, small team adjustment
    impact = onice_impact(raw["mp"].get("skaters") or {}, cur)
    for t in sk_by_team:
        ctx = team_ctx(t)
        plist = [profiles[p["id"]] for p in sk_by_team[t]]
        tot_pts = sum((q["windows"]["cur"] or {}).get("p", 0) for q in plist) or 1
        tot_g = sum((q["windows"]["cur"] or {}).get("g", 0) for q in plist) or 1
        recent3 = set(team_dates.get(t, [])[-3:])
        missing, adj_f, adj_a = [], 0.0, 0.0
        for q in plist:
            w = q["windows"]["cur"] or {}
            regular = w.get("gp", 0) >= 5 and (q["toi_season"] or 0) >= (12 if q["posg"] == "F" else 15)
            if q["active"] or not regular:
                continue
            played = {g["date"] for g in (raw["logs"].get(q["id"], {}) or {}).get("cur", [])}
            new = len(played & recent3) >= 2
            imp = impact.get(q["id"], {})
            missing.append({"id": q["id"], "name": q["name"], "pos": q["pos"], "posg": q["posg"], "why": q.get("why"),
                            "pts_share": w.get("p", 0) / tot_pts, "g_share": w.get("g", 0) / tot_g, "toi": q["toi_season"],
                            "new": new, "rel_f": imp.get("rel_f"), "rel_a": imp.get("rel_a")})
            if new and imp:
                share = min((q["toi_season"] or 0) * 0.8 / 60, 0.5)   # his share of 5-on-5 time
                adj_f -= 0.5 * imp["rel_f"] * share
                adj_a -= 0.5 * imp["rel_a"] * share
        # ice time to teammates at the same position (new absences only)
        for m_ in [x for x in missing if x["new"]]:
            rec = [q for q in plist if q["active"] and q["posg"] == m_["posg"]]
            tot = sum(q["toi_proj"] for q in rec) or 1
            # a replacement dresses and takes bottom-of-the-lineup minutes; teammates share the difference
            freed = 0.9 * max(0.0, (m_["toi"] or 0) - (11.0 if m_["posg"] == "F" else 16.0))
            for q in rec:
                add = min(freed * q["toi_proj"] / tot, 2.0)
                if add >= 0.2:
                    q["toi_proj"] += add
                    b = q.setdefault("toi_boost", {"min": 0.0, "from": []})
                    b["min"] = round(b["min"] + add, 1); b["from"].append(m_["name"])
        lg5 = tm.lg_rate("5on5", "xGF") or 2.5
        f_mult = float(np.clip(1 + adj_f / lg5, 0.95, 1.05)) if missing else 1.0
        a_mult = float(np.clip(1 + adj_a / lg5, 0.95, 1.05)) if missing else 1.0
        if f_mult != 1.0 or a_mult != 1.0:
            ctx["xgf5_base"], ctx["xga5_base"] = ctx["xgf5"], ctx["xga5"]
            ctx["xgf5"] *= f_mult; ctx["xga5"] *= a_mult; ctx["xga_all"] *= a_mult
        missing.sort(key=lambda x: -x["pts_share"])
        ctx["missing"] = {"players": missing[:8], "att": f_mult - 1, "def": a_mult - 1}

    hit_fns = hit_markets(cfg)
    opp_pos = opponent_by_position(raw)
    lg_xga = tm.lg_rate("all", "xGA") or 2.9
    lg_sa = tm.lg_rate("all", "SA") or 30
    hb_f = hb_factors(tf, cur, raw.get("venue_hits") or {})
    shr = shot_ratings(tf, cur, cfg)
    league["shots_pg"], league["goals_per_shot"] = shr["lg"], shr["gps"]
    league["saves_play_share"] = cfg["saves_play_share"]
    for gm in raw["games"]:
        H, A = gm["home"], gm["away"]
        gdate = gm["date"]
        yday = (dt.date.fromisoformat(gdate) - dt.timedelta(days=1)).isoformat()
        th, ta = team_ctx(H), team_ctx(A)
        b2b = {H: b2b_on(H, gdate), A: b2b_on(A, gdate)}
        starters, starter_status, starter_alt = {}, {}, {}
        reports = news["goalies"].get(gdate) or []
        for t in (H, A):
            ids = [g["id"] for g in raw["goalies"] if g["team"] == t]
            starters[t] = probable_starter(ids, raw["glogs"], team_dates.get(t, []), b2b[t], yday)
            starter_status[t] = "Projected"
            # every source's pick; the best-supported one wins (confirmed > very likely > NHL.com > likely > DF)
            picks = []
            for rep_ in (news.get("mp_starters") or {}).get(gm["id"], []) + \
                    (news.get("mp_starters") or {}).get(str(gm["id"]), []):
                last = norm(rep_["name"]).split()[-1] if rep_["name"] else ""
                cand = [i for i in ids if norm(goalies_out[i]["name"]).split()[-1:] == [last]]
                if not cand and last:  # spelling differences (Merilinen / Merilainen)
                    import difflib
                    cand = [i for i in ids if difflib.SequenceMatcher(
                        None, (norm(goalies_out[i]["name"]).split() or [""])[-1], last).ratio() >= 0.8]
                if len(cand) == 1:
                    st = rep_["status"].lower()
                    rank = 5 if "confirm" in st else 4 if "very" in st else 2
                    picks.append((rank, cand[0], f"{rep_['status']} {rep_['pct']:.0f}%"))
                    break
            nhl_g = (nhl_day.get(t) or {}).get("goalies") or []
            if nhl_g and gdate == nhl.get("date"):
                m = idx.find(nhl_g[0], t)
                if m and m["id"] in ids:
                    picks.append((3, m["id"], "Expected"))
            for rep_ in reports:
                if rep_.get("team") not in (None, t):
                    continue
                m = idx.find(rep_["name"], t)
                if m and m["id"] in ids:
                    st = rep_["status"].lower()
                    picks.append((5 if "confirm" in st and "unconfirm" not in st else 1, m["id"], rep_["status"].title()))
                    break
            if picks:
                picks.sort(key=lambda x: -x[0])
                best = picks[0]
                starters[t], starter_status[t] = best[1], best[2]
                rivals = [p_ for p_ in picks if p_[1] != best[1] and p_[0] >= 2]
                if best[0] < 5 and rivals:   # sources disagree and nobody has confirmed
                    starter_status[t] = "Uncertain"
                    starter_alt[t] = rivals[0][1]
            if starters[t] and injury_of.get(starters[t], {}).get("kind") == "out":
                alt = [i for i in ids if injury_of.get(i, {}).get("kind") != "out" and i != starters[t]]
                if alt:
                    starters[t] = alt[0]
            for gid in ids:
                o = A if t == H else H
                goalies_out[gid].setdefault("vs", {})[o] = goalie_vs(raw["glogs"].get(gid, [[], [], []]), o)

        mh, ma = tm.mu_parts(th, ta), tm.mu_parts(ta, th)
        ha, bf = cfg["home_adv"], cfg["b2b_factor"]
        fh = ha * (bf if b2b[H] else 1) / (bf if b2b[A] else 1)
        fa = (1 / ha) * (bf if b2b[A] else 1) / (bf if b2b[H] else 1)
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

        gr_h = goalies_out[starters[A]]["ratio"] if starters.get(A) else 1.0
        gr_a = goalies_out[starters[H]]["ratio"] if starters.get(H) else 1.0
        m_h, m_a = mu["home"]["g"] * gr_h + mu["home"]["o"], mu["away"]["g"] * gr_a + mu["away"]["o"]
        lg_tot, c = league["cur_gpg_reg"], cfg.get("total_compression", 1.0)
        sc = (lg_tot + c * (m_h + m_a - lg_tot)) / (m_h + m_a) if lg_tot and m_h + m_a > 0 else 1.0
        dist = game_distribution(m_h * sc, m_a * sc, cfg["ou_lines"], cfg.get("tie_boost", 1.0))
        dist["proj_h"], dist["proj_a"] = m_h * sc, m_a * sc

        props = []
        for t, opp, is_home in ((H, A, True), (A, H, False)):
            octx = team_ctx(opp)
            venue = ha if is_home else 1 / ha
            bb = (bf if b2b[t] else 1) / (bf if b2b[opp] else 1)
            d_xg = octx["xga_all"] / lg_xga
            d_s = octx["sa_all"] / lg_sa
            gr = goalies_out[starters[opp]]["ratio"] if starters.get(opp) else 1.0
            for p in sk_by_team.get(t, []):
                prof = profiles[p["id"]]
                r = prof["rates"]
                lg_ = raw["logs"].get(p["id"], {}) or {}
                vs = [g for g in (lg_.get("cur", []) + lg_.get("prev", [])) if g.get("opp") == opp]
                hv = {"n": len(vs), **{mk: sum(1 for g in vs if fn(g)) for mk, fn in hit_fns.items()}}
                hrs = prof["toi_proj"] / 60
                base = {"g": r["g60"] * hrs * d_xg * venue * bb * env,
                        "a": r["a60"] * hrs * d_xg * venue * bb * env,
                        "s": r["s60"] * hrs * d_s * venue * bb * cfg.get("sog_calibration", 1.0)}
                lam_g, lam_a = base["g"] * gr, base["a"] * gr
                if r.get("hit60") is not None:
                    arena = hb_f["arena"].get(H, 1.0)
                    base["hit"] = r["hit60"] * hrs * arena * hb_f["opp_hit"].get(opp, 1.0) ** 0.5
                    base["blk"] = r["blk60"] * hrs * hb_f["opp_blk"].get(opp, 1.0)
                    hbp = {**{f"h{L}": p_over_nb(base["hit"], L, cfg["hb_dispersion"]) for L in cfg["hit_lines"]},
                           **{f"b{L}": p_over_nb(base["blk"], L, cfg["hb_dispersion"]) for L in cfg["blk_lines"]}}
                else:
                    hbp = {}
                props.append({
                    "id": p["id"], "team": t, "opp": opp, "home": is_home, "base": base, "hv": hv,
                    "factors": {"opp_def": d_xg, "opp_shots": d_s, "venue": venue, "b2b": bb, "env": env, "goalie": gr},
                    "p": {"g": p_at_least_one(lam_g), "a": p_at_least_one(lam_a), "pt": p_at_least_one(lam_g + lam_a),
                          **{f"s{L}": p_over_poisson(base["s"], L) for L in cfg["sog_lines"]}, **hbp},
                })
        games_out.append({**gm, "mu": mu, "starters": starters, "starter_status": starter_status, "starter_alt": starter_alt, "b2b": b2b,
                          "h2h": hh, "hist_over": hist_p, "default": dist, "props": props,
                          "shots": shots_block(game_shots(shr, H, A, cfg), shr["gps"], cfg,
                                               goalies_out[starters[H]]["ratio"] if starters.get(H) else 1.0,
                                               goalies_out[starters[A]]["ratio"] if starters.get(A) else 1.0),
                          "goalies": {t: [g["id"] for g in raw["goalies"] if g["team"] == t] for t in (H, A)}})

    log(f"Model built: {len(games_out)} games, {len(players_out)} skaters "
        f"({sum(p['active'] for p in players_out.values())} expected to play)")
    return {"date": date, "season": cur, "league": league, "teams": teams_out, "games": games_out, "opp_pos": opp_pos,
            "team_ranks": team_ranks(tf, cur),
            "players": {str(k): v for k, v in players_out.items()},
            "goalies": {str(k): v for k, v in goalies_out.items()},
            "news_status": news.get("status", {})}
