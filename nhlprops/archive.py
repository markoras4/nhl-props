"""Archive: every game of the last 7 days as it looked before puck drop, with what actually happened.

data/frozen/<date>.json already keeps each game's probabilities, odds and picks at its last pre-game refresh.
Here we add, per game, the player profiles, goalies and team context from that same moment (updated while the
game is still to come, then left alone), and after the game the real stat lines from the box score.
Files: data/archive/<date>.json (kept on the "archive" branch, not in the main history) and, for past dates,
output/archive/<date>.json, which the site loads only when someone opens that date.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

DAYS = 7
PRE = (None, "FUT", "PRE")


def _results(src, gid: int, pbp_game: dict | None) -> dict | None:
    box = src.boxscore(gid)
    if not box or box.get("gameState") not in ("OFF", "FINAL"):
        return None
    players = {}
    for side in ("homeTeam", "awayTeam"):
        grp = (box.get("playerByGameStats") or {}).get(side) or {}
        for key in ("forwards", "defense", "defensemen"):
            for p in grp.get(key) or []:
                players[str(p["playerId"])] = {"g": p.get("goals", 0) or 0, "a": p.get("assists", 0) or 0,
                                               "s": p.get("sog", p.get("shots", 0)) or 0, "h": p.get("hits", 0) or 0,
                                               "b": p.get("blockedShots", 0) or 0, "pim": p.get("pim", 0) or 0,
                                               "toi": p.get("toi"), "pp": p.get("powerPlayGoals", 0) or 0}
        for p in grp.get("goalies") or []:
            if p.get("toi") and p.get("toi") != "00:00":
                players[str(p["playerId"])] = {"sv": p.get("saves"), "sa": p.get("shotsAgainst"), "ga": p.get("goalsAgainst"),
                                               "toi": p.get("toi"), "gk": 1}
    fg = None
    if pbp_game:
        # power-play points and faceoffs won from the play-by-play (the box score has PP goals only)
        for p in players.values():
            if "gk" not in p:
                p["pp"] = 0
        for i, (sc, a1, a2, team, pp) in enumerate(pbp_game.get("gl") or []):
            if i == 0:
                fg = sc
            if pp:
                for x in (sc, a1, a2):
                    if x and str(x) in players:
                        players[str(x)]["pp"] = players[str(x)].get("pp", 0) + 1
        for w, l in pbp_game.get("fo") or []:
            if str(w) in players:
                players[str(w)]["fow"] = players[str(w)].get("fow", 0) + 1
    last = (box.get("gameOutcome") or {}).get("lastPeriodType")
    return {"hs": (box.get("homeTeam") or {}).get("score"), "as": (box.get("awayTeam") or {}).get("score"),
            "last": last, "fg": fg, "players": players, "has_pbp": bool(pbp_game)}


def build(slate: dict, src, data_dir: Path, out_dir: Path, pbp: dict | None = None, log=print) -> list[str]:
    arch_dir, frozen_dir = data_dir / "archive", data_dir / "frozen"
    arch_dir.mkdir(parents=True, exist_ok=True)
    today = slate["date"]
    d0 = dt.date.fromisoformat(today)
    keep_from = (d0 - dt.timedelta(days=DAYS)).isoformat()
    players, goalies, teams = slate.get("players") or {}, slate.get("goalies") or {}, slate.get("teams") or {}
    live = {str(g["id"]): g for g in slate["games"]}

    def snap_for(g: dict) -> dict:
        ids = {str(pr["id"]) for pr in g.get("props") or []}
        gk = {str(x) for t in (g.get("starters") or {}).values() for x in [t] if x}
        gk |= {str(x) for x in (g.get("starter_alt") or {}).values() if x}
        return {"players": {i: players[i] for i in ids if i in players},
                "goalies": {i: goalies[i] for i in gk if i in goalies},
                "teams": {t: teams[t] for t in (g.get("home"), g.get("away")) if t in teams}}

    published = []
    for f in sorted(frozen_dir.glob("20*.json")):
        d = f.stem
        if d < keep_from:
            continue
        frozen = json.loads(f.read_text(encoding="utf-8"))
        af = arch_dir / f"{d}.json"
        arch = json.loads(af.read_text(encoding="utf-8")) if af.exists() else {"date": d, "games": {}}
        changed = False
        for gid, fz in frozen.items():
            rec = arch["games"].get(gid) or {}
            g_live = live.get(gid)
            if g_live is not None and g_live.get("state") in PRE:
                # still to come: keep the player context in step with the frozen probabilities
                rec = {**snap_for(g_live), "at": fz.get("at"), "partial": False}
                changed = True
            elif not rec.get("players"):
                # started before the archive existed (or missed a refresh): fill in with today's player context
                rec = {**snap_for(fz["game"]), "at": fz.get("at"), "partial": True}
                changed = True
            if d < today and not rec.get("result"):
                try:
                    res = _results(src, int(gid), (pbp or {}).get(gid) or (pbp or {}).get(int(gid)))
                except Exception as e:  # noqa: BLE001
                    res = None
                    log(f"  archive: no box score for {gid}: {e}")
                if res:
                    rec["result"] = res
                    changed = True
            arch["games"][gid] = rec
        if changed:
            af.write_text(json.dumps(arch, separators=(",", ":")), encoding="utf-8")
        if d < today:
            # published file: a small DATA-like object the app swaps in when that date is opened
            games, odds = [], {}
            P, GK, T = {}, {}, {}
            for gid, fz in frozen.items():
                rec = arch["games"].get(gid) or {}
                g = dict(fz["game"])
                res = rec.get("result")
                if res:
                    g.update({"state": "OFF", "hs": res["hs"], "as": res["as"], "last_period": res.get("last")})
                g["frozen"] = fz.get("at")
                games.append(g)
                if fz.get("odds"):
                    odds[gid] = fz["odds"]
                P.update(rec.get("players") or {}); GK.update(rec.get("goalies") or {}); T.update(rec.get("teams") or {})
            if not games:
                continue
            out = {"date": d, "games": sorted(games, key=lambda x: x.get("start") or ""), "odds": odds,
                   "players": P, "goalies": GK, "teams": T,
                   "results": {gid: (arch["games"].get(gid) or {}).get("result") for gid in frozen},
                   "partial": [gid for gid in frozen if (arch["games"].get(gid) or {}).get("partial")]}
            (out_dir / "archive").mkdir(parents=True, exist_ok=True)
            (out_dir / "archive" / f"{d}.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
            published.append(d)
    for f in arch_dir.glob("20*.json"):
        if f.stem < keep_from:
            f.unlink()
    log(f"  archive: {len(published)} past dates published ({', '.join(published)})")
    return published
