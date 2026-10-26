"""Game markets backtest: regulation 1X2, puck line, team totals, both teams to score and 1st-period markets.

Uses the same point-in-time team ratings as the totals backtest (backtest_totals.season_rows), then the live
site's adjustments (total compression and the regulation tie boost) to build the score distribution.
1st-period goals come from the NHL daily score feed (goals with their period).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .backtest_totals import season_rows
from .grade import summarize
from .sources import NHL

N = 16


def pois(lam):
    out = np.zeros(N)
    out[0] = math.exp(-lam)
    for i in range(1, N):
        out[i] = out[i - 1] * lam / i
    return out


def markets(mh, ma, tie_boost, p1_share, p1_tie, en_q=0.0):
    j = np.outer(pois(mh * 0.985), pois(ma * 0.985))
    j[np.diag_indices(N)] *= tie_boost
    j /= j.sum()
    i_, k_ = np.indices(j.shape)
    hw, tie, aw = j[i_ > k_].sum(), j[i_ == k_].sum(), j[i_ < k_].sum()
    share = mh / (mh + ma)
    # final score: a regulation tie gets one more goal for the overtime/shootout winner
    fh, fa = np.zeros(N + 1), np.zeros(N + 1)
    btts = 0.0
    for a in range(N):
        for b in range(N):
            p = j[a, b]
            if a == b:
                fh[a + 1] += p * share; fa[b] += p * share
                fh[a] += p * (1 - share); fa[b + 1] += p * (1 - share)
                btts += p if a >= 1 else 0.0
            else:
                fh[a] += p; fa[b] += p
                btts += p if (a >= 1 and b >= 1) else 0.0
    out = {"h1": hw, "x": tie, "a2": aw,
           # empty net: a one-goal lead at the end of regulation becomes two with probability en_q
           "hpl": j[i_ - k_ >= 2].sum() + en_q * j[i_ - k_ == 1].sum(), "apl": j[k_ - i_ >= 2].sum() + en_q * j[k_ - i_ == 1].sum(),
           "btts": btts, "btts60": j[(i_ >= 1) & (k_ >= 1)].sum(), "btts2_60": j[(i_ >= 2) & (k_ >= 2)].sum()}
    for L in (2.5, 3.5):
        out[f"htt{L}"] = fh[int(L) + 1:].sum()
        out[f"att{L}"] = fa[int(L) + 1:].sum()
    # 1st period
    p1h, p1a = pois(mh * p1_share), pois(ma * p1_share)
    j1 = np.outer(p1h, p1a)
    j1[np.diag_indices(N)] *= p1_tie
    j1 /= j1.sum()
    tot = np.zeros(2 * N)
    for a in range(N):
        for b in range(N):
            tot[a + b] += j1[a, b]
    for L in (0.5, 1.5, 2.5):
        out[f"p1o{L}"] = tot[int(L) + 1:].sum()
    out["p1x"] = j1[i_ == k_].sum()
    return out


def p1_goals(src, dates, log=print):
    """game id -> (home 1st-period goals, away 1st-period goals) from the NHL daily score feed"""
    res = {}
    for d in sorted(set(dates)):
        js = src.json(f"{NHL}/score/{d}", f"nhl/score/{d}.json", None) or {}
        for g in js.get("games") or []:
            if g.get("gameState") not in ("OFF", "FINAL"):
                continue
            def ab(x):
                return x.get("default") if isinstance(x, dict) else x
            home = ab((g.get("homeTeam") or {}).get("abbrev"))
            h = a = 0
            for gl in g.get("goals") or []:
                pdsc = gl.get("periodDescriptor") or {}
                if pdsc.get("number") == 1 and pdsc.get("periodType", "REG") == "REG":
                    if ab(gl.get("teamAbbrev")) == home:
                        h += 1
                    else:
                        a += 1
            res[int(g["id"])] = (h, a)
    return res


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames, prev = [], pd.DataFrame()
    first = min(seasons)
    for y in range(first - 3, first):
        g, _, _ = season_boxscores(src, y, cfg["workers"], log)
        prev = pd.concat([prev, g])
    for y in sorted(seasons):
        df, games = season_rows(src, y, cfg, prev[prev.season >= y - 3] if len(prev) else prev, log)
        df = df.merge(games[["id", "hs", "as_", "last"]], on="id", how="left")
        p1 = p1_goals(src, [str(x)[:10] for x in games.date], log)
        df["p1h"] = df.id.map(lambda i: (p1.get(int(i)) or (np.nan, np.nan))[0])
        df["p1a"] = df.id.map(lambda i: (p1.get(int(i)) or (np.nan, np.nan))[1])
        frames.append(df)
        prev = pd.concat([prev, games])
    d = pd.concat(frames)
    d = d[d.gp_min >= 5].copy()
    c, tb = cfg.get("total_compression", 1.0), cfg.get("tie_boost", 1.0)

    def adj(r):
        lg = r.recent_lg
        s = (lg + c * (r.mh + r.ma - lg)) / (r.mh + r.ma) if lg and r.mh + r.ma > 0 else 1.0
        return r.mh * s, r.ma * s
    hm = d.apply(adj, axis=1, result_type="expand")
    d["mh2"], d["ma2"] = hm[0], hm[1]
    reg_tie = d["last"] != "REG"
    # regulation goals: in OT/SO games the final has one extra goal for the winner
    rh = np.where(reg_tie & (d.hs > d.as_), d.hs - 1, d.hs)
    ra = np.where(reg_tie & (d.as_ > d.hs), d.as_ - 1, d.as_)
    y = {"h1": (rh > ra), "x": (rh == ra), "a2": (rh < ra), "hpl": (d.hs - d.as_ >= 2), "apl": (d.as_ - d.hs >= 2),
         "btts": (d.hs >= 1) & (d.as_ >= 1), "btts60": (rh >= 1) & (ra >= 1), "btts2_60": (rh >= 2) & (ra >= 2), "htt2.5": d.hs > 2.5, "htt3.5": d.hs > 3.5, "att2.5": d.as_ > 2.5, "att3.5": d.as_ > 3.5}
    ok1 = d.p1h.notna()
    rep = {"seasons": seasons, "games": int(len(d)), "p1_games": int(ok1.sum())}
    tot_reg = (rh + ra).astype(float)
    rep["p1_share_actual"] = float((d.p1h[ok1] + d.p1a[ok1]).sum() / tot_reg[ok1].sum()) if ok1.any() else None
    # grid for the 1st-period share and tie boost (log loss over the 1st-period markets)
    best = None
    grid = []
    for share in (0.27, 0.28, 0.29, 0.30, 0.31, 0.32):
        for t1 in (1.0, 1.1, 1.2, 1.3):
            ll = 0.0
            sub = d[ok1]
            P = [markets(r.mh2, r.ma2, tb, share, t1) for r in sub.itertuples()]
            t = (sub.p1h + sub.p1a).values
            for key, yy in (("p1o0.5", t > 0.5), ("p1o1.5", t > 1.5), ("p1o2.5", t > 2.5), ("p1x", (sub.p1h == sub.p1a).values)):
                p = np.clip([x[key] for x in P], 1e-4, 1 - 1e-4)
                ll += float(-(yy * np.log(p) + (1 - yy) * np.log(1 - p)).mean())
            grid.append({"share": share, "tie": t1, "logloss": ll / 4})
            if best is None or ll < best[2]:
                best = (share, t1, ll)
    rep["p1_grid"] = grid
    rep["p1_best"] = {"share": best[0], "tie": best[1]}
    # empty-net term for the puck line
    en_grid, en_best = [], None
    yh, ya = np.asarray(y["hpl"]).astype(int), np.asarray(y["apl"]).astype(int)
    for q in (0.0, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5):
        Pq = [markets(r.mh2, r.ma2, tb, best[0], best[1], q) for r in d.itertuples()]
        ph = np.clip([x["hpl"] for x in Pq], 1e-4, 1 - 1e-4); pa = np.clip([x["apl"] for x in Pq], 1e-4, 1 - 1e-4)
        ll = float(-(yh * np.log(ph) + (1 - yh) * np.log(1 - ph)).mean() - (ya * np.log(pa) + (1 - ya) * np.log(1 - pa)).mean())
        en_grid.append({"q": q, "logloss": ll / 2})
        if en_best is None or ll < en_best[1]:
            en_best = (q, ll)
    rep["en_grid"], rep["en_best"] = en_grid, en_best[0]
    P = [markets(r.mh2, r.ma2, tb, best[0], best[1], en_best[0]) for r in d.itertuples()]
    res = {}
    for k, yy in y.items():
        p = np.clip([x[k] for x in P], 1e-4, 1 - 1e-4)
        res[k] = summarize(list(zip(p, np.asarray(yy).astype(int))))
    sub = d[ok1]
    P1 = [markets(r.mh2, r.ma2, tb, best[0], best[1]) for r in sub.itertuples()]
    t = (sub.p1h + sub.p1a).values
    for key, yy in (("p1o0.5", t > 0.5), ("p1o1.5", t > 1.5), ("p1o2.5", t > 2.5), ("p1x", (sub.p1h == sub.p1a).values)):
        p = np.clip([x[key] for x in P1], 1e-4, 1 - 1e-4)
        res[key] = summarize(list(zip(p, np.asarray(yy).astype(int))))
    rep["markets"] = res
    (out_dir / "markets.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "markets.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


NAMES = {"h1": "Home win (regulation)", "x": "Draw after 60 min", "a2": "Away win (regulation)",
         "hpl": "Home -1.5", "apl": "Away -1.5", "btts": "Both teams to score (incl. OT)", "btts60": "Both teams to score (60 min)", "btts2_60": "Both teams 2+ goals (60 min)",
         "htt2.5": "Home over 2.5", "htt3.5": "Home over 3.5", "att2.5": "Away over 2.5", "att3.5": "Away over 3.5",
         "p1o0.5": "1st period over 0.5", "p1o1.5": "1st period over 1.5", "p1o2.5": "1st period over 2.5", "p1x": "1st period draw"}


def to_md(r):
    L = [f"# Game markets backtest: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}",
         f"Games: {r['games']:,} (1st period data: {r['p1_games']:,}) · actual 1st-period share of regulation goals: "
         f"{(r['p1_share_actual'] or 0):.3f} · best 1st-period share {r['p1_best']['share']} · tie boost {r['p1_best']['tie']} · empty-net term {r.get('en_best')}",
         "Empty-net grid: " + " · ".join(f"{x['q']}: {x['logloss']:.4f}" for x in r.get('en_grid', [])), "",
         "| Market | Predicted | Happened | Brier | Skill |", "|---|---|---|---|---|"]
    for k, s in r["markets"].items():
        L.append(f"| {NAMES.get(k, k)} | {s['mean_pred']:.1%} | {s['hit_rate']:.1%} | {s['brier']:.4f} | {s['skill'] * 100:+.1f}% |")
    return "\n".join(L) + "\n"
