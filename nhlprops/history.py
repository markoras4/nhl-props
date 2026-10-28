"""Three seasons of game-by-game history per player, for the game-by-game chart (loaded on demand by the site).

Per game: date, opponent, home, goals, assists, shots, ice time, power-play points, PIM (NHL game logs), hits and
blocked shots (box scores), and expected goals (MoneyPuck's shot files: every unblocked attempt's xG, summed per player
per game). Past seasons are processed once and kept in small derived files; the current season is refreshed.
Output: output/gl/<player id>.json = [[date, opp, home, G, A, SOG, TOI, PPP, PIM, hits, blocks, xG], ...] newest first.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MP_SHOTS = "https://peter-tanner.com/moneypuck/downloads/shots_{y}.zip"
NHL_BOX = "https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore"


def mp_xg(src, season: int, current: bool, store: Path, log=print) -> dict:
    """{(player id, NHL game id): xG} for one season, regular season and playoffs."""
    f = store / f"mp_xg_{season}.json"
    if f.exists() and not current:
        return {tuple(map(int, k.split("|"))): v for k, v in json.loads(f.read_text()).items()}
    raw = src._download(MP_SHOTS.format(y=season))
    if not raw:
        if f.exists():
            return {tuple(map(int, k.split("|"))): v for k, v in json.loads(f.read_text()).items()}
        return {}
    out: dict = {}
    z = zipfile.ZipFile(io.BytesIO(raw))
    with z.open(z.namelist()[0]) as fh:
        r = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
        hdr = next(r)
        ix = {c: hdr.index(c) for c in ("season", "game_id", "shooterPlayerId", "xGoal")}
        for row in r:
            try:
                pid = int(float(row[ix["shooterPlayerId"]]))
                gid = int(row[ix["season"]]) * 1_000_000 + int(row[ix["game_id"]])
                out[(pid, gid)] = out.get((pid, gid), 0.0) + float(row[ix["xGoal"]] or 0)
            except (ValueError, IndexError):
                continue
    f.write_text(json.dumps({f"{k[0]}|{k[1]}": round(v, 3) for k, v in out.items()}, separators=(",", ":")))
    log(f"  history: MoneyPuck xG for {season}: {len(out):,} player-games")
    return out


def box_hb(src, season: int, gids: list[int], current: bool, store: Path, workers: int, log=print) -> dict:
    """{(player id, game id): (hits, blocks)} from box scores; past seasons kept in a small derived file."""
    f = store / f"hb_{season}.json"
    have = json.loads(f.read_text()) if f.exists() else {}
    todo = [g for g in gids if str(g) not in have]
    if todo:
        log(f"  history: {len(todo)} box scores for hits/blocks ({season})")

        def one(gid):
            raw = src._download(NHL_BOX.format(gid=gid))
            try:
                box = json.loads(raw) if raw else None
            except ValueError:
                box = None
            if not box or box.get("gameState") not in ("OFF", "FINAL"):
                return gid, None
            rows = {}
            for side in ("homeTeam", "awayTeam"):
                grp = (box.get("playerByGameStats") or {}).get(side) or {}
                for key in ("forwards", "defense", "defensemen"):
                    for p in grp.get(key) or []:
                        rows[str(p["playerId"])] = [p.get("hits", 0) or 0, p.get("blockedShots", 0) or 0]
            return gid, rows
        with ThreadPoolExecutor(workers) as ex:
            for gid, rows in ex.map(one, todo):
                if rows is not None:
                    have[str(gid)] = rows
        f.write_text(json.dumps(have, separators=(",", ":")))
    out = {}
    for gid, rows in have.items():
        for pid, v in rows.items():
            out[(int(pid), int(gid))] = tuple(v)
    return out


def build(raw: dict, slate: dict, src, out_dir: Path, store: Path, workers: int, log=print) -> int:
    store.mkdir(parents=True, exist_ok=True)
    cur = int(raw["season"])
    lg = raw["league_games"]
    xg, hb = {}, {}
    for y in (cur - 2, cur - 1, cur):
        try:
            xg.update(mp_xg(src, y, y == cur, store, log))
        except Exception as e:  # noqa: BLE001
            log(f"  history: MoneyPuck xG {y} skipped: {e}")
        try:
            gids = [int(i) for i in lg[(lg.season == y) & (lg.final)].id]
            hb.update(box_hb(src, y, gids, y == cur, store, workers, log))
        except Exception as e:  # noqa: BLE001
            log(f"  history: box scores {y} skipped: {e}")
    mp_games = {gid for _, gid in xg}          # games MoneyPuck has processed (0 xG = played without an attempt)
    (out_dir / "gl").mkdir(parents=True, exist_ok=True)
    n = 0
    for pid in (slate.get("players") or {}):
        L = (raw.get("logs") or {}).get(int(pid)) or {}
        games = (L.get("cur") or []) + (L.get("prev") or []) + (L.get("prev2") or [])
        rows = []
        for g in games:
            gid = g.get("game_id")
            key = (int(pid), int(gid)) if gid else None
            h_b = hb.get(key) if key else None
            x = xg.get(key, 0.0) if key and key[1] in mp_games else None
            rows.append([g["date"], g["opp"], 1 if g["home"] else 0, g["g"], g["a"], g["s"], round(g["toi"], 1),
                         g.get("ppp", 0), g.get("pim", 0), h_b[0] if h_b else None, h_b[1] if h_b else None,
                         round(x, 2) if x is not None else None])
        if rows:
            (out_dir / "gl" / f"{pid}.json").write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
            n += 1
            # the chart's built-in last 40 games use the same numbers (MoneyPuck xG where it has the game, box-score hits)
            by_date = {r[0]: r for r in rows}
            prof = slate["players"][pid]
            for row in (prof.get("form") or {}).get("gl") or []:
                r = by_date.get(row[0])
                if r:
                    if r[11] is not None:
                        row[11] = r[11]
                    if row[9] is None and r[9] is not None:
                        row[9], row[10] = r[9], r[10]
            for gm in (prof.get("form") or {}).get("games") or []:
                r = by_date.get(gm.get("d"))
                if r and r[11] is not None:
                    gm["x"] = r[11]
    log(f"  history: game-by-game files for {n} players (3 seasons)")
    return n
