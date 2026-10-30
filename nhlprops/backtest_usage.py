"""Usage change: when a player's shooting this season breaks from his history, should the model trust this season more?
(does not touch the website)

The live model blends seasons with fixed weights (this season 5, last 4, then 3/2/1), so 12 games of a new role count
for little against 300 old games. Shot volume settles fast, finishing slowly. For every player-game (info strictly
before the game) we rebuild his shots/60 and individual xG/60 with this season's weight multiplied by m:
  global       m applies to everyone
  conditional  m applies only when this season's rate is z or more standard deviations from his history (8+ games)
m for shots and m for xG are fitted on the older seasons, judged on the newer ones (Brier, log loss, calibration).
Key table: players whose volume / xG jumped (z >= 2): did they keep it (model now underpredicts them) or fall back?
In-season xG per game comes from the play-by-play shot locations (same small model as the website's game logs).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict, p_over, shrink
from .grade import summarize
from .model import game_ixg
from .shots import collect

FORM = {"g": 40.0, "s": 80.0}
MS = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0]
ZS = [2.0]


def frames(src, seasons, cfg, log):
    out = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        if sk.empty:
            continue
        pri = prior_frames(src, season)
        shots = collect(src, games.assign(season=season, final=True), season, Path("data/cache/shots_bt"), cfg["workers"], log)
        ixg = game_ixg(shots)
        att = {}
        for gid, g in shots.items():
            for pid, x, y, k, team in g["s"]:
                att[(int(gid), int(pid))] = att.get((int(gid), int(pid)), 0) + 1
        df = predict(sk, pri, games, go, season, cfg, prior_beta=1.0)
        df["xg"] = [ixg.get((int(p), str(d)), 0.0) for p, d in zip(df.pid, df.date)]
        df["att"] = [att.get((int(g), int(p)), 0) for g, p in zip(df.gid, df.pid)]
        df = df.sort_values(["pid", "date", "gid"])
        g = df.groupby("pid")
        df["c_xg"] = g.xg.cumsum() - df.xg
        df["c_att"] = g.att.cumsum() - df.att
        for c in ("g", "a", "s", "toi"):
            df[f"r10_{c}"] = g[c].transform(lambda x: x.shift(1).rolling(10, min_periods=1).sum()).fillna(0)
        P = pri["sk"]
        L = P[P.season >= season - 3].groupby("posg")[["h", "s", "g", "xg", "gp"]].sum()
        lg = {pg: {"s60": r.s / r.h, "xg60": r.xg / r.h, "sh": r.g / r.s, "toi": r.h * 60 / r.gp} for pg, r in L.iterrows()}
        for kk in ("s60", "xg60", "sh", "toi"):
            df[f"L_{kk}"] = df.posg.map({pg: v[kk] for pg, v in lg.items()})
        prev_gp = P.groupby("pid").gp.sum() if len(P) else pd.Series(dtype=float)
        df["exp_gp"] = df.pid.map(prev_gp).fillna(0)
        df["season"] = season
        keep = ["season", "pid", "posg", "date", "g", "a", "s", "lam_g", "lam_a", "lam_s", "toi_proj", "c_gp", "c_toi", "c_s", "c_g",
                "c_xg", "c_att", "r10_g", "r10_a", "r10_s", "r10_toi", "wh", "ws", "wg", "wxg", "car_s", "car_g", "exp_gp", "L_s60", "L_xg60", "L_sh", "L_toi"]
        out.append(df[keep])
        log(f"  {season}: {len(df):,} player-games, in-season xG for {int((df.xg > 0).sum()):,}")
    return pd.concat(out, ignore_index=True)


def parts(d, cfg):
    """pieces that do not depend on m"""
    tf = (d.toi_proj / d.L_toi).clip(0.4, 1.8)
    h = d.c_toi / 60
    Ls, Lx = d.L_s60 * tf, d.L_xg60 * tf
    career_sh = shrink(d.car_g + d.c_g, d.car_s + d.c_s, d.L_sh, cfg["k_career_sh_shots"])
    recent_sh = shrink(d.c_g + d.wg, d.c_s + d.ws, career_sh, cfg["k_recent_sh_shots"])
    fin_bt = shrink(d.wg, d.wxg, 1.0, cfg["k_finish_xg"])
    fin = shrink(d.wg + d.c_g, d.wxg + d.c_xg, 1.0, cfg["k_finish_xg"])
    s60_bt = shrink(d.c_s + d.ws, h + d.wh, Ls, cfg["k_shots_hours"])
    xg60_bt = shrink(d.wxg, d.wh, Lx, cfg["k_ixg_hours"])
    al = cfg["alpha_shots_vs_xg"]
    g60_bt = al * s60_bt * recent_sh + (1 - al) * xg60_bt * fin_bt
    # how far this season is from his history (Poisson-style z on counts so far)
    s_hist = shrink(d.ws, d.wh, Ls, cfg["k_shots_hours"])
    x_hist = shrink(d.wxg, d.wh, Lx, cfg["k_ixg_hours"])
    zs = (d.c_s - h * s_hist) / np.sqrt(h * s_hist + 1e-9)
    zx = (d.c_xg - h * x_hist) / np.sqrt(h * x_hist + 1e-9)
    return {"h": h, "Ls": Ls, "Lx": Lx, "recent_sh": recent_sh, "fin": fin, "s60_bt": s60_bt, "g60_bt": g60_bt, "al": al,
            "zs": np.where(d.c_gp >= 8, zs, 0.0), "zx": np.where(d.c_gp >= 8, zx, 0.0)}


def lams(d, pt, cfg, ms, mx, z=None):
    h = pt["h"]
    if z is not None:                       # conditional: extra weight only for a clear break from his history
        ms = np.where(np.abs(pt["zs"]) >= z, ms, 1.0)
        mx = np.where(np.abs(pt["zx"]) >= z, mx, 1.0)
    s60 = shrink(ms * d.c_s + d.ws, ms * h + d.wh, pt["Ls"], cfg["k_shots_hours"])
    xg60 = shrink(mx * d.c_xg + d.wxg, mx * h + d.wh, pt["Lx"], cfg["k_ixg_hours"])
    g60 = pt["al"] * s60 * pt["recent_sh"] + (1 - pt["al"]) * xg60 * pt["fin"]
    lg, ls = d.lam_g * g60 / pt["g60_bt"], d.lam_s * s60 / pt["s60_bt"]
    if FORM:                                 # live recent-form factor (last 10 games vs his level), so the test matches the site
        h10 = d.r10_toi / 60
        fg = (d.r10_g + FORM["g"]) / (g60 * h10 + FORM["g"])
        fs = (d.r10_s + FORM["s"]) / (s60 * h10 + FORM["s"])
        lg, ls = lg * fg, ls * fs
    return lg, ls


def probs(d, lg, ls):
    return {"Goal": 1 - np.exp(-lg), "2+ goals": 1 - np.exp(-lg) * (1 + lg), "Point": 1 - np.exp(-(lg + d.lam_a)), "SOG 2+": p_over(ls, 1.5), "SOG 3+": p_over(ls, 2.5)}


def ys(d):
    return {"Goal": d.g >= 1, "2+ goals": d.g >= 2, "Point": d.g + d.a >= 1, "SOG 2+": d.s >= 2, "SOG 3+": d.s >= 3}


def brier(p, y):
    return float(((np.asarray(p, float) - np.asarray(y, float)) ** 2).mean())


def score(d, lg, ls):
    P, Y = probs(d, lg, ls), ys(d)
    return {m: summarize(list(zip(np.clip(np.asarray(P[m], float), 1e-4, 1 - 1e-4), np.asarray(Y[m], float)))) for m in P}


def calib(d, lg, ls, mask, label):
    if mask.sum() < 150:
        return None
    P, Y = probs(d, lg, ls), ys(d)
    row = {"group": label, "n": int(mask.sum()), "shots_pg": [float(ls[mask].mean()), float(d.s[mask].mean())]}
    for m in ("Goal", "Point", "SOG 2+"):
        p, y = np.asarray(P[m], float)[mask], np.asarray(Y[m], float)[mask]
        se = np.sqrt((y.var() + 1e-9) / len(y))
        row[m] = {"pred": float(p.mean()), "act": float(y.mean()), "t": float((y.mean() - p.mean()) / se)}
    return row


def groups(d, pt):
    zs, zx = pt["zs"], pt["zx"]
    base = d.c_gp >= 8
    return [(base & (zs >= 2), "shots up (z>=2)"), (base & (zs <= -2), "shots down (z<=-2)"), (base & (np.abs(zs) < 1), "shots steady"),
            (base & (zx >= 2), "xG up (z>=2)"), (base & (zx <= -2), "xG down (z<=-2)"),
            (base & (zs >= 2) & (d.posg == "D"), "D, shots up"), (base & (zx >= 2) & (d.posg == "D"), "D, xG up"),
            (base & (zs >= 2) & (zx >= 2), "shots and xG both up")]


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, log)
    d = d[(d.c_gp >= 5) & (d.lam_g > 0) & (d.lam_s > 0)].reset_index(drop=True)
    pt = parts(d, cfg)
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    fm = d.season.isin(half).values
    Y = ys(d)

    def fit_err(lg, ls, m):
        P = probs(d, lg, ls)
        sog = (brier(P["SOG 2+"][m], Y["SOG 2+"][m]) + brier(P["SOG 3+"][m], Y["SOG 3+"][m])) / 2
        return sog, (brier(P["Goal"][m], Y["Goal"][m]) + brier(P["Point"][m], Y["Point"][m])) / 2

    grid = []
    for mode, z in [("global", None)] + [("cond", z) for z in ZS]:
        for ms, mx in itertools.product(MS, MS):
            lg, ls = lams(d, pt, cfg, ms, mx, z)
            e_s, e_g = fit_err(lg, ls, fm)
            grid.append({"mode": mode, "z": z, "ms": ms, "mx": mx, "sog": e_s, "goal": e_g})
    now = next(r for r in grid if r["mode"] == "global" and r["ms"] == 1 and r["mx"] == 1)
    best_s = min(grid, key=lambda r: r["sog"])
    best_g = min(grid, key=lambda r: r["goal"])
    tm = ~fm
    dt = d[tm].reset_index(drop=True)
    ptt = {k: (v[tm] if isinstance(v, np.ndarray) else (v[tm].reset_index(drop=True) if hasattr(v, "reset_index") else v)) for k, v in pt.items()}

    def lam_t(r):
        return lams(dt, ptt, cfg, r["ms"], r["mx"], r["z"])
    lg0, ls0 = lam_t(now)
    lgS, lsS = lam_t(best_s)
    lgG, lsG = lam_t(best_g)
    # combined: shots part from the best shots setting, goals from the best goal setting
    rep = {"seasons": seasons, "fit_seasons": half, "rows": int(len(d)), "rows_test": int(len(dt)),
           "now": now, "best_sog": best_s, "best_goal": best_g,
           "grid_top_sog": sorted(grid, key=lambda r: r["sog"])[:8], "grid_top_goal": sorted(grid, key=lambda r: r["goal"])[:8],
           "test_now": score(dt, lg0, ls0), "test_bestS": score(dt, lgS, lsS), "test_bestG": score(dt, lgG, lsG),
           "groups_now": [x for x in (calib(dt, lg0, ls0, m, lab) for m, lab in groups(dt, ptt)) if x],
           "groups_bestS": [x for x in (calib(dt, lgS, lsS, m, lab) for m, lab in groups(dt, ptt)) if x],
           "groups_bestG": [x for x in (calib(dt, lgG, lsG, m, lab) for m, lab in groups(dt, ptt)) if x]}
    # persistence: for 'up' players, how much of the gap between this season's rate and the model's number held up
    per = []
    for m, lab in groups(dt, ptt)[:5]:
        q = dt[m]
        if len(q) < 150:
            continue
        h = q.c_toi / 60
        cur_s = (q.c_s / h) * q.toi_proj / 60
        cur_x = (q.c_xg / h) * q.toi_proj / 60
        mod_s, mod_g = ls0[m], lg0[m]
        per.append({"group": lab, "n": int(len(q)), "shots": {"season_rate": float(cur_s.mean()), "model": float(mod_s.mean()), "actual": float(q.s.mean()),
                    "kept": float((q.s.mean() - mod_s.mean()) / (cur_s.mean() - mod_s.mean() + 1e-9))},
                    "goals": {"season_xg_rate": float(cur_x.mean()), "model": float(mod_g.mean()), "actual": float(q.g.mean()),
                              "kept": float((q.g.mean() - mod_g.mean()) / (cur_x.mean() - mod_g.mean() + 1e-9))}})
    rep["persistence"] = per
    # 2+ goals: does a plain Poisson give the tail right? by expected goals tonight
    tail = []
    for lo, hi in ((0, .06), (.06, .1), (.1, .15), (.15, .2), (.2, .3), (.3, .45), (.45, 9)):
        m = (lg0 >= lo) & (lg0 < hi)
        if m.sum() < 300:
            continue
        lam = lg0[m]
        tail.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": int(m.sum()), "lam": float(lam.mean()), "g1_pred": float((1 - np.exp(-lam)).mean()),
                     "g1_act": float((dt.g[m] >= 1).mean()), "g2_pred": float((1 - np.exp(-lam) * (1 + lam)).mean()),
                     "g2_act": float((dt.g[m] >= 2).mean()), "g2_n": int((dt.g[m] >= 2).sum())})
    rep["tail"] = tail
    (out_dir / "usage.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "usage.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    def nm(x):
        return f"{x['mode']}{'' if x['z'] is None else ' z>=' + str(x['z'])} · shots ×{x['ms']} · xG ×{x['mx']}"
    L = [f"# Usage change: fit on {r['fit_seasons']}, tested on the rest of {r['seasons']} ({r['rows_test']:,} test player-games, 5+ GP)", "",
         f"Now (live weights): {nm(r['now'])} · SOG Brier {r['now']['sog']:.6f} · goal/point Brier {r['now']['goal']:.6f}",
         f"Best for SOG: {nm(r['best_sog'])} · {r['best_sog']['sog']:.6f}",
         f"Best for goals/points: {nm(r['best_goal'])} · {r['best_goal']['goal']:.6f}", "",
         "Top SOG settings: " + " | ".join(f"{nm(x)} {x['sog']:.6f}" for x in r["grid_top_sog"][:5]),
         "Top goal settings: " + " | ".join(f"{nm(x)} {x['goal']:.6f}" for x in r["grid_top_goal"][:5]), "",
         "## Test seasons: accuracy", "| Market | Now Brier (skill) | Best-SOG setting | Best-goal setting |", "|---|---|---|---|"]
    for m in r["test_now"]:
        a, b, c = r["test_now"][m], r["test_bestS"][m], r["test_bestG"][m]
        L.append(f"| {m} | {a['brier']:.5f} ({a['skill'] * 100:+.2f}%) | {b['brier']:.5f} ({b['skill'] * 100:+.2f}%) | {c['brier']:.5f} ({c['skill'] * 100:+.2f}%) |")

    def tb(rows):
        o = ["| Group | n | Shots pred / act | Goal pred | act | t | Point pred | act | t | SOG2+ pred | act | t |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in rows:
            o.append(f"| {x['group']} | {x['n']:,} | {x['shots_pg'][0]:.2f} / {x['shots_pg'][1]:.2f} | " +
                     " | ".join(f"{x[m]['pred']:.1%} | {x[m]['act']:.1%} | {x[m]['t']:+.1f}" for m in ("Goal", "Point", "SOG 2+")) + " |")
        return o
    L += ["", "## Players whose shooting broke from their history (test seasons): model now"] + tb(r["groups_now"])
    L += ["", "## Same groups, best-SOG setting"] + tb(r["groups_bestS"]) + ["", "## Same groups, best-goal setting"] + tb(r["groups_bestG"])
    L += ["", "## How much of the jump held up (0 = the model was right to ignore it, 1 = this season's rate was right)",
          "| Group | n | Shots: season rate / model / actual / kept | Goals: season xG rate / model / actual / kept |", "|---|---|---|---|"]
    for x in r["persistence"]:
        s, g = x["shots"], x["goals"]
        L.append(f"| {x['group']} | {x['n']:,} | {s['season_rate']:.2f} / {s['model']:.2f} / {s['actual']:.2f} / {s['kept']:.0%} | "
                 f"{g['season_xg_rate']:.3f} / {g['model']:.3f} / {g['actual']:.3f} / {g['kept']:.0%} |")
    L += ["", "## 2+ goals: plain Poisson vs what happened (test seasons, model now)",
          "| Expected goals tonight | n | 1+ pred | 1+ act | 2+ pred | 2+ act | 2+ count | act / pred |", "|---|---|---|---|---|---|---|---|"]
    for x in r.get("tail", []):
        L.append(f"| {x['bin']} (avg {x['lam']:.3f}) | {x['n']:,} | {x['g1_pred']:.1%} | {x['g1_act']:.1%} | {x['g2_pred']:.2%} | {x['g2_act']:.2%} | {x['g2_n']} | {x['g2_act'] / max(x['g2_pred'], 1e-9):.2f} |")
    return "\n".join(L) + "\n"
