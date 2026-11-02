"""Player season projections: every skater's season simulated 10,000 times, game by game.

Per-game goal and assist rates come from the model (true-talent rates per 60 x projected ice time), the games left
from his team's schedule, a share of games missed, and uncertainty in the true rate (Gamma, shape K: the model's
rate is an estimate, not a known number). The settings are the ones the backtest (backtest_proj.py) found honest.

Output per player: goals / assists / points [10th, median, 90th percentile], pace (points per 82 so far),
milestone chances, Art Ross / Rocket Richard / most-assists chances, and a small histogram of final points.
"""
from __future__ import annotations

import numpy as np

# settings from the backtest (2022-24 seasons, projected from ~14 team games in, 1,984 skater-seasons): the 10-90% range
# held the real final points 79% of the time, median bias about 0 for regulars, mid scorers and top scorers alike
K = 14            # uncertainty in the true rate (Gamma shape)
AVAIL = 0.78      # average share of his team's remaining games a skater plays (injuries, scratches, trades, demotions)
AVAIL_TOI = 0.08  # ... rising by this much for every 6 minutes of ice time above 16 (regulars miss fewer games)
CONC = 1.5        # how much that share varies from player to player (Beta concentration: some miss half a season)
STRETCH = 0.2     # top-end stretch: rate x (rate / 0.5 points per game) ^ STRETCH (the model's shrinkage is a bit strong for stars)
SIMS = 10000
MILESTONES = {"g": (20, 30, 40, 50, 60), "a": (40, 60, 80), "p": (60, 80, 100, 120, 140)}


def project(players: dict, team_gp: dict, season_games: int = 82, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    rows = []
    for pid, p in players.items():
        if p.get("posg") not in ("F", "D") or not p.get("team"):
            continue
        cur = (p.get("windows") or {}).get("cur") or {}
        r = p.get("rates") or {}
        toi = p.get("toi_proj") or 0
        left = max(0, season_games - int(team_gp.get(p["team"], 0)))
        lg, la = (r.get("g60") or 0) * toi / 60, (r.get("a60") or 0) * toi / 60
        if not p.get("active"):
            lg = la = 0.0          # not in the lineup picture: counts only what he has
        car = ((p.get("windows") or {}).get("career") or {}).get("gp") or 0
        rows.append((str(pid), p, int(cur.get("g") or 0), int(cur.get("a") or 0), int(cur.get("gp") or 0), left, lg, la,
                     car - int(cur.get("gp") or 0) < 25))
    if not rows:
        return {}
    n = len(rows)
    out = {}
    G = np.zeros((n, SIMS), np.int32)
    A = np.zeros((n, SIMS), np.int32)
    for j, (_, p, g0, a0, gp, left, lg, la, _) in enumerate(rows):
        av = float(np.clip(AVAIL + AVAIL_TOI * ((p.get("toi_proj") or 16) - 16) / 6, 0.55, 0.95))
        if p.get("injury") is not None:
            av *= 0.8
        f = float(np.clip((lg + la) / 0.5, 0.2, 3)) ** STRETCH if lg + la > 0 else 1.0
        lg, la = lg * f, la * f
        share = rng.beta(av * CONC, (1 - av) * CONC, SIMS)
        games = rng.binomial(left, share) if left else np.zeros(SIMS, int)
        mg = rng.gamma(K, lg / K, SIMS) if lg > 0 else np.zeros(SIMS)
        ma = rng.gamma(K, la / K, SIMS) if la > 0 else np.zeros(SIMS)
        G[j] = g0 + rng.poisson(mg * games)
        A[j] = a0 + rng.poisson(ma * games)
    P = G + A

    def share_lead(M):        # chance of finishing first (ties split)
        top = M.max(axis=0)
        lead = (M == top)
        return (lead / lead.sum(axis=0)).mean(axis=1)
    ar, rr, am = share_lead(P), share_lead(G), share_lead(A)
    # award stand-ins: most points among rookies (Calder) and among defencemen (Norris), same simulated seasons
    sub_lead = {}
    for key, mask in (("rl", np.array([r[8] for r in rows])), ("dl", np.array([r[1].get("posg") == "D" for r in rows]))):
        if mask.sum() >= 2:
            idx = np.flatnonzero(mask)
            sub_lead[key] = dict(zip(idx.tolist(), share_lead(P[idx]).tolist()))
    pct = lambda x: [int(np.percentile(x, 10)), int(round(float(np.median(x)))), int(np.percentile(x, 90))]
    for j, (pid, p, g0, a0, gp, left, lg, la, rookie) in enumerate(rows):
        g, a, pt = G[j], A[j], P[j]
        lo, hi = int(np.percentile(pt, 1)), int(np.percentile(pt, 99))
        width = max(1, int(np.ceil((hi - lo + 1) / 30)))
        h, _ = np.histogram(pt, bins=range(lo, hi + width + 1, width))
        hm = h.max() or 1
        rec = {"g": pct(g), "a": pct(a), "p": pct(pt), "pace": round((g0 + a0) / gp * 82, 1) if gp else None,
               "gp": gp, "left": left, "ar": round(float(ar[j]), 4), "rr": round(float(rr[j]), 4), "am": round(float(am[j]), 4),
               "m": {f"{k}{v}": round(float(((g if k == "g" else a if k == "a" else pt) >= v).mean()), 3) for k, vs in MILESTONES.items() for v in vs},
               "h": [int(round(x / hm * 99)) for x in h], "h0": lo, "hw": width}
        if rookie:
            rec["rk"] = 1
        for key, d in sub_lead.items():
            if j in d:
                rec[key] = round(float(d[j]), 4)
        out[pid] = rec
    return out


# ---- goalies: saved goals above expected (GSAx) to the end of the season, for the Vezina view
G_SD = 0.035      # uncertainty in a goalie's true goals-against / expected-goals ratio
G_PRIOR = 10      # games of his three-season shot-quality-faced rate blended into this season's
VEZ_MIN_GP = 40   # a goalie needs a real workload to be a Vezina candidate


def project_goalies(goalies: dict, team_gp: dict, season_games: int = 82, seed: int = 9) -> dict:
    """Per goalie: final GSAx [10th, median, 90th], final games, and the chance of finishing first in GSAx among
    goalies with 40+ games (a stand-in for the Vezina, which general managers vote on)."""
    rng = np.random.default_rng(seed)
    by_team: dict[str, list] = {}
    for gid, g in (goalies or {}).items():
        if g.get("team") and g.get("raw_ratio") is not None:
            by_team.setdefault(g["team"], []).append((str(gid), g))
    rows = []
    for tm, gs in by_team.items():
        T = int(team_gp.get(tm, 0))
        left = max(0, season_games - T)
        # share of the team's remaining starts: this season's split, pulled toward a 60/40 starter/backup split
        raw = [((g.get("cur") or {}).get("gp") or 0) for _, g in gs]
        order = sorted(range(len(gs)), key=lambda i: -raw[i])
        prior = [0.0] * len(gs)
        for rank, i in enumerate(order[:2]):
            prior[i] = 0.6 if rank == 0 else 0.4
        sh = [(raw[i] + 8 * prior[i]) / (T + 8) if T + 8 else prior[i] for i in range(len(gs))]
        tot = sum(sh) or 1
        for i, (gid, g) in enumerate(gs):
            cur, l3 = g.get("cur") or {}, g.get("last3") or {}
            l3pg = (l3.get("xga") or 0) / l3["gp"] if l3.get("gp") else 2.9
            gp0 = int(cur.get("gp") or 0)
            xpg = ((cur.get("xga") or 0) + G_PRIOR * l3pg) / (gp0 + G_PRIOR)
            rows.append((gid, g, gp0, float(cur.get("gsax") or 0), left, sh[i] / tot, xpg, float(g["raw_ratio"])))
    if not rows:
        return {}
    n = len(rows)
    S = np.zeros((n, SIMS)); GP = np.zeros((n, SIMS), np.int32)
    for j, (_, g, gp0, gs0, left, share, xpg, ratio) in enumerate(rows):
        share = float(np.clip(share, 0.01, 0.95))
        sh = rng.beta(share * 8, (1 - share) * 8, SIMS)
        starts = rng.binomial(left, sh) if left else np.zeros(SIMS, int)
        xga = starts * xpg
        r = np.clip(rng.normal(ratio, G_SD, SIMS), 0.75, 1.3)
        S[j] = gs0 + xga - rng.poisson(xga * r)
        GP[j] = gp0 + starts
    elig = np.where(GP >= VEZ_MIN_GP, S, -1e9)
    top = elig.max(axis=0)
    lead = (elig == top) & (top > -1e9)
    vz = (lead / np.maximum(1, lead.sum(axis=0))).mean(axis=1)
    out = {}
    for j, (gid, g, gp0, gs0, left, share, xpg, ratio) in enumerate(rows):
        out[gid] = {"gsax": [round(float(np.percentile(S[j], q)), 1) for q in (10, 50, 90)], "gp": int(np.median(GP[j])),
                    "gp0": gp0, "vz": round(float(vz[j]), 4), "q": round(float((GP[j] >= VEZ_MIN_GP).mean()), 3)}
    return out
