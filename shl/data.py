"""Parse stored SHL games into tidy tables: games, player games, goalie games, shots (with strength and context)."""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import pandas as pd

from .collect import ROOT, rgz

POSG = {"CE": "F", "LW": "F", "RW": "F", "F": "F", "LD": "D", "RD": "D", "D": "D", "GK": "G"}


def _sec(period, clock):
    try:
        m, s = str(clock).split(":")
        return (int(period) - 1) * 1200 + int(m) * 60 + int(s)
    except (ValueError, TypeError):
        return None


def _toi(s):
    try:
        m, sec = str(s).split(":")
        return int(m) + int(sec) / 60
    except (ValueError, TypeError):
        return 0.0


def schedules():
    rows = []
    for p in sorted(ROOT.glob("sched_*.json.gz")):
        season = int(p.stem.split("_")[1].split(".")[0])
        for g in rgz(p).get("gameInfo", []):
            h, a = g.get("homeTeamInfo") or {}, g.get("awayTeamInfo") or {}
            rows.append({"uuid": g["uuid"], "season": season, "start": g.get("rawStartDateTime"), "state": g.get("state"),
                         "home": h.get("code"), "away": a.get("code"), "home_uuid": h.get("uuid"), "away_uuid": a.get("uuid"),
                         "home_name": (h.get("names") or {}).get("long") or h.get("code"), "away_name": (a.get("names") or {}).get("long") or a.get("code"),
                         "home_logo": h.get("icon"), "away_logo": a.get("icon"),
                         "hs": h.get("score"), "as": a.get("score"), "ot": bool(g.get("overtime")), "so": bool(g.get("shootout")),
                         "venue": (g.get("venueInfo") or {}).get("name"), "round": g.get("roundNumber"), "ssgt": g.get("ssgtUuid")})
    df = pd.DataFrame(rows)
    if len(df):
        df["start"] = pd.to_datetime(df.start, utc=True)
        df["date"] = df.start.dt.tz_convert("Europe/Stockholm").dt.strftime("%Y-%m-%d")
    return df


def _strength_timeline(pbp):
    """penalty windows per side and goalie-pulled windows, as lists of (start, end) in game seconds"""
    pens = {"home": [], "away": []}
    pulled = {"home": [], "away": []}
    goals = []
    out_since = {}
    ev = sorted([e for e in pbp if e.get("type") in ("penalty", "goal", "goalkeeper")], key=lambda e: (_sec(e.get("period"), e.get("time")) or 0, e.get("eventId") or 0))
    for e in ev:
        t = _sec(e.get("period"), e.get("time"))
        if t is None:
            continue
        side = (e.get("eventTeam") or {}).get("place")
        if e["type"] == "penalty" and side in pens:
            v = e.get("variant") or {}
            dur = 0
            try:
                dur = max(int(v.get("minorTime") or 0) * 60, int(v.get("doubleMinorTime") or 0) * 60, int(v.get("majorTime") or 0) * 60,
                          int(v.get("benchTime") or 0) * 60)
            except ValueError:
                dur = 0
            if dur:
                pens[side].append([t, t + dur, dur <= 120])      # minors can end early on a power-play goal
        elif e["type"] == "goal":
            goals.append((t, side))
        elif e["type"] == "goalkeeper" and side in pulled:
            if e.get("isEntering") is False:
                out_since[side] = t
            elif e.get("isEntering") is True and side in out_since:
                pulled[side].append((out_since.pop(side), t))
    for side, t0 in out_since.items():
        pulled[side].append((t0, 99999))
    # a power-play goal ends the earliest running minor of the shorthanded side
    for t, scorer in goals:
        other = "away" if scorer == "home" else "home"
        if scorer not in pens:
            continue
        man = lambda s: sum(1 for p in pens[s] if p[0] <= t < p[1])
        if man(other) > man(scorer):
            run = sorted([p for p in pens[other] if p[0] <= t < p[1] and p[2]], key=lambda p: p[0])
            if run:
                run[0][1] = t + 1          # the goal itself is still on the power play
    return pens, pulled


def _state_at(t, side, pens, pulled):
    other = "away" if side == "home" else "home"
    sk = {s: 5 - sum(1 for p in pens[s] if p[0] <= t < p[1]) for s in ("home", "away")}
    sk = {s: max(3, v) for s, v in sk.items()}
    if t >= 3600:                                           # SHL overtime is 3-on-3
        sk = {s: max(3, v - 2) for s, v in sk.items()}
    g_out = {s: any(a <= t < b for a, b in pulled[s]) for s in ("home", "away")}
    for s in ("home", "away"):
        if g_out[s]:
            sk[s] += 1
    return sk[side], sk[other], g_out[other], g_out[side]


def parse_game(path: Path, meta: dict):
    d = rgz(path)
    pbp, box = d.get("pbp") or [], d.get("box") or {}
    uuid = d["uuid"]
    code = {"home": meta["home"], "away": meta["away"]}
    players, goalies, shots, goals = [], [], [], []
    names = {}
    for side, key in (("home", "homeTeamValue"), ("away", "awayTeamValue")):
        for pid, nm in ((box.get("players") or {}).get(key) or {}).items():
            names[int(pid)] = nm.get("fullName")
        for pid, nm in ((box.get("goalkeepers") or {}).get(key) or {}).items():
            names[int(pid)] = nm.get("fullName")
        for r in (box.get("stats") or {}).get(key) or []:
            if (r.get("info") or {}).get("period", 0) != 0:
                continue
            pid = int(r["info"]["playerId"])
            players.append({"uuid": uuid, "pid": pid, "team": code[side], "opp": code["away" if side == "home" else "home"], "home": side == "home",
                            "num": r.get("NR"), "pos": r.get("POS"), "posg": POSG.get(r.get("POS"), "F"), "line": r.get("LINE"),
                            "g": r.get("G", 0), "a": r.get("A", 0), "ppg": r.get("PPG", 0), "shg": r.get("SHG", 0), "pim": r.get("PIM", 0),
                            "s": r.get("SOG", 0), "pps": r.get("PPSOG", 0), "ms": r.get("MS", 0), "blk": r.get("BkS", 0), "hit": r.get("Hits", 0),
                            "fow": r.get("FOW", 0), "fol": r.get("FOL", 0), "pm": r.get("+/-", 0), "toi": _toi(r.get("TOI"))})
        for r in (box.get("gkStats") or {}).get(key) or []:
            if (r.get("info") or {}).get("period", 0) != 0:
                continue
            goalies.append({"uuid": uuid, "pid": int(r["info"]["playerId"]), "team": code[side], "opp": code["away" if side == "home" else "home"],
                            "home": side == "home", "num": r.get("NR"), "sa": r.get("SOGA", 0) or 0, "ga": r.get("GA", 0) or 0, "sv": r.get("SVS", 0) or 0})
    pens, pulled = _strength_timeline(pbp)
    # starting goalies: the first goalie entering for each side
    starters = {}
    for e in sorted([e for e in pbp if e.get("type") == "goalkeeper" and e.get("isEntering")], key=lambda e: (_sec(e.get("period"), e.get("time")) or 0)):
        side = (e.get("eventTeam") or {}).get("place")
        if side in code and side not in starters:
            try:
                starters[side] = int(e["player"]["playerId"])
            except (KeyError, ValueError, TypeError):
                pass
    for gk in goalies:
        side = "home" if gk["home"] else "away"
        gk["started"] = starters.get(side) == gk["pid"] if starters.get(side) else gk["sa"] > 0
    att = sorted([e for e in pbp if e.get("type") in ("shot", "goal") and e.get("locationX") is not None],
                 key=lambda e: (_sec(e.get("period"), e.get("time")) or 0, e.get("eventId") or 0))
    last = {}
    for e in att:
        side = (e.get("eventTeam") or {}).get("place")
        if side not in code:
            continue
        t = _sec(e.get("period"), e.get("time"))
        if t is None:
            continue
        try:
            pid = int((e.get("player") or {}).get("playerId"))
        except (TypeError, ValueError):
            pid = None
        x, y = float(e["locationX"]), float(e["locationY"])
        sec = e.get("goalSection")
        goal = e["type"] == "goal"
        kind = "goal" if goal else ("block" if sec == -3 else ("miss" if sec in (0, -1) else "on"))
        sk_for, sk_ag, en_against, en_for = _state_at(t, side, pens, pulled)
        prev = last.get(side)
        reb = bool(prev and prev[0] == e.get("period") and 0 <= t - prev[1] <= 3)
        if kind != "block":
            last[side] = (e.get("period"), t)
        dist = math.hypot(x, y) / 10.0
        ang = math.degrees(math.atan2(abs(y), x)) if (x or y) else 0.0
        shots.append({"uuid": uuid, "period": e.get("period"), "t": t, "team": code[side], "opp": code["away" if side == "home" else "home"],
                      "home": side == "home", "pid": pid, "x": x, "y": y, "dist": dist, "ang": ang, "kind": kind, "goal": goal,
                      "pen_shot": bool(e.get("isPenaltyShot")), "reb": reb, "sk_for": sk_for, "sk_ag": sk_ag,
                      "en": bool(e.get("isEmptyNetGoal")) or en_against, "status": e.get("goalStatus")})
        if goal:
            asst = e.get("assists") or {}
            goals.append({"uuid": uuid, "t": t, "team": code[side], "scorer": pid,
                          "a1": int(asst["first"]["playerId"]) if asst.get("first") else None,
                          "a2": int(asst["second"]["playerId"]) if asst.get("second") else None,
                          "status": e.get("goalStatus"), "en": bool(e.get("isEmptyNetGoal")),
                          "pop": [int(p["playerId"]) for p in e.get("pop") or [] if p.get("playerId")],
                          "nep": [int(p["playerId"]) for p in e.get("nep") or [] if p.get("playerId")]})
    return players, goalies, shots, goals, names


def load_all(log=print):
    sch = schedules()
    meta = sch.set_index("uuid").to_dict("index")
    P, G, S, GL, names = [], [], [], [], {}
    for p in sorted((ROOT / "games").glob("*/*.json.gz")):
        uuid = p.name.split(".")[0]
        m = meta.get(uuid)
        if not m:
            continue
        try:
            a, b, c, d, n = parse_game(p, m)
        except Exception as e:  # noqa: BLE001
            log(f"  parse failed {uuid}: {e}")
            continue
        for rows, out in ((a, P), (b, G), (c, S), (d, GL)):
            for r in rows:
                r["season"], r["date"] = m["season"], m["date"]
            out.extend(rows)
        names.update(n)
    return sch, pd.DataFrame(P), pd.DataFrame(G), pd.DataFrame(S), pd.DataFrame(GL), names
