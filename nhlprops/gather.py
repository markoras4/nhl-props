"""Download (or load from cache) everything a slate needs."""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .sources import Source, teams_for_season


def season_start_for(date: str) -> int:
    d = dt.date.fromisoformat(date)
    return d.year if d.month >= 8 else d.year - 1


def _name(obj) -> str:
    if isinstance(obj, dict):
        return obj.get("default") or next(iter(obj.values()), "")
    return obj or ""


def slate_games(sched: dict | None, date: str, days_ahead: int = 0) -> list[dict]:
    if not sched:
        return []
    days = sched.get("gameWeek") or []
    last = (dt.date.fromisoformat(date) + dt.timedelta(days=days_ahead)).isoformat()
    games = []
    for day in days:
        if not (date <= (day.get("date") or "") <= last):
            continue
        for g in day.get("games", []):
            if g.get("gameType") not in (2, 3):
                continue
            games.append({
                "id": g["id"], "date": day["date"],
                "start": g.get("startTimeUTC"),
                "state": g.get("gameState"),
                "home": _team(g["homeTeam"]["abbrev"]),
                "away": _team(g["awayTeam"]["abbrev"]),
                "home_name": _name(g["homeTeam"].get("placeName")) + " " + _name(g["homeTeam"].get("commonName", "")),
                "away_name": _name(g["awayTeam"].get("placeName")) + " " + _name(g["awayTeam"].get("commonName", "")),
                "venue": _name(g.get("venue")),
            })
    return games


def _team(a):
    return "UTA" if a == "ARI" else a  # Arizona's history continues as Utah


def parse_club_games(js: dict | None, start_year: int) -> list[dict]:
    rows = []
    for g in (js or {}).get("games", []):
        if g.get("gameType") != 2:
            continue
        home, away = g.get("homeTeam", {}), g.get("awayTeam", {})
        last = ((g.get("gameOutcome") or {}).get("lastPeriodType")
                or (g.get("periodDescriptor") or {}).get("periodType") or "REG")
        rows.append({
            "id": g["id"], "season": start_year, "date": g.get("gameDate"),
            "home": _team(home.get("abbrev")), "away": _team(away.get("abbrev")),
            "hs": home.get("score"), "as_": away.get("score"),
            "final": g.get("gameState") in ("OFF", "FINAL"),
            "last": last,
        })
    return rows


def parse_log(js: dict | None) -> list[dict]:
    out = []
    for g in (js or {}).get("gameLog", []):
        toi = g.get("toi") or "0:00"
        try:
            m, s = toi.split(":")
            toi_min = int(m) + int(s) / 60
        except Exception:
            toi_min = 0.0
        out.append({
            "game_id": g.get("gameId"), "date": g.get("gameDate"),
            "team": _team(g.get("teamAbbrev")), "opp": _team(g.get("opponentAbbrev")),
            "home": g.get("homeRoadFlag") == "H",
            "g": g.get("goals", 0) or 0, "a": g.get("assists", 0) or 0,
            "p": g.get("points", 0) or 0, "s": g.get("shots", 0) or 0,
            "ppp": g.get("powerPlayPoints", 0) or 0, "toi": toi_min,
            # goalie fields
            "gs": g.get("gamesStarted", 0) or 0, "sa": g.get("shotsAgainst", 0) or 0,
            "ga": g.get("goalsAgainst", 0) or 0, "dec": g.get("decision"),
        })
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    return out


def gather(src: Source, date: str, cfg: dict, log=print) -> dict:
    cur = season_start_for(date)
    n_mp = max(cfg["lookback_seasons"], cfg["league_env_seasons"], cfg["goalie_seasons"])
    mp_years = [cur - i for i in range(n_mp)]
    club_years = [cur - i for i in range(4)]

    log(f"Season {cur}-{cur + 1}. Fetching schedule from {date} ...")
    games = slate_games(src.schedule(date), date, cfg["days_ahead"])
    games = [g for g in games if g["state"] not in ("OFF", "FINAL")]
    log(f"  {len(games)} upcoming games over {len({g['date'] for g in games})} days")
    if not games:
        return {"date": date, "season": cur, "games": [], "errors": list(src.errors)}

    log("Fetching MoneyPuck season summaries ...")
    mp = {"skaters": {}, "goalies": {}, "teams": {}}
    for kind in mp:
        for y in mp_years:
            df = src.moneypuck(kind, y, current=(y == cur))
            if df is not None:
                mp[kind][y] = df
    log(f"  seasons loaded: skaters {sorted(mp['skaters'])}, teams {sorted(mp['teams'])}")

    log("Fetching team schedules/results (all 32 teams, 4 seasons) ...")
    jobs = [(t, y) for y in club_years for t in teams_for_season(y)]
    with ThreadPoolExecutor(cfg["workers"]) as ex:
        res = list(ex.map(lambda ty: (ty[1], src.club_schedule(ty[0], ty[1], ty[1] == cur)), jobs))
    rows = []
    for y, js in res:
        rows += parse_club_games(js, y)
    league_games = pd.DataFrame(rows).drop_duplicates("id") if rows else pd.DataFrame(
        columns=["id", "season", "date", "home", "away", "hs", "as_", "final", "last"])
    log(f"  {len(league_games)} league games")

    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    log(f"Fetching rosters for {len(teams)} teams ...")
    with ThreadPoolExecutor(cfg["workers"]) as ex:
        rosters = dict(zip(teams, ex.map(src.roster, teams)))

    skaters, goalies = [], []
    for t, r in rosters.items():
        for grp in ("forwards", "defensemen"):
            for p in (r or {}).get(grp, []):
                skaters.append({"id": p["id"], "team": t, "pos": p.get("positionCode", "F"),
                                "name": f"{_name(p.get('firstName'))} {_name(p.get('lastName'))}".strip(),
                                "num": p.get("sweaterNumber"), "headshot": p.get("headshot")})
        for p in (r or {}).get("goalies", []):
            goalies.append({"id": p["id"], "team": t, "pos": "G",
                            "name": f"{_name(p.get('firstName'))} {_name(p.get('lastName'))}".strip(),
                            "num": p.get("sweaterNumber")})

    log(f"Fetching {len(skaters)} skaters and {len(goalies)} goalies (careers + game logs) ...")

    def fetch_skater(p):
        pid = p["id"]
        return pid, src.landing(pid), parse_log(src.game_log(pid, cur, True)), parse_log(src.game_log(pid, cur - 1, False))

    def fetch_goalie(p):
        pid = p["id"]
        logs = [parse_log(src.game_log(pid, cur - i, i == 0)) for i in range(3)]
        return pid, src.landing(pid), logs

    with ThreadPoolExecutor(cfg["workers"]) as ex:
        sk = list(ex.map(fetch_skater, skaters))
        go = list(ex.map(fetch_goalie, goalies))

    landing, logs, glogs = {}, {}, {}
    for pid, land, lc, lp in sk:
        landing[pid] = land
        logs[pid] = {"cur": lc, "prev": lp}
    for pid, land, gl in go:
        landing[pid] = land
        glogs[pid] = gl

    from .news import fetch_news
    news = fetch_news(src, teams, sorted({g["date"] for g in games}), log)

    if src.errors:
        log(f"  {len(src.errors)} requests failed (first: {src.errors[0]})")

    return {
        "date": date, "season": cur, "games": games, "mp": mp,
        "league_games": league_games, "rosters": rosters,
        "skaters": skaters, "goalies": goalies,
        "landing": landing, "logs": logs, "glogs": glogs, "news": news,
        "errors": list(src.errors),
    }
