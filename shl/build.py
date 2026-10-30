"""Build the private SHL page: data in the NHL Props Lab format, rendered with the same template (SHL mode)."""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from . import xg as xgm
from .collect import ROOT, rgz
from .data import load_all
from .model import CFG, build_state, project

OUT = Path("output/shl")
TEMPLATE = Path(__file__).resolve().parent.parent / "nhlprops" / "template.html"
DAYS_AHEAD = 7
TZ = "Europe/Stockholm"


def gid_of(uuid: str) -> int:
    return int(hashlib.md5(uuid.encode()).hexdigest()[:8], 16)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


TEAM_STOP = {"if", "hc", "bk", "hk", "ik", "hf", "aik", "hockey", "the"}


def team_key(s: str) -> str:
    return "".join(w for w in norm(s).split() if w not in TEAM_STOP)


def poisson_cdf(k, lam):
    return sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(0, int(k) + 1)) if lam > 0 else 1.0


# ------------------------------------------------------------------ player profiles
def profiles(P, S, GL, names, st, cur, asof):
    ppp = {}
    for r in GL[GL.status.astype(str).str.startswith("PP")].itertuples():
        for pid in (r.scorer, r.a1, r.a2):
            if pid is not None and not (isinstance(pid, float) and math.isnan(pid)):
                ppp[(r.uuid, int(pid))] = ppp.get((r.uuid, int(pid)), 0) + 1
    P = P.copy()
    P["ppp"] = [ppp.get((u, i), 0) for u, i in zip(P.uuid, P.pid)]
    ix = S[xgm.model_rows(S) | S.pen_shot].groupby(["uuid", "pid"]).xg.sum()
    P["xg"] = [round(float(ix.get((u, i), 0.0)), 3) for u, i in zip(P.uuid, P.pid)]
    att = S.groupby(["uuid", "pid", "kind"]).size().unstack(fill_value=0)
    P = P.merge(att, left_on=["uuid", "pid"], right_index=True, how="left").fillna({"on": 0, "miss": 0, "block": 0, "goal": 0})
    P = P.sort_values("date", ascending=False)
    lg_sh = float(P[P.season >= cur - 1].g.sum() / max(1, P[P.season >= cur - 1].s.sum()))
    out = {}
    for pid, q in P.groupby("pid"):
        if pid not in st["players"]:
            continue
        sp = st["players"][pid]
        lc = q[q.season == cur]
        lp = q[q.season == cur - 1]
        rows = lambda df: [{"date": r.date, "opp": r.opp, "home": bool(r.home), "g": int(r.g), "a": int(r.a), "p": int(r.g + r.a), "s": int(r.s),
                            "toi": float(r.toi), "ppp": int(r.ppp), "pim": int(r.pim), "hit": int(r.hit), "blk": int(r.blk), "xg": float(r.xg)} for r in df.itertuples()]
        LC, LP = rows(lc), rows(lp)
        allg = LC + LP
        l5 = LC[:5]
        form = {"gp": len(l5), "g": sum(x["g"] for x in l5), "a": sum(x["a"] for x in l5), "p": sum(x["p"] for x in l5), "s": sum(x["s"] for x in l5),
                "toi": float(np.mean([x["toi"] for x in l5])) if l5 else None,
                "games": [{"d": x["date"], "o": x["opp"], "h": x["home"], "g": x["g"], "a": x["a"], "s": x["s"], "t": round(x["toi"], 1),
                           "pp": x["ppp"], "pm": x["pim"], "x": round(x["xg"], 2)} for x in LC[:10]],
                "gl": [[x["date"], x["opp"], 1 if x["home"] else 0, x["g"], x["a"], x["s"], round(x["toi"], 1), x["ppp"], x["pim"], x["hit"], x["blk"], round(x["xg"], 2)]
                       for x in allg[:40]]}
        fns = {"g": lambda x: x["g"] >= 1, "a": lambda x: x["a"] >= 1, "pt": lambda x: x["p"] >= 1,
               **{f"s{L}": (lambda L: lambda x: x["s"] > L)(L) for L in (1.5, 2.5, 3.5, 4.5)}}
        wins = [allg[:5], allg[:10], allg[:20], LC]
        hr = {"n": [len(w) for w in wins], **{k: [sum(1 for x in w if fn(x)) for w in wins] for k, fn in fns.items()}}
        w5, w10 = allg[:5], allg[:10]
        hr["hbn"] = [len(w5), len(w10)]
        for L in (0.5, 1.5, 2.5, 3.5):
            hr[f"h{L}"] = [sum(1 for x in w if x["hit"] > L) for w in (w5, w10)]
        for L in (0.5, 1.5, 2.5):
            hr[f"b{L}"] = [sum(1 for x in w if x["blk"] > L) for w in (w5, w10)]
        base_g = allg[5:25]
        role = None
        if len(l5) >= 4 and len(base_g) >= 8:
            t5, tb = float(np.mean([x["toi"] for x in l5])), float(np.mean([x["toi"] for x in base_g]))
            role = {"toi5": round(t5, 1), "toi_base": round(tb, 1), "toi_delta": round(t5 - tb, 1), "ppp5": sum(x["ppp"] for x in l5)}
        lam_pt = (sp["g60"] + sp["a60"]) * sp["toi_proj"] / 60
        p_pt_game = 1 - math.exp(-lam_pt) if lam_pt > 0 else 0.0
        pt_dry, pt_found = 0, False
        for x in allg:
            if x["p"] > 0:
                pt_found = True; break
            pt_dry += 1
        ps = 0
        for x in LC:
            if x["p"] == 0: break
            ps += 1
        gs = 0
        for x in LC:
            if x["g"] == 0: break
            gs += 1
        streaks = {"pd": pt_dry, "pp": (1 - p_pt_game) ** pt_dry if pt_found else None, "ppg": round(p_pt_game, 3), "ps": ps, "gst": gs,
                   "ps_pts": sum(x["p"] for x in LC[:ps]), "gst_g": sum(x["g"] for x in LC[:gs])}
        shots_since, games_since, found = 0, 0, False
        for x in allg:
            if x["g"] > 0:
                found = True; break
            shots_since += x["s"]; games_since += 1
        c_g, c_s = float(q.g.sum()), float(q.s.sum())
        career_sh = (c_g + 60 * lg_sh) / (c_s + 60)
        recent = q.head(40)
        recent_sh = (float(recent.g.sum()) + 40 * career_sh) / (float(recent.s.sum()) + 40)
        # regression check: this season's goals vs his chances (xG) at his prior finishing level
        prior = q[q.season < cur]
        prior_fin = (float(prior.g.sum()) + 15) / (float(prior.xg.sum()) + 15) if len(prior) else 1.0
        cur_g, cur_s, cur_xg = float(lc.g.sum()), float(lc.s.sum()), float(lc.xg.sum())
        exp_g = cur_xg * prior_fin if cur_xg else cur_s * career_sh
        z = (cur_g - exp_g) / math.sqrt(exp_g) if exp_g > 0.3 else 0.0
        p_low = poisson_cdf(int(cur_g), exp_g)
        p_high = 1 - poisson_cdf(int(cur_g) - 1, exp_g) if cur_g >= 1 else 1.0
        flag = None
        if cur_s >= 25:
            flag = "due" if p_low < 0.1 else ("hot" if p_high < 0.1 else None)
        seasons = {}
        for y, qq in q.groupby("season"):
            seasons[int(y)] = {"gp": int(len(qq)), "g": int(qq.g.sum()), "a": int(qq.a.sum()), "p": int(qq.g.sum() + qq.a.sum()), "s": int(qq.s.sum()),
                               "xg": round(float(qq.xg.sum()), 2), "xg_gp": int(len(qq))}

        def window(yrs):
            ss = [seasons[y] for y in seasons if yrs is None or y in yrs]
            if not ss:
                return None
            gp = sum(x["gp"] for x in ss)
            t = {k: sum(x[k] for x in ss) for k in ("gp", "g", "a", "p", "s")}
            n = len(ss)
            return {**t, "seasons": n, **{f"{k}_pg": t[k] / gp if gp else 0 for k in ("g", "a", "p", "s")},
                    **{f"{k}_ps": t[k] / n for k in ("g", "a", "p", "s")}, "sh": t["g"] / t["s"] if t["s"] else None}

        def xgw(yrs):
            ss = [seasons[y] for y in seasons if y in yrs]
            if not ss:
                return None
            return {"xg": sum(x["xg"] for x in ss), "g": sum(x["g"] for x in ss), "s": sum(x["s"] for x in ss), "gp": sum(x["gp"] for x in ss)}

        def shp(df):
            return {"gp": float(len(df)), "att": float((df.on + df.miss + df.block + df.goal).sum()), "s": float(df.s.sum()),
                    "miss": float(df.miss.sum()), "blkd": float(df.block.sum()), "xg": round(float(df.xg.sum()), 2), "h": round(float(df.toi.sum()) / 60, 2)}
        shpd = {str(y): shp(q[q.season == y]) for y in (cur, cur - 1) if (q.season == y).any()}
        if (q.season < cur).any():
            shpd["avg"] = {**shp(q[q.season < cur]), "n": int(q[q.season < cur].season.nunique())}
        fo_t = int(q[q.season == cur].fow.sum() + q[q.season == cur].fol.sum())
        fo_w = int(q[q.season == cur].fow.sum())
        nm = names.get(int(pid)) or str(pid)
        out[int(pid)] = {
            "id": int(pid), "name": nm, "team": sp["team"], "pos": {"CE": "C", "LW": "L", "RW": "R", "LD": "D", "RD": "D"}.get(sp["pos"], sp["pos"]),
            "posg": sp["posg"], "num": int(sp["num"]) if sp.get("num") is not None and not pd.isna(sp["num"]) else None, "age": None,
            "active": True, "toi_proj": sp["toi_proj"], "toi_recent": sp["toi_recent"], "toi_season": sp["toi_season"],
            "rates": {"s60": sp["s60"], "xg60": sp["ixg60"], "a60": sp["a60"], "g60": sp["g60"], "career_sh": career_sh, "hit60": sp["hit60"], "blk60": sp["blk60"],
                      "fm_g": 1.0, "fm_a": 1.0, "fm_s": 1.0, "recent_sh": recent_sh, "finish": sp["fin"], "lg_sh": lg_sh},
            "form": form, "hr": hr, "role": role, "streaks": streaks, "career": {"goals": int(c_g), "assists": int(q.a.sum()), "points": int(c_g + q.a.sum()), "gamesPlayed": int(len(q))},
            "shp": shpd,
            "drought": {"shots": shots_since, "games": games_since, "found": found, "shots_per_goal": 1 / recent_sh if recent_sh else None, "p": (1 - recent_sh) ** shots_since},
            "regress": {"g": cur_g, "exp": exp_g, "z": z, "flag": flag, "p_low": p_low, "p_high": p_high, "s": cur_s, "sh": cur_g / cur_s if cur_s else None, "xg": cur_xg},
            "windows": {"cur": window([cur]), "prev3": window([cur - 1, cur - 2, cur - 3]), "prev5": window([cur - i for i in range(1, 6)]), "career": window(None)},
            "xg_windows": {"cur": xgw([cur]), "prev3": xgw([cur - 1, cur - 2, cur - 3]), "prev5": xgw([cur - i for i in range(1, 6)])},
            "by_season": [{"y": y, **seasons[y]} for y in sorted(seasons, reverse=True)],
            "x": {"ppp_lam": None, "pp": None, "fo": {"pg": fo_t / max(1, len(lc)), "win": fo_w / fo_t if fo_t else None, "taken": fo_t, "won": fo_w,
                                                    "pct_cur": fo_w / fo_t if fo_t else None, "pct_prev": None},
                  "pen60": None, "pim": {"cur": int(lc.pim.sum()), "gp": int(len(lc)), "games_with": int((lc.pim > 0).sum())}},
            "hb_games": [{"d": x["date"], "o": x["opp"], "h": x["home"], "hit": x["hit"], "blk": x["blk"]} for x in allg[:10]],
            "mates": None, "injury": None, "why": None,
        }
    return out


# ------------------------------------------------------------------ odds (Unibet / Kambi)
PLAYER_MARKETS = {"to score - including overtime": "g", "to score at least 2 goals - including overtime": "g2",
                  "to score at least 3 goals - including overtime": "g3", "player to score at least 1 points - including overtime": "pt",
                  "player to score at least 2 points - including overtime": "pt2", "player to score at least 3 points - including overtime": "pt3",
                  "player to record at least 1 assists - including overtime": "a", "first goal scorer - including overtime": "fg"}


def parse_odds(games, players):
    p = ROOT / "odds" / "latest.json.gz"
    if not p.exists():
        return {}
    js = rgz(p)
    by_key = {(team_key(g["home_name"]), team_key(g["away_name"])): g for g in games}
    roster = {}
    for pid, pl in players.items():
        roster.setdefault(pl["team"], {})[norm(pl["name"])] = pid
    out = {}
    for ev in js.get("events", []):
        e = ev["event"]
        k = (team_key(e.get("homeName")), team_key(e.get("awayName")))
        g = by_key.get(k) or next((gg for kk, gg in by_key.items() if (k[0] in kk[0] or kk[0] in k[0]) and (k[1] in kk[1] or kk[1] in k[1])), None)
        if not g:
            continue
        rec = {"book": "Unibet", "players": {}, "totals": {}, "winner": {}, "fetched": js.get("fetched")}
        names = {**roster.get(g["home"], {}), **roster.get(g["away"], {})}
        for o in ev["offers"]:
            lab = (o.get("criterion") or {}).get("label", "")
            m = PLAYER_MARKETS.get(lab.lower())
            if m:
                for oc in o.get("outcomes", []):
                    nm = norm(oc.get("participant"))
                    pid = names.get(nm)
                    if pid is None:
                        # "Last, First" or small spelling differences: match on last name + first initial
                        parts = nm.split()
                        cand = [v for kk, v in names.items() if parts and kk.split()[-1:] == parts[-1:] and kk[:1] == nm[:1]]
                        pid = cand[0] if len(cand) == 1 else None
                    if pid is None or not oc.get("odds"):
                        continue
                    od = round(oc["odds"] / 1000, 2)
                    slot = rec["players"].setdefault(str(pid), {}).setdefault(m, {})
                    if oc.get("label") == "No":
                        slot["n"] = od
                    else:
                        slot["o"] = od
            elif lab.startswith("Moneyline - Including Overtime"):
                for oc in o.get("outcomes", []):
                    side = "home" if oc.get("type") == "OT_ONE" or oc.get("label") == "1" else ("away" if oc.get("type") == "OT_TWO" or oc.get("label") == "2" else None)
                    if side and oc.get("odds"):
                        rec["winner"][side] = round(oc["odds"] / 1000, 2)
            elif lab.startswith("Total Goals - Including Overtime"):
                for oc in o.get("outcomes", []):
                    if oc.get("line") and oc.get("odds"):
                        L = str(oc["line"] / 1000)
                        rec["totals"].setdefault(L, {})["over" if oc.get("label") == "Over" else "under"] = round(oc["odds"] / 1000, 2)
        rec["players"] = {k: {m: v for m, v in d.items() if v.get("o")} for k, d in rec["players"].items()}
        out[str(g["id"])] = rec
    return out


# ------------------------------------------------------------------ build
def main(log=print):
    sch, P, G, S, GL, names = load_all(log)
    now = pd.Timestamp.now(tz=TZ)
    today = now.strftime("%Y-%m-%d")
    w, xrep = xgm.train(S)                                  # all completed seasons
    S = S.copy(); S["xg"] = xgm.predict(S, w)
    st = build_state(P, G, S, sch, today, CFG, w)
    cur = st["season"]
    up = sch[(sch.state != "post-game") & (sch.start >= now - pd.Timedelta(hours=3)) & (sch.start <= now + pd.Timedelta(days=DAYS_AHEAD))].sort_values("start")
    prof = profiles(P, S, GL, names, st, cur, today)
    # team context
    last_game = {}
    pc = P[P.season == cur].sort_values("date")
    for team, q in pc.groupby("team"):
        lastu = q.uuid.iloc[-1]
        last_game[team] = q[q.uuid == lastu]
    gk_cur = G[(G.season == cur)].sort_values("date")
    teams_out = {}
    res = sch[(sch.season == cur) & (sch.state == "post-game")].sort_values("start")
    for team in sorted(set(sch[sch.season == cur].home) | set(sch[sch.season == cur].away)):
        t = st["teams"].get(team, {"xgf": 0, "xga": 0, "att": 1, "def": 1, "gp": 0, "gf": 0, "ga": 0, "s_xgf": 0, "s_xga": 0})
        tr = res[(res.home == team) | (res.away == team)]
        recs = []
        for r in tr.to_dict("records"):
            h = r["home"] == team
            gf, ga = (r["hs"], r["as"]) if h else (r["as"], r["hs"])
            recs.append({"d": r["date"], "o": r["away"] if h else r["home"], "h": h, "gf": int(gf), "ga": int(ga), "x": "SO" if r["so"] else ("OT" if r["ot"] else None)})
        w_ = sum(1 for x in recs if x["gf"] > x["ga"]); otl = sum(1 for x in recs if x["gf"] < x["ga"] and x["x"]); l_ = len(recs) - w_ - otl
        lu = last_game.get(team)
        groups = {}
        if lu is not None:
            for r in lu.itertuples():
                if r.posg in ("F", "D") and r.line:
                    groups.setdefault(f"{'f' if r.posg == 'F' else 'd'}{int(r.line)}", []).append(int(r.pid))
        rw = sum(1 for x in recs if x["gf"] > x["ga"] and not x["x"]); xw = w_ - rw
        reg_tot = lambda x: x["gf"] + x["ga"] - (1 if x["x"] else 0)
        hm = [x for x in recs if x["h"]]; aw = [x for x in recs if not x["h"]]
        avg = lambda xs, k: sum(x[k] for x in xs) / len(xs) if xs else None
        share = lambda xs, L: sum(1 for x in xs if reg_tot(x) > L) / len(xs) if xs else None
        old3 = sch[(sch.season < cur) & (sch.season >= cur - 3) & (sch.state == "post-game") & ((sch.home == team) | (sch.away == team))]
        o3 = [{"gf": int(r["hs"]), "ga": int(r["as"]), "x": r["ot"] or r["so"]} for r in old3.to_dict("records")] + recs
        extra = {"l5_gf": avg(recs[-5:], "gf"), "l5_ga": avg(recs[-5:], "ga"),
                 "gf_home": avg(hm, "gf"), "ga_home": avg(hm, "ga"), "gf_away": avg(aw, "gf"), "ga_away": avg(aw, "ga"),
                 "over_5.5_reg": share(recs, 5.5), "over_6.5_reg": share(recs, 6.5), "over_5.5_home": share(hm, 5.5), "over_5.5_away": share(aw, 5.5),
                 "over_5.5_3y": share(o3, 5.5)}
        teams_out[team] = {**extra, "abbr": team, "xgf5": t["xgf"], "xga5": t["xga"], "xga_all": t["xga"], "sa_all": None, "gp": t["gp"],
                           "gf_pg": t["gf"] / t["gp"] if t["gp"] else None, "ga_pg": t["ga"] / t["gp"] if t["gp"] else None,
                           "s_gf": t["gf"], "s_ga": t["ga"], "s_xgf": t["s_xgf"], "s_xga": t["s_xga"],
                           "w": w_, "l": l_, "otl": otl, "pts_pct": (rw * 3 + xw * 2 + otl) / (3 * len(recs)) if recs else None,
                           "recent6": list(reversed(recs[-6:])), "last5": "".join("W" if x["gf"] > x["ga"] else "L" for x in recs[-5:]),
                           "injuries": [], "lineup": {"source": "recent", "groups": groups}, "name": None}
    # games
    games_out, players_out, goalies_out = [], {}, {}
    for gm in up.itertuples():
        gid = gid_of(gm.uuid)
        lus, starters = {}, {}
        for team in (gm.home, gm.away):
            lu = last_game.get(team)
            lus[team] = [int(x) for x in lu.pid] if lu is not None else []
            # projected starter: most starts in the team's last 5 games
            tg = gk_cur[gk_cur.team == team]
            last5 = tg[tg.uuid.isin(tg.uuid.drop_duplicates().tail(5))]
            st_ = last5[last5.started].pid.value_counts()
            starters[team] = int(st_.index[0]) if len(st_) else None
        pr = project(st, gm.home, gm.away, lus[gm.home], lus[gm.away], starters[gm.home], starters[gm.away], CFG)
        gr_ = lambda gk: st["goalies"].get(gk, {}).get("ratio", 1.0) if gk else 1.0
        d = pr["default"]
        props = []
        for x in pr["props"]:
            if x["id"] not in prof:
                continue
            props.append({"id": x["id"], "team": x["team"], "opp": x["opp"], "home": x["home"], "base": {k: float(v) for k, v in x["base"].items()},
                          "hv": {"n": 0}, "factors": x["factors"], "p": {k: float(v) for k, v in x["p"].items()}})
            players_out[str(x["id"])] = prof[x["id"]]
        team_gk = {}
        for team, gk0 in starters.items():
            pool = sorted([k for k, v in st["goalies"].items() if v["team"] == team and v["gp_cur"] > 0], key=lambda k: -st["goalies"][k]["gp_cur"])
            if gk0 and gk0 not in pool:
                pool.insert(0, gk0)
            team_gk[team] = pool
            for gk in pool:
                if gk and gk in st["goalies"]:
                    gg = st["goalies"][gk]
                    goalies_out[str(gk)] = {"id": gk, "team": team, "pos": "G", "name": names.get(gk, str(gk)), "num": None, "ratio": gg["ratio"], "raw_ratio": gg["ratio"],
                                            "league_ratio": 1.0, "cur": {"gp": gg["gp_cur"], "sv": gg["sv_cur"], "ga": gg["ga_cur"], "sa": gg["sa_cur"], "starts": gg["starts_cur"],
                                                    "gaa": gg.get("gaa_cur"), "gsax": gg.get("gsax_cur"), "xga": gg.get("xga_cur")},
                                            "last3": {"gp": gg.get("gp_3"), "sv": gg.get("sv_3"), "gaa": gg.get("gaa_3"), "gsax": gg.get("gsax_3")}, "vs": None}
        games_out.append({"id": gid, "uuid": gm.uuid, "date": gm.date, "start": gm.start.isoformat().replace("+00:00", "Z"), "state": "FUT",
                          "home": gm.home, "away": gm.away, "home_name": gm.home_name, "away_name": gm.away_name, "venue": gm.venue,
                          "hs": None, "as": None, "period": None, "last_period": None,
                          # expected goals before the opposing goalie (the page applies the selected goalie's ratio itself)
                          "mu": {"home": {"g": pr["mu"]["h"] / gr_(starters[gm.away]), "o": 0.0}, "away": {"g": pr["mu"]["a"] / gr_(starters[gm.home]), "o": 0.0}},
                          "starters": {gm.home: starters[gm.home], gm.away: starters[gm.away]},
                          "starter_status": {gm.home: "Projected", gm.away: "Projected"}, "starter_alt": {}, "b2b": {gm.home: False, gm.away: False},
                          "h2h": None, "hist_over": {"5.5": d["over_5.5"], "6.5": d["over_6.5"]},
                          "default": {k: (float(v) if not isinstance(v, dict) else {kk: float(vv) for kk, vv in v.items()}) for k, v in d.items()},
                          "props": props, "shots": None, "goalies": team_gk})
    gl = [{"id": g["id"], "home": g["home"], "away": g["away"], "home_name": g["home_name"], "away_name": g["away_name"]} for g in games_out]
    odds = parse_odds(gl, {int(k): v for k, v in players_out.items()})
    import base64
    logos = {}
    for r in sch[sch.season == cur].itertuples():
        for code, icon in ((r.home, r.home_logo), (r.away, r.away_logo)):
            if not code or code in logos:
                continue
            f = ROOT / "logos" / f"{code}.svg"
            src = ("data:image/svg+xml;base64," + base64.b64encode(f.read_bytes()).decode()) if f.exists() else icon
            if src:
                logos[code] = {"dark": src, "light": src}
    league = {"cur_gpg": st["gpg"], "cur_gpg_reg": None, "env": 1.0, "base_gpg": st["gpg"], "gpg_by_season": {}, "goalie_ga_xga": st["lg_ratio"], "shots_pg": None,
              "goals_per_shot": None, "saves_play_share": 0.965, "pos": {}, "shot_tiers": None}
    data = {"league_code": "SHL", "date": today, "season": cur, "league": league, "teams": teams_out, "games": games_out, "opp_pos": {}, "team_ranks": {},
            "players": players_out, "goalies": goalies_out, "news_status": {}, "league_lines": None, "odds": odds, "futures": None,
            "archive_dates": [], "logos": logos, "graded": None, "demo": False, "previews": {},
            "built": now.isoformat(timespec="minutes"), "shl": {"xg": xrep, "games_in_data": int(sch[sch.state == "post-game"].shape[0])}}
    from nhlprops.config import CONFIG
    cfg_js = {**CONFIG, "sog_lines": [1.5, 2.5, 3.5], "hit_lines": [0.5, 1.5, 2.5], "blk_lines": [0.5, 1.5], "fo_lines": [], "ou_lines": [5.5, 6.5],
              "tie_boost": CFG["tie_boost"], "tie_home_bonus": CFG["tie_home_bonus"], "total_compression": 1.0, "hb_dispersion": 50.0,
              "shots_r": CFG["shots_r"], "league": "SHL"}
    data["config"] = cfg_js
    OUT.mkdir(parents=True, exist_ok=True)
    js = json.dumps(data, separators=(",", ":"), default=lambda o: None if (isinstance(o, float) and (math.isnan(o) or math.isinf(o))) else (o.item() if hasattr(o, "item") else str(o))).replace("</", "<\\/")
    js = re.sub(r"\bNaN\b", "null", js)
    html = TEMPLATE.read_text(encoding="utf-8").replace("NHL Props Lab", "SHL Props Lab").replace("/*__DATA__*/null", js)
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / "data.json").write_text(js, encoding="utf-8")
    n_odds = sum(len(v["players"]) for v in odds.values())
    log(f"SHL page: {len(games_out)} games, {len(players_out)} players, odds for {len(odds)} games / {n_odds} players -> {OUT / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
