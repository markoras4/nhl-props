"""Shot locations for every skater, every game this season (NHL play-by-play).

Each final game is downloaded once and reduced to a compact list of shot attempts, kept in
data/shots/<season>.json so later builds only fetch new games. The site gets output/shots.json,
loaded only when someone opens a player's breakdown.

Coordinates are normalised so every attempt is drawn toward the same net (x = +89 is the goal line).
Types: G goal, S shot on goal (saved), M missed the net, B blocked (location of the block).
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .sources import NHL

KIND = {"goal": "G", "shot-on-goal": "S", "missed-shot": "M", "blocked-shot": "B"}


def extract(pbp: dict) -> dict | None:
    """Compact shot list for one game: [player id, x, y, kind, team abbrev]."""
    if not pbp or pbp.get("gameState") not in ("OFF", "FINAL"):
        return None
    home = pbp.get("homeTeam") or {}
    away = pbp.get("awayTeam") or {}
    hid, aid = home.get("id"), away.get("id")
    abbr = {hid: home.get("abbrev"), aid: away.get("abbrev")}
    out = []
    for pl in pbp.get("plays") or []:
        k = KIND.get(pl.get("typeDescKey"))
        if not k:
            continue
        d = pl.get("details") or {}
        x, y = d.get("xCoord"), d.get("yCoord")
        pid = d.get("scoringPlayerId") if k == "G" else d.get("shootingPlayerId")
        team = d.get("eventOwnerTeamId")
        if x is None or y is None or not pid:
            continue
        if pl.get("periodDescriptor", {}).get("periodType") == "SO":
            continue                                   # shootout attempts are not game shots
        # checked against the season's data: for blocked shots the event owner is the shooting team as well
        shooter_team = team
        # direction: which net the shooting team attacks this period
        side = pl.get("homeTeamDefendingSide")
        if side in ("left", "right"):
            attacks_right = (side == "left") if shooter_team == hid else (side == "right")
            flip = not attacks_right
        else:
            zone = d.get("zoneCode")
            flip = (x < 0) if (zone == "O" or k != "B") else (x > 0)
        if flip:
            x, y = -x, -y
        out.append([int(pid), int(x), int(y), k, abbr.get(shooter_team) or ""])
    # goals with assists and strength (power play = more skaters, both goalies in), faceoffs, penalties per team
    goals, fo, pens = [], [], {}
    for pl in pbp.get("plays") or []:
        k, d = pl.get("typeDescKey"), pl.get("details") or {}
        if k == "goal" and (pl.get("periodDescriptor") or {}).get("periodType") != "SO" and d.get("scoringPlayerId"):
            sc = str(pl.get("situationCode") or "")
            team = d.get("eventOwnerTeamId")
            pp = len(sc) == 4 and sc[0] == "1" and sc[3] == "1" and ((team == hid and sc[2] > sc[1]) or (team == aid and sc[1] > sc[2]))
            goals.append([int(d["scoringPlayerId"]), int(d.get("assist1PlayerId") or 0), int(d.get("assist2PlayerId") or 0),
                          abbr.get(team) or "", 1 if pp else 0])
        elif k == "faceoff" and d.get("winningPlayerId") and d.get("losingPlayerId"):
            fo.append([int(d["winningPlayerId"]), int(d["losingPlayerId"])])
        elif k == "penalty" and (d.get("duration") or 0) in (2, 4, 5):
            t = abbr.get(d.get("eventOwnerTeamId")) or ""
            pens[t] = pens.get(t, 0) + 1
    return {"d": str(pbp.get("gameDate") or "")[:10], "s": out, "gl": goals, "fo": fo, "pen": pens,
            "h": abbr.get(hid) or "", "a": abbr.get(aid) or ""}


def collect(src, league_games, season: int, store_dir: Path, workers: int, log=print) -> dict:
    store_dir.mkdir(parents=True, exist_ok=True)
    f = store_dir / f"{season}.json"
    games = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    lg = league_games[(league_games.season == season) & (league_games.final)]
    # games stored before goals / faceoffs were added are fetched once more
    todo = [int(i) for i in lg.id if str(int(i)) not in games or "gl" not in games[str(int(i))]]
    if todo:
        log(f"Shot maps: downloading play-by-play for {len(todo)} new games ...")

        def one(gid):
            raw = src._download(f"{NHL}/gamecenter/{gid}/play-by-play")
            try:
                return gid, extract(json.loads(raw)) if raw else None
            except Exception:
                return gid, None
        with ThreadPoolExecutor(workers) as ex:
            for gid, rec in ex.map(one, todo):
                if rec:
                    games[str(gid)] = rec
        f.write_text(json.dumps(games, separators=(",", ":")), encoding="utf-8")
    log(f"  shot maps: {len(games)} games, {sum(len(g['s']) for g in games.values())} attempts")
    return games


def player_file(games: dict, out: Path) -> None:
    """Per player: dates of his games with attempts, and his attempts [x, y, kind, date index, opponent?]."""
    by: dict[int, dict] = {}
    for gid, g in sorted(games.items(), key=lambda kv: kv[1]["d"]):
        for pid, x, y, k, team in g["s"]:
            p = by.setdefault(pid, {"d": [], "s": []})
            if not p["d"] or p["d"][-1] != g["d"]:
                p["d"].append(g["d"])
            p["s"].append([x, y, k, len(p["d"]) - 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({str(k): v for k, v in by.items()}, separators=(",", ":")), encoding="utf-8")


def close_range(x: int, y: int) -> bool:
    """Inside about 20 feet of the net, in front of the goal line (the slot)."""
    return x <= 89 and math.hypot(89 - x, y) <= 20


def build(src, league_games, season: int, store_dir: Path, out: Path, workers: int, log=print) -> None:
    games = collect(src, league_games, season, store_dir, workers, log)
    player_file(games, out)
