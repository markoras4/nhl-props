"""Hits and blocked-shots props: point-in-time backtest (does not touch the website).

For every player-game we predict with information available before that game only:
  * the player's rate per 60 minutes: this season so far, shrunk toward last season, shrunk toward the
    position average (forwards / defencemen separately)
  * projected ice time: his recent games (no peeking at that night's actual TOI)
  * hits: an arena factor (scorekeepers in some buildings record many more hits) and an opponent factor
  * blocks: an opponent factor (how often that team's shots get blocked) and a venue factor
Expected count -> Poisson and negative binomial (over-dispersed) probabilities for the usual lines.
Compared with what happened, and with the simple approach most bettors use: his last-10-games hit rate.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .grade import summarize

LINES = {"hit": [0.5, 1.5, 2.5, 3.5], "blk": [0.5, 1.5, 2.5]}
DISPERSIONS = [None, 20, 10, 6, 4, 3]          # None = Poisson; smaller = more spread


def shrink(x, n, prior, k):
    return (x + k * prior) / (n + k)


def p_over(lam: float, line: float, r: float | None) -> float:
    k_max = int(math.floor(line))
    if lam <= 0:
        return 0.0
    cdf = 0.0
    if r is None:
        term = math.exp(-lam)
        for k in range(k_max + 1):
            if k:
                term *= lam / k
            cdf += term
    else:
        p = r / (r + lam)
        for k in range(k_max + 1):
            cdf += math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))
    return max(0.0, min(1.0, 1 - cdf))


def season_rows(sk: pd.DataFrame, games: pd.DataFrame, prev: pd.DataFrame | None, cfg: dict, log=print) -> pd.DataFrame:
    sk = sk[sk.toi > 0].copy()
    venue_of = dict(zip(games.id, games.home))
    sk["venue"] = sk.gid.map(venue_of)
    # ---- priors from last season
    pos_avg, prior_rate, prior_toi, prior_venue, prior_opp_blk, prior_opp_hit = {}, {}, {}, {}, {}, {}
    if prev is not None and len(prev):
        pv = prev[prev.toi > 0].copy()
        for stat in ("hit", "blk"):
            for pg, grp in pv.groupby("posg"):
                pos_avg[(stat, pg)] = float(grp[stat].sum() / (grp.toi.sum() / 60))
            per = pv.groupby(["pid", "posg"]).agg(x=(stat, "sum"), toi=("toi", "sum")).reset_index()
            for r in per.itertuples():
                prior_rate[(stat, r.pid)] = shrink(r.x, r.toi / 60, pos_avg[(stat, r.posg)], 8.0)
        for pid, grp in pv.groupby("pid"):
            prior_toi[pid] = float(grp.toi.mean())
    lg_hit_pg = lg_blk_pg = None
    if prev is not None and len(prev):
        g_per = prev.groupby("gid")[["hit", "blk"]].sum()
        lg_hit_pg, lg_blk_pg = float(g_per.hit.mean()), float(g_per.blk.mean())
    # ---- walk the season day by day
    cum: dict[tuple, list] = {}            # (pid) -> [hits, blk, toi_min, gp]
    recent_toi: dict[int, list] = {}
    venue_hits: dict[str, list] = {}       # venue team -> [hits recorded, games]
    opp_blk: dict[str, list] = {}          # team -> [shots of theirs that got blocked, games]
    opp_hit: dict[str, list] = {}          # team -> [hits made against them, games]
    lg = {"hit": 0.0, "blk": 0.0, "games": 0}
    rows = []
    for date, day in sk.sort_values("date").groupby("date", sort=True):
        lg_h = shrink(lg["hit"], lg["games"], lg_hit_pg or 45.0, 50)     # hits per game, both teams
        lg_b = shrink(lg["blk"], lg["games"], lg_blk_pg or 28.0, 50)
        for r in day.itertuples():
            c = cum.get(r.pid, [0, 0, 0.0, 0])
            hours = c[2] / 60
            rt = recent_toi.get(r.pid, [])
            if rt:
                toi = 0.6 * float(np.mean(rt[-5:])) + 0.4 * float(np.mean(rt))
            elif r.pid in prior_toi:
                toi = prior_toi[r.pid]
            else:
                continue                      # no history at all: skip (the site needs a few games too)
            vh = venue_hits.get(r.venue, [0, 0])
            arena = shrink(vh[0], vh[1], lg_h, 8) / lg_h
            ob = opp_blk.get(r.opp, [0, 0])
            opp_b = shrink(ob[0], ob[1], lg_b / 2, 10) / (lg_b / 2)
            oh = opp_hit.get(r.opp, [0, 0])
            opp_h = shrink(oh[0], oh[1], lg_h / 2, 10) / (lg_h / 2)
            out = {"season": int(str(r.date)[:4]) if str(r.date)[5:7] > "08" else int(str(r.date)[:4]) - 1,
                   "date": r.date, "pid": r.pid, "posg": r.posg, "home": r.home, "gp_cur": c[3],
                   "hit": r.hit, "blk": r.blk, "toi_proj": toi}
            for stat, idx in (("hit", 0), ("blk", 1)):
                base = pos_avg.get((stat, r.posg), 2.0 if stat == "hit" else 1.2)
                prior = prior_rate.get((stat, r.pid), base)
                rate = shrink(c[idx], hours, prior, 6.0)
                lam_plain = rate * toi / 60
                if stat == "hit":
                    lam = lam_plain * arena * (opp_h ** 0.5)
                else:
                    lam = lam_plain * opp_b
                out[f"{stat}_lam"], out[f"{stat}_lam_plain"] = lam, lam_plain
            rows.append(out)
        # update state after the day
        for gid, g in day.groupby("gid"):
            v = g.venue.iloc[0]
            vh = venue_hits.setdefault(v, [0, 0]); vh[0] += g.hit.sum(); vh[1] += 1
            for team, tg in g.groupby("team"):
                opp = tg.opp.iloc[0]
                ob = opp_blk.setdefault(opp, [0, 0]); ob[0] += tg.blk.sum(); ob[1] += 1   # our blocks = their shots blocked
                oh = opp_hit.setdefault(opp, [0, 0]); oh[0] += tg.hit.sum(); oh[1] += 1
            lg["hit"] += g.hit.sum(); lg["blk"] += g.blk.sum(); lg["games"] += 1
        for r in day.itertuples():
            c = cum.setdefault(r.pid, [0, 0, 0.0, 0])
            c[0] += r.hit; c[1] += r.blk; c[2] += r.toi; c[3] += 1
            recent_toi.setdefault(r.pid, []).append(r.toi)
    return pd.DataFrame(rows)


def add_last10(df: pd.DataFrame, sk: pd.DataFrame) -> pd.DataFrame:
    """Each player's last-10-games results before the game (what a bettor sees in the app)."""
    sk = sk[sk.toi > 0].sort_values("date")
    hist = {}
    l10 = {}
    for r in sk.itertuples():
        h = hist.setdefault(r.pid, [])
        l10[(r.pid, r.date)] = list(h[-10:])
        h.append((r.hit, r.blk))
    df["l10"] = [l10.get((p, d), []) for p, d in zip(df.pid, df.date)]
    return df


def evaluate(df: pd.DataFrame) -> dict:
    rep = {}
    d = df[(df.gp_cur >= 5)].copy()          # players with at least 5 games this season, like the live site
    rep["rows"] = int(len(d))
    for stat, lines in LINES.items():
        y_all = d[stat].values
        lam, lam_plain = d[f"{stat}_lam"].values, d[f"{stat}_lam_plain"].values
        rep[f"{stat}_mean_actual"] = float(y_all.mean())
        rep[f"{stat}_mean_pred"] = float(lam.mean())
        rep[f"{stat}_corr"] = float(np.corrcoef(lam, y_all)[0, 1])
        # pick the dispersion by log loss over all lines
        best = None
        disp_res = []
        for r in DISPERSIONS:
            ll = 0.0
            for L in lines:
                y = (y_all > L).astype(int)
                p = np.clip([p_over(x, L, r) for x in lam], 1e-4, 1 - 1e-4)
                ll += float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
            disp_res.append({"r": r, "logloss": ll / len(lines)})
            if best is None or ll < best[1]:
                best = (r, ll)
        rep[f"{stat}_dispersion"] = disp_res
        r_best = best[0]
        rep[f"{stat}_r"] = r_best
        per_line = []
        for L in lines:
            y = (y_all > L).astype(int)
            pm = np.array([p_over(x, L, r_best) for x in lam])
            pp = np.array([p_over(x, L, r_best) for x in lam_plain])
            l10 = np.array([np.mean([(h[0] if stat == "hit" else h[1]) > L for h in hh]) if len(hh) >= 5 else np.nan for hh in d.l10])
            ok = ~np.isnan(l10)
            l10c = np.clip(l10, 0.03, 0.97)
            base = float(y.mean())
            s_model = summarize(list(zip(np.clip(pm, 1e-4, 1 - 1e-4), y)))
            s_plain = summarize(list(zip(np.clip(pp, 1e-4, 1 - 1e-4), y)))
            s_l10 = summarize(list(zip(l10c[ok], y[ok])))
            s_model_ok = summarize(list(zip(np.clip(pm[ok], 1e-4, 1 - 1e-4), y[ok])))
            per_line.append({"line": L, "n": int(len(y)), "base_rate": base,
                             "model": s_model, "no_arena_opp": s_plain, "last10": s_l10, "model_same_rows": s_model_ok})
        rep[f"{stat}_lines"] = per_line
    return rep


def run(src, seasons: list[int], cfg: dict, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    prev_sk = None
    for y in [min(seasons) - 1] + sorted(seasons):
        games, sk, _ = season_boxscores(src, y, cfg["workers"], log)
        if "hit" not in sk.columns:
            raise RuntimeError("boxscores have no hits column")
        if y in seasons:
            log(f"  {y}: predicting {len(sk)} player-games")
            df = season_rows(sk, games, prev_sk, cfg, log)
            df = add_last10(df, sk)
            frames.append(df)
        prev_sk = sk
    d = pd.concat(frames)
    rep = {"seasons": seasons, **evaluate(d)}
    (out_dir / "hits_blocks.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "hits_blocks.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r: dict) -> str:
    L = [f"# Hits & blocks backtest: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}",
         f"Player-games evaluated (5+ games played this season): {r['rows']:,}", ""]
    for stat, name in (("hit", "Hits"), ("blk", "Blocked shots")):
        L += [f"## {name}",
              f"Average per game: predicted {r[f'{stat}_mean_pred']:.3f} · actual {r[f'{stat}_mean_actual']:.3f} · correlation {r[f'{stat}_corr']:.3f}",
              f"Best spread: {'Poisson' if r[f'{stat}_r'] is None else 'negative binomial r=' + str(r[f'{stat}_r'])}  ("
              + " · ".join(f"{'P' if x['r'] is None else x['r']}: {x['logloss']:.4f}" for x in r[f"{stat}_dispersion"]) + ")", "",
              "| Line | Base rate | Model Brier | Skill | No arena/opp skill | Last-10 Brier | Last-10 skill | Model skill (same rows) |",
              "|---|---|---|---|---|---|---|---|"]
        for x in r[f"{stat}_lines"]:
            L.append(f"| over {x['line']} | {x['base_rate']:.1%} | {x['model']['brier']:.4f} | {x['model']['skill'] * 100:+.1f}% | "
                     f"{x['no_arena_opp']['skill'] * 100:+.1f}% | {x['last10']['brier']:.4f} | {x['last10']['skill'] * 100:+.1f}% | {x['model_same_rows']['skill'] * 100:+.1f}% |")
        L += ["", "Calibration (model, over the middle line):", "", "| Predicted | n | Predicted avg | Happened |", "|---|---|---|---|"]
        mid = r[f"{stat}_lines"][1]["model"]
        for b in mid.get("bins", []):
            L.append(f"| {b['lo']:.0%}-{b['hi']:.0%} | {b['n']} | {b['pred']:.1%} | {b['actual']:.1%} |")
        L.append("")
    return "\n".join(L) + "\n"
