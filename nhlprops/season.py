"""Season simulation: playoff, round-by-round and Stanley Cup chances, and how much each game matters.

The rest of the regular season is played out many times from the real schedule and current standings, then the
playoffs (real NHL format: top 3 per division + 2 wild cards per conference, divisional bracket, best of 7).
Team strength: attack and defence goal rates from this season (goals and shot-based expected goals), pulled toward
last season's expected goals (20 games), the same rating the game-totals backtest uses. Each simulated season draws
every team's strength from a range that narrows as games are played (sigma, fitted in backtest_season), so early
in the season the chances are not overconfident.
Importance of a game for a team = its playoff chance in the simulations where it wins minus where it loses.
"""
from __future__ import annotations

import math

import numpy as np

DIVISIONS = {
    "Atlantic": ["BOS", "BUF", "DET", "FLA", "MTL", "OTT", "TBL", "TOR"],
    "Metropolitan": ["CAR", "CBJ", "NJD", "NYI", "NYR", "PHI", "PIT", "WSH"],
    "Central": ["CHI", "COL", "DAL", "MIN", "NSH", "STL", "UTA", "WPG", "ARI"],
    "Pacific": ["ANA", "CGY", "EDM", "LAK", "SEA", "SJS", "VAN", "VGK"],
}
CONF = {"East": ["Atlantic", "Metropolitan"], "West": ["Central", "Pacific"]}
DIV_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}
CONF_OF = {d: c for c, ds in CONF.items() for d in ds}


def shrink(x, n, prior, k):
    return (x + k * prior) / (n + k)


def _pois(lam, n=16):
    out = np.zeros(n)
    out[0] = math.exp(-lam)
    for i in range(1, n):
        out[i] = out[i - 1] * lam / i
    return out


def outcome_probs(mh: float, ma: float, tie_boost: float) -> tuple[float, float, float]:
    """regulation: home win, tie, away win (same score model as the site)"""
    j = np.outer(_pois(mh * 0.985), _pois(ma * 0.985))
    j[np.diag_indices(16)] *= tie_boost
    j /= j.sum()
    i, k = np.indices(j.shape)
    return float(j[i > k].sum()), float(j[i == k].sum()), float(j[i < k].sum())


def ratings(team_stats: dict, prior: dict, lg: float, k: float = 20.0) -> dict:
    """team -> (attack, defence) goals per game. team_stats: {t: (gp, gf, ga, xf, xa)} this season;
    prior: {t: (xgf_pg, xga_pg)} last season (scaled to this season's league level)."""
    out = {}
    plg = np.mean([v[0] for v in prior.values()]) if prior else lg
    for t in set(team_stats) | set(prior):
        gp, gf, ga, xf, xa = team_stats.get(t, (0, 0, 0, 0, 0))
        p = prior.get(t, (plg, plg))
        sc = lg / plg if plg else 1.0
        att = shrink(0.5 * gf + 0.5 * xf, gp, p[0] * sc, k)
        dfn = shrink(0.5 * ga + 0.5 * xa, gp, p[1] * sc, k)
        out[t] = (att, dfn, gp)
    return out


def simulate(teams: list[str], rat: dict, played: list[tuple], remaining: list[tuple], cfg: dict,
             n: int = 10000, sigma0: float = 0.25, sigma_k: float = 30.0, seed: int = 7, track: set | None = None) -> dict:
    """played: [(home, away, hs, as, last)], remaining: [(gid, home, away)].
    Returns per team chances and, for games in `track`, playoff chance if win / if lose for both teams."""
    rng = np.random.default_rng(seed)
    T = len(teams)
    ix = {t: i for i, t in enumerate(teams)}
    lg = float(np.mean([rat[t][0] for t in teams]))
    ha, tb = cfg.get("home_adv", 1.05), cfg.get("tie_boost", 1.0)
    # standings so far
    pts0, rw0, row0, w0 = (np.zeros(T) for _ in range(4))
    for h, a, hs, as_, last in played:
        if h not in ix or a not in ix or hs is None or as_ is None:
            continue
        win, lose = (h, a) if hs > as_ else (a, h)
        pts0[ix[win]] += 2; w0[ix[win]] += 1
        if last == "REG":
            rw0[ix[win]] += 1
        if last in ("REG", "OT"):
            row0[ix[win]] += 1
        if last in ("OT", "SO"):
            pts0[ix[lose]] += 1
    # strength uncertainty per simulated season (log-odds units), narrowing with games played
    sig = np.array([sigma0 * math.sqrt(sigma_k / (sigma_k + rat[t][2])) for t in teams])
    s = rng.normal(0, 1, (n, T)) * sig
    pts = np.tile(pts0, (n, 1)); rw = np.tile(rw0, (n, 1)); row = np.tile(row0, (n, 1)); w = np.tile(w0, (n, 1))
    base = {}
    track = track or set()
    tracked = {}
    ar = np.arange(n)
    for gid, h, a in remaining:
        if h not in ix or a not in ix:
            continue
        i, j = ix[h], ix[a]
        key = (h, a)
        if key not in base:
            mh = rat[h][0] * rat[a][1] / lg * ha
            ma = rat[a][0] * rat[h][1] / lg / ha
            base[key] = outcome_probs(mh, ma, tb)
        pH, pT, pA = base[key]
        d = s[:, i] - s[:, j]
        eh, ea = pH * np.exp(d), pA * np.exp(-d)
        sc = (1 - pT) / (eh + ea)
        ph, pa = eh * sc, ea * sc
        u = rng.random(n)
        reg_h = u < ph
        reg_a = (u >= ph) & (u < ph + pa)
        tie = ~(reg_h | reg_a)
        share = ph / (ph + pa)
        ot_h = tie & (rng.random(n) < share)
        ot_a = tie & ~ot_h
        so = tie & (rng.random(n) < 0.4)           # about 40% of overtime games go to a shootout
        hw = reg_h | ot_h
        pts[:, i] += 2 * hw + ot_a; pts[:, j] += 2 * (reg_a | ot_a) + ot_h
        w[:, i] += hw; w[:, j] += ~hw
        rw[:, i] += reg_h; rw[:, j] += reg_a
        row[:, i] += reg_h | (ot_h & ~so); row[:, j] += reg_a | (ot_a & ~so)
        if gid in track:
            tracked[gid] = (h, a, hw)
    # final ranking score: points, regulation wins, regulation+OT wins, wins, random
    score = pts * 1e9 + rw * 1e6 + row * 1e3 + w + rng.random((n, T)) * 0.5
    made = np.zeros((n, T), bool)
    r2 = np.zeros((n, T), bool); cf = np.zeros((n, T), bool); fin = np.zeros((n, T), bool); cup = np.zeros((n, T), bool)
    divw = np.zeros((n, T), bool)
    # playoff game: home team win probability with mean strengths, by pair (overtime until a goal)
    def pwin(hi, lo):     # hi has home ice; arrays of team indices
        out = np.empty(len(hi))
        for k, (x, y) in enumerate(zip(hi, lo)):
            out[k] = PW[x, y]
        return out
    PW = np.zeros((T, T))
    for x in range(T):
        for y in range(T):
            if x == y:
                continue
            mh = rat[teams[x]][0] * rat[teams[y]][1] / lg * ha
            ma = rat[teams[y]][0] * rat[teams[x]][1] / lg / ha
            pH, pT, pA = outcome_probs(mh, ma, tb)
            PW[x, y] = pH + pT * pH / (pH + pA)
    HOME = np.array([1, 1, 0, 0, 1, 0, 1], bool)    # 2-2-1-1-1, from the higher seed's view

    def series(a, b):
        """a, b: arrays (n,) of team indices; higher score has home ice. returns winner array"""
        sa_, sb_ = score[ar, a], score[ar, b]
        hi = np.where(sa_ >= sb_, a, b); lo = np.where(sa_ >= sb_, b, a)
        ph_home = PW[hi, lo]; ph_away = 1 - PW[lo, hi]
        d = s[ar, hi] - s[ar, lo]
        def adj(p):
            lp = np.log(p / (1 - p)) + d
            return 1 / (1 + np.exp(-lp))
        p_home, p_away = adj(ph_home), adj(ph_away)
        wins_hi = np.zeros(n, int); wins_lo = np.zeros(n, int)
        for g in range(7):
            p = p_home if HOME[g] else p_away
            live = (wins_hi < 4) & (wins_lo < 4)
            x = rng.random(n) < p
            wins_hi += live & x; wins_lo += live & ~x
        return np.where(wins_hi == 4, hi, lo)

    champs = {}
    for conf, divs in CONF.items():
        d_teams = [[ix[t] for t in DIVISIONS[d] if t in ix] for d in divs]
        if not all(d_teams):
            continue
        tops, rest = [], []
        for dt in d_teams:
            dt = np.array(dt)
            order = dt[np.argsort(-score[:, dt], axis=1)]      # (n, len)
            tops.append(order[:, :3]); rest.append(order[:, 3:])
        rest = np.concatenate(rest, axis=1)
        wc = rest[np.arange(n)[:, None], np.argsort(-score[np.arange(n)[:, None], rest], axis=1)][:, :2]
        for tp in tops:
            made[ar[:, None], tp] = True
            divw[ar, tp[:, 0]] = True
        made[ar[:, None], wc] = True
        # the better division winner plays the second wild card
        d1, d2 = tops[0][:, 0], tops[1][:, 0]
        d1_better = score[ar, d1] >= score[ar, d2]
        wc1, wc2 = wc[:, 0], wc[:, 1]
        opp1 = np.where(d1_better, wc2, wc1); opp2 = np.where(d1_better, wc1, wc2)
        b1a = series(d1, opp1); b1b = series(tops[0][:, 1], tops[0][:, 2])
        b2a = series(d2, opp2); b2b = series(tops[1][:, 1], tops[1][:, 2])
        for x in (b1a, b1b, b2a, b2b):
            r2[ar, x] = True
        f1 = series(b1a, b1b); f2 = series(b2a, b2b)
        cf[ar, f1] = True; cf[ar, f2] = True
        champ = series(f1, f2)
        fin[ar, champ] = True
        champs[conf] = champ
    if "East" in champs and "West" in champs:
        cup[ar, series(champs["East"], champs["West"])] = True
    res = {"teams": {t: {"playoffs": float(made[:, k].mean()), "r2": float(r2[:, k].mean()), "cf": float(cf[:, k].mean()),
                         "final": float(fin[:, k].mean()), "cup": float(cup[:, k].mean()), "div": float(divw[:, k].mean()),
                         "pts": float(pts[:, k].mean()), "pts_now": float(pts0[k]), "sigma": float(sig[k])}
                     for k, t in enumerate(teams)},
           "games": {}}
    for gid, (h, a, hw) in tracked.items():
        i, j = ix[h], ix[a]
        out = {}
        for t, k, won in ((h, i, hw), (a, j, ~hw)):
            pw_ = float(made[won, k].mean()) if won.any() else None
            pl_ = float(made[~won, k].mean()) if (~won).any() else None
            out[t] = {"win": pw_, "lose": pl_, "now": float(made[:, k].mean())}
        res["games"][gid] = out
    return res


def importance_level(swing: float | None) -> str:
    """playoff-chance swing between winning and losing this game"""
    if swing is None:
        return "low"
    return "critical" if swing >= 0.12 else "high" if swing >= 0.07 else "medium" if swing >= 0.035 else "low"


def live(raw: dict, slate: dict, cfg: dict, n: int = 10000, log=print) -> dict:
    """Futures for the website from the data the daily build already has."""
    from .model import _num, _sit
    cur = raw["season"]
    lg_df = raw["league_games"]
    g = lg_df[lg_df.season == cur]
    fin = g[g.final]
    teams = sorted(set(g.home) | set(g.away))
    gp = {t: 0 for t in teams}; gf = dict(gp); ga = dict(gp)
    played = []
    for r in fin.itertuples():
        played.append((r.home, r.away, r.hs, r.as_, r.last))
        for t, f, a in ((r.home, r.hs, r.as_), (r.away, r.as_, r.hs)):
            gp[t] += 1; gf[t] += f; ga[t] += a
    def mp_team(year):
        d = (raw["mp"].get("teams") or {}).get(year)
        if d is None:
            return {}
        a = d[_sit(d) == "all"]
        g_ = _num(a, "games_played").clip(lower=1)
        return {t: (float(xf), float(xa), float(n_)) for t, xf, xa, n_ in zip(a.team.astype(str), _num(a, "xGoalsFor"), _num(a, "xGoalsAgainst"), g_)}
    now, prev = mp_team(cur), mp_team(cur - 1)
    stats = {}
    for t in teams:
        xf, xa, n_ = now.get(t, (0.0, 0.0, 0.0))
        k = gp[t] / n_ if n_ else 0.0          # MoneyPuck can be a game behind: scale its per-game rate to our games
        stats[t] = (gp[t], gf[t], ga[t], xf * k, xa * k)
    prior = {t: (v[0] / v[2], v[1] / v[2]) for t, v in prev.items() if v[2]}
    lg_prev = np.mean([v[0] for v in prior.values()]) if prior else 3.05
    tot_g = sum(gf.values()); tot_gp = sum(gp.values())
    lg = shrink(tot_g, tot_gp, lg_prev, 400)
    rat = ratings(stats, prior, lg)
    rat = {t: rat[t] for t in teams if t in rat}
    remaining = [(int(r.id), r.home, r.away) for r in g[~g.final].sort_values("date").itertuples()]
    track = {int(x["id"]) for x in slate.get("games") or []}
    res = simulate(list(rat), rat, played, remaining, cfg, n=n, sigma0=cfg.get("season_sigma", 0.25),
                   sigma_k=cfg.get("season_sigma_k", 30.0), track=track)
    for t, v in res["teams"].items():
        v["div_name"], v["conf"] = DIV_OF.get(t), CONF_OF.get(DIV_OF.get(t))
        v["gp"], v["att"], v["def"] = gp.get(t, 0), round(rat[t][0], 3), round(rat[t][1], 3)
    for gid, v in res["games"].items():
        for t, x in v.items():
            sw = (x["win"] - x["lose"]) if x["win"] is not None and x["lose"] is not None else None
            x["swing"], x["level"] = sw, importance_level(sw)
    res["sims"], res["remaining"] = n, len(remaining)
    log(f"  season simulation: {n} seasons, {len(remaining)} games left, top Cup: "
        + ", ".join(f"{t} {v['cup']:.0%}" for t, v in sorted(res['teams'].items(), key=lambda kv: -kv[1]['cup'])[:3]))
    return res
