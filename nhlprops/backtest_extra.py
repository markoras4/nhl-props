"""New player markets, point-in-time backtest (does not touch the website):
  1. multi-point / multi-goal props from the existing model (2+ / 3+ points, 2+ goals, hat trick, 2+ assists):
     is a Poisson count right, or are multi-point nights more common (over-dispersed)?
  2. penalty minutes: any penalty (PIM over 0.5) and PIM over 2.5
  3. power-play points: PP scoring rate (MoneyPuck 5-on-4) x PP ice time, updated with this season's PP points
     (from play-by-play) and the opponent's penalty rate
  4. faceoffs won (centres): draws per game x win % (shrunk) adjusted for the opponent's faceoff strength
Each is compared with the simple average bettors use.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict
from .grade import summarize
from .model import _hours, _num, _sit
from .sources import NHL

DISP = [None, 40, 20, 10, 6, 4]


def shrink(x, n, prior, k):
    return (x + k * prior) / (n + k)


def p_ge(lam, k, r=None):
    """P(X >= k) for Poisson (r None) or negative binomial with mean lam"""
    lam = np.asarray(lam, float)
    out = np.zeros_like(lam)
    for i, l in enumerate(lam):
        if l <= 0:
            continue
        if r is None:
            term, cdf = math.exp(-l), 0.0
            for j in range(k):
                if j:
                    term *= l / j
                cdf += term
        else:
            p = r / (r + l)
            term = p ** r; cdf = 0.0
            for j in range(k):
                if j:
                    term *= (j + r - 1) / j * (1 - p)
                cdf += term
        out[i] = 1 - cdf
    return out


def score(p, y):
    return summarize(list(zip(np.clip(p, 1e-4, 1 - 1e-4), np.asarray(y).astype(int))))


def ll(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4); y = np.asarray(y).astype(float)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


# ------------------------------------------------------------------ play-by-play: PP goals/assists, faceoffs, penalties
def pbp_extract(js):
    if not js or js.get("gameState") not in ("OFF", "FINAL"):
        return None
    hid, aid = (js.get("homeTeam") or {}).get("id"), (js.get("awayTeam") or {}).get("id")
    pp_pts, fo, pens = [], [], {}
    for pl in js.get("plays") or []:
        k, d = pl.get("typeDescKey"), pl.get("details") or {}
        if k == "goal":
            sc = str(pl.get("situationCode") or "")
            if len(sc) == 4 and (pl.get("periodDescriptor") or {}).get("periodType") != "SO":
                ag, ask, hsk, hg = sc
                team = d.get("eventOwnerTeamId")
                pp = ag == "1" and hg == "1" and ((team == hid and hsk > ask) or (team == aid and ask > hsk))
                if pp:
                    for key in ("scoringPlayerId", "assist1PlayerId", "assist2PlayerId"):
                        if d.get(key):
                            pp_pts.append(int(d[key]))
        elif k == "faceoff" and d.get("winningPlayerId") and d.get("losingPlayerId"):
            fo.append([int(d["winningPlayerId"]), int(d["losingPlayerId"])])
        elif k == "penalty" and (d.get("duration") or 0) in (2, 4, 5):
            t = d.get("eventOwnerTeamId")
            pens[str(t)] = pens.get(str(t), 0) + 1
    return {"pp": pp_pts, "fo": fo, "pen": pens, "hid": hid, "aid": aid}


def pbp_season(src, games, season, workers, log=print):
    f = Path("data/cache/pbp_bt") / f"{season}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    have = json.loads(f.read_text()) if f.exists() else {}
    todo = [int(i) for i in games.id if str(int(i)) not in have]
    if todo:
        log(f"  {season}: play-by-play for {len(todo)} games ...")

        def one(gid):
            raw = src._download(f"{NHL}/gamecenter/{gid}/play-by-play")
            try:
                return gid, pbp_extract(json.loads(raw)) if raw else None
            except Exception:
                return gid, None
        with ThreadPoolExecutor(workers) as ex:
            for gid, rec in ex.map(one, todo):
                if rec:
                    have[str(gid)] = rec
        f.write_text(json.dumps(have, separators=(",", ":")))
    return have


def mp_pp(src, season):
    """previous season 5-on-4 points and ice time per player (MoneyPuck)"""
    d = src.moneypuck("skaters", season - 1, False)
    if d is None:
        return {}, 0.0
    a = d[_sit(d) == "all"]; p = d[_sit(d) == "5on4"]
    gp = dict(zip(pd.to_numeric(a.playerId, errors="coerce"), _num(a, "games_played")))
    pts = _num(p, "I_F_points") if "I_F_points" in p.columns else _num(p, "I_F_goals") + _num(p, "I_F_primaryAssists") + _num(p, "I_F_secondaryAssists")
    h = _hours(p, "icetime")
    out = {}
    for pid, pt, hh in zip(pd.to_numeric(p.playerId, errors="coerce"), pts, h):
        if pid == pid:
            out[int(pid)] = (float(pt), float(hh), float(gp.get(pid, 0) or 0))
    lg = float(pts.sum() / h.sum()) if h.sum() else 4.0
    return out, lg


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    seasons = sorted(seasons)
    rows = []
    for season in seasons:
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        pri = prior_frames(src, season)
        df = predict(sk, pri, games, go, season, cfg)
        prev_games, prev_sk, _ = season_boxscores(src, season - 1, cfg["workers"], log)
        pb = pbp_season(src, games, season, cfg["workers"], log)
        pbp_prev = pbp_season(src, prev_games, season - 1, cfg["workers"], log)
        # per player-game: PP points, faceoffs taken / won
        ppg, fot, fow = {}, {}, {}
        for gid, g in pb.items():
            for pid in g["pp"]:
                ppg[(int(gid), pid)] = ppg.get((int(gid), pid), 0) + 1
            for w, l in g["fo"]:
                fot[(int(gid), w)] = fot.get((int(gid), w), 0) + 1; fow[(int(gid), w)] = fow.get((int(gid), w), 0) + 1
                fot[(int(gid), l)] = fot.get((int(gid), l), 0) + 1
        df["ppp"] = [ppg.get((int(a), int(b)), 0) for a, b in zip(df.gid, df.pid)]
        df["fo_t"] = [fot.get((int(a), int(b)), 0) for a, b in zip(df.gid, df.pid)]
        df["fo_w"] = [fow.get((int(a), int(b)), 0) for a, b in zip(df.gid, df.pid)]
        # previous-season faceoffs and penalty minutes per player (priors)
        pf_t, pf_w = {}, {}
        for g in pbp_prev.values():
            for w, l in g["fo"]:
                pf_t[w] = pf_t.get(w, 0) + 1; pf_w[w] = pf_w.get(w, 0) + 1; pf_t[l] = pf_t.get(l, 0) + 1
        pgp = prev_sk.groupby("pid").agg(gp=("gid", "count"), toi=("toi", "sum"), pim=("pim", "sum"))
        # team faceoff % and penalties committed, season to date (by game)
        team_of = {}
        for r in games.itertuples():
            team_of[int(r.id)] = (r.home, r.away)
        pmp, pp_lg = mp_pp(src, season)
        df = df.sort_values(["date", "gid"])
        tfo, tpen, tgp = {}, {}, {}
        lg_pen = [0.0, 0]
        c = {}
        out = []
        for date, day in df.groupby("date", sort=True):
            for r in day.itertuples():
                pc = c.get(r.pid, {"ppp": 0, "fot": 0, "fow": 0, "pim": 0.0, "toi": 0.0, "gp": 0})
                rec = {"season": season, "pid": r.pid, "posg": r.posg, "gp": r.c_gp, "g": r.g, "a": r.a, "lam_g": r.lam_g, "lam_a": r.lam_a,
                       "toi_proj": r.toi_proj, "pim": r.pim, "ppp": r.ppp, "fo_t": r.fo_t, "fo_w": r.fo_w}
                # PIM
                pr = pgp.loc[r.pid] if r.pid in pgp.index else None
                pos_pen = 0.55 if r.posg == "D" else 0.6           # penalties per 60, refined below by league data
                prior_pen = shrink(pr.pim / 2, pr.toi / 60, pos_pen, 8) if pr is not None and pr.toi > 0 else pos_pen
                rec["lam_pen"] = shrink(pc["pim"] / 2, pc["toi"] / 60, prior_pen, 6) * r.toi_proj / 60
                rec["naive_pen"] = (pc["gp_pen"] / pc["gp"]) if pc.get("gp") and pc.get("gp_pen") is not None else np.nan
                # PP points
                mp = pmp.get(int(r.pid))
                if mp and mp[1] > 0 and mp[2] > 0:
                    ppp60 = shrink(mp[0], mp[1], pp_lg * 0.6, 6)
                    lam_prior = ppp60 * mp[1] / mp[2]
                else:
                    lam_prior = 0.03
                opp = r.opp
                op = shrink(tpen.get(opp, 0), tgp.get(opp, 0), (lg_pen[0] / lg_pen[1]) if lg_pen[1] else 3.3, 15)
                lgp = (lg_pen[0] / lg_pen[1]) if lg_pen[1] else 3.3
                for K in (5, 10, 20, 40):
                    rec[f"lam_pp{K}"] = (pc["ppp"] + K * lam_prior) / (pc["gp"] + K)
                rec["opp_pen"] = op / lgp if lgp else 1.0
                rec["naive_pp"] = pc["ppp"] / pc["gp"] if pc["gp"] >= 5 else np.nan
                # faceoffs
                pt, pw = pf_t.get(r.pid, 0), pf_w.get(r.pid, 0)
                pgp_ = float(pr.gp) if pr is not None else 0.0
                fo_pg_prior = pt / pgp_ if pgp_ else 0.0
                fo_pg = (pc["fot"] + 10 * fo_pg_prior) / (pc["gp"] + 10) if (pc["gp"] + pgp_) else 0.0
                winp = shrink(pc["fow"] + pw, pc["fot"] + pt, 0.5, 200)
                h, a = team_of.get(int(r.gid), (None, None))
                won_t = tfo.get(opp, [0, 0])
                q = (won_t[0] + 200) / (won_t[1] + 400)          # opponent team faceoff %, shrunk to 50%
                padj = winp * (1 - q) / (winp * (1 - q) + (1 - winp) * q)
                rec["fo_n"] = fo_pg; rec["lam_fow"] = fo_pg * padj; rec["lam_fow_noopp"] = fo_pg * winp
                rec["naive_fow"] = pc["fow"] / pc["gp"] if pc["gp"] >= 5 else np.nan
                out.append(rec)
            # update after the day
            for r in day.itertuples():
                pc = c.setdefault(r.pid, {"ppp": 0, "fot": 0, "fow": 0, "pim": 0.0, "toi": 0.0, "gp": 0, "gp_pen": 0})
                pc["ppp"] += r.ppp; pc["fot"] += r.fo_t; pc["fow"] += r.fo_w; pc["pim"] += r.pim; pc["toi"] += r.toi; pc["gp"] += 1
                pc["gp_pen"] = pc.get("gp_pen", 0) + int(r.pim > 0)
            for gid in set(day.gid):
                g = pb.get(str(int(gid)))
                h, a = team_of.get(int(gid), (None, None))
                if not g or not h:
                    continue
                for team, tid in ((h, g["hid"]), (a, g["aid"])):
                    tpen[team] = tpen.get(team, 0) + g["pen"].get(str(tid), 0); tgp[team] = tgp.get(team, 0) + 1
                    lg_pen[0] += g["pen"].get(str(tid), 0); lg_pen[1] += 1
                # team faceoffs won / taken
                for team in (h, a):
                    tfo.setdefault(team, [0, 0])
                ids_h = set(day[(day.gid == gid) & (day.team == h)].pid); ids_a = set(day[(day.gid == gid) & (day.team == a)].pid)
                for w, l in g["fo"]:
                    if w in ids_h: tfo[h][0] += 1; tfo[h][1] += 1; tfo[a][1] += 1
                    elif w in ids_a: tfo[a][0] += 1; tfo[a][1] += 1; tfo[h][1] += 1
        rows += out
    d = pd.DataFrame(rows)
    d = d[d.gp >= 5].copy()
    rep = {"seasons": seasons, "rows": int(len(d))}

    # 1. multi-point props
    lp = d.lam_g + d.lam_a
    multi = {"2+ points": (lp, 2, d.g + d.a), "3+ points": (lp, 3, d.g + d.a), "2+ goals": (d.lam_g, 2, d.g),
             "hat trick": (d.lam_g, 3, d.g), "2+ assists": (d.lam_a, 2, d.a)}
    rep["multi"] = {}
    for name, (lam, k, cnt) in multi.items():
        y = (cnt >= k).values
        res = {str(r): ll(p_ge(lam.values, k, r), y) for r in DISP}
        best = min(res, key=res.get)
        rb = None if best == "None" else float(best)
        rep["multi"][name] = {"logloss": res, "best_r": best, "poisson": score(p_ge(lam.values, k, None), y), "best": score(p_ge(lam.values, k, rb), y)}
    # 2. PIM
    y1, y3 = (d.pim > 0.5).values, (d.pim > 2.5).values
    res = {str(r): ll(p_ge(d.lam_pen.values, 1, r), y1) for r in DISP}
    best = min(res, key=res.get); rb = None if best == "None" else float(best)
    # calibration multiplier for the penalty rate
    mult = {}
    for m_ in (0.8, 0.9, 1.0, 1.1, 1.2, 1.3):
        mult[str(m_)] = ll(p_ge(d.lam_pen.values * m_, 1, rb), y1)
    mb = float(min(mult, key=mult.get))
    ok = d.naive_pen.notna()
    rep["pim"] = {"best_r": best, "logloss": res, "mult": mult, "best_mult": mb,
                  "pim_0.5": score(p_ge(d.lam_pen.values * mb, 1, rb), y1), "pim_2.5": score(p_ge(d.lam_pen.values * mb, 2, rb), y3),
                  "naive_0.5": score(d.naive_pen[ok].clip(0.01, 0.99).values, y1[ok]), "model_same_rows": score(p_ge(d.lam_pen[ok].values * mb, 1, rb), y1[ok])}
    # 3. PP points
    ypp = (d.ppp >= 1).values
    pres = {}
    for K in (5, 10, 20, 40):
        for beta in (0.0, 0.5, 1.0):
            lam = d[f"lam_pp{K}"].values * d.opp_pen.values ** beta
            pres[f"K{K}_b{beta}"] = ll(1 - np.exp(-lam), ypp)
    pb_ = min(pres, key=pres.get)
    K = int(pb_.split("_")[0][1:]); beta = float(pb_.split("_b")[1])
    lam = d[f"lam_pp{K}"].values * d.opp_pen.values ** beta
    ok = d.naive_pp.notna()
    rep["pp"] = {"grid": pres, "best": pb_, "model": score(1 - np.exp(-lam), ypp),
                 "naive": score(d.naive_pp[ok].clip(0.005, 0.95).values, ypp[ok]), "model_same_rows": score(1 - np.exp(-lam[ok.values]), ypp[ok])}
    # 4. faceoffs won (players expected to take 5+ draws)
    f = d[d.fo_n >= 5]
    yw = f.fo_w.values
    lines = [4.5, 6.5, 8.5, 10.5]
    fres = {}
    for nm, col in (("opp", "lam_fow"), ("noopp", "lam_fow_noopp")):
        for r in DISP:
            fres[f"{nm}_{r}"] = float(np.mean([ll(p_ge(f[col].values, int(L) + 1, None if r is None else float(r)), yw > L) for L in lines]))
    fb = min(fres, key=fres.get); col = "lam_fow" if fb.startswith("opp") else "lam_fow_noopp"
    rr = fb.split("_")[1]; rr = None if rr == "None" else float(rr)
    okf = f.naive_fow.notna()
    rep["fo"] = {"rows": int(len(f)), "grid": fres, "best": fb, "mean_pred": float(f[col].mean()), "mean_actual": float(yw.mean()),
                 "lines": {str(L): {"model": score(p_ge(f[col].values, int(L) + 1, rr), yw > L),
                                    "naive": score(p_ge(f.naive_fow[okf].values, int(L) + 1, rr), (yw > L)[okf.values]),
                                    "model_same_rows": score(p_ge(f[col][okf].values, int(L) + 1, rr), (yw > L)[okf.values])} for L in lines}}
    (out_dir / "extra.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "extra.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    def s(x):
        return f"pred {x['mean_pred']:.1%} · happened {x['hit_rate']:.1%} · Brier {x['brier']:.4f} · skill {x['skill'] * 100:+.1f}%"
    L = [f"# New markets backtest: {', '.join(f'{y}-{y + 1}' for y in r['seasons'])}", f"Player-games: {r['rows']:,}", "", "## Multi-point / multi-goal"]
    for k, v in r["multi"].items():
        L.append(f"- **{k}** · Poisson: {s(v['poisson'])} · best spread {v['best_r']}: {s(v['best'])}")
    p = r["pim"]
    L += ["", "## Penalty minutes", f"best spread {p['best_r']} · rate multiplier {p['best_mult']}",
          f"- PIM over 0.5: {s(p['pim_0.5'])}", f"- PIM over 2.5: {s(p['pim_2.5'])}",
          f"- simple average (share of games with a penalty): {s(p['naive_0.5'])} · model same rows: {s(p['model_same_rows'])}"]
    q = r["pp"]
    L += ["", "## Power-play points", f"best {q['best']} (K = games of prior weight, b = opponent penalty effect)",
          f"- model: {s(q['model'])}", f"- simple average: {s(q['naive'])} · model same rows: {s(q['model_same_rows'])}",
          "- grid: " + " · ".join(f"{k}: {v:.5f}" for k, v in q["grid"].items())]
    f = r["fo"]
    L += ["", f"## Faceoffs won (centres, {f['rows']:,} games)", f"best {f['best']} · mean predicted {f['mean_pred']:.2f} · actual {f['mean_actual']:.2f}"]
    for line, v in f["lines"].items():
        L.append(f"- over {line}: model {s(v['model'])} | simple avg {s(v['naive'])} | model same rows {s(v['model_same_rows'])}")
    return "\n".join(L) + "\n"
