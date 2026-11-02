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
        out[pid] = rec
    return out
