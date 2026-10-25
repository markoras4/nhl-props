"""Simulated data in the exact file formats of the real sources.

Used to test the whole pipeline without internet (`python -m nhlprops build --demo`).
Numbers are random - never bet on demo output.
"""
from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

from .sources import NHL_TEAMS, MP_TO_NHL, season_id, teams_for_season

NHL_TO_MP = {v: k for k, v in MP_TO_NHL.items()}
FIRST = ["Erik", "Lucas", "Mikko", "Jack", "Nils", "Connor", "Ivan", "Oskar", "Leo", "Matt", "Adam", "Viktor",
         "Sam", "Elias", "Ryan", "Kasper", "Noah", "Anton", "Jesper", "Tyler", "Filip", "Max", "Alex", "Owen"]
LAST = ["Lindqvist", "Hartley", "Koskinen", "Marchand", "Novak", "Berglund", "Sorensen", "Duclair", "Haapala",
        "Kowalski", "Brennan", "Ekholm", "Larkin", "Petrov", "Wallin", "Dahlberg", "Stone", "Moreau", "Nyberg",
        "Kane", "Fischer", "Lundell", "Barrett", "Rask", "Holm", "Sutter", "Vrana", "Granlund", "Okafor"]


def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, pd.DataFrame):
        obj.to_csv(path, index=False)
    else:
        path.write_text(json.dumps(obj), encoding="utf-8")


def _mmss(m: float) -> str:
    return f"{int(m)}:{int(round((m % 1) * 60)) % 60:02d}"


def generate(cache: Path, date: str, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    random.seed(seed)
    d0 = dt.date.fromisoformat(date)
    cur = d0.year if d0.month >= 8 else d0.year - 1
    years = [cur - i for i in range(5)]

    att = {t: rng.normal(1, 0.09) for t in NHL_TEAMS + ["ARI"]}
    dfn = {t: rng.normal(1, 0.08) for t in NHL_TEAMS + ["ARI"]}

    # ---------------- league schedules + results
    gid = 0
    games = {y: [] for y in years}
    for y in years:
        teams = teams_for_season(y)
        start = dt.date(y, 10, 8)
        n_days = 180 if y < cur else (d0 - start).days  # current season: up to yesterday
        per_day = 1312 / 180
        for day in range(n_days):
            d = (start + dt.timedelta(days=day)).isoformat()
            k = int(rng.poisson(per_day))
            pool = random.sample(teams, min(2 * k, len(teams)))
            for i in range(0, len(pool) - 1, 2):
                h, a = pool[i], pool[i + 1]
                gid += 1
                mh = 3.05 * att[h] / dfn[a] * 1.03
                ma = 3.05 * att[a] / dfn[h] / 1.03
                hs, as_ = int(rng.poisson(mh)), int(rng.poisson(ma))
                last = "REG"
                if hs == as_:
                    last = "OT" if rng.random() < 0.6 else "SO"
                    if rng.random() < 0.52:
                        hs += 1
                    else:
                        as_ += 1
                games[y].append({"id": int(f"{y}02{gid % 10000:04d}"), "date": d, "home": h, "away": a,
                                 "hs": hs, "as": as_, "last": last})

    # slate: 8 games, include teams that played yesterday (back-to-backs)
    yesterday = (d0 - dt.timedelta(days=1)).isoformat()
    played_y = [g for g in games[cur] if g["date"] == yesterday]
    b2b_teams = [played_y[0]["home"]] if played_y else []
    rest = [t for t in NHL_TEAMS if t not in b2b_teams]
    random.shuffle(rest)
    slate_teams = (b2b_teams + rest)[:16]
    slate = []
    for i in range(0, 16, 2):
        gid += 1
        slate.append({"id": int(f"{cur}02{(gid % 10000):04d}"), "home": slate_teams[i + 1], "away": slate_teams[i],
                      "start": f"{date}T{23 + (i // 6) % 2:02d}:00:00Z"})

    _write(cache / f"nhl/schedule/{date}.json", {"gameWeek": [{"date": date, "games": [{
        "id": g["id"], "gameType": 2, "startTimeUTC": g["start"], "gameState": "FUT",
        "venue": {"default": f"{g['home']} Arena"},
        "homeTeam": {"abbrev": g["home"], "placeName": {"default": g["home"]}, "commonName": {"default": "Demo"}},
        "awayTeam": {"abbrev": g["away"], "placeName": {"default": g["away"]}, "commonName": {"default": "Demo"}},
    } for g in slate]}]})

    for y in years[:4]:
        for t in teams_for_season(y):
            gl = [g for g in games[y] if t in (g["home"], g["away"])]
            _write(cache / f"nhl/club/{t}_{season_id(y)}.json", {"games": [{
                "id": g["id"], "gameType": 2, "gameDate": g["date"], "gameState": "OFF",
                "homeTeam": {"abbrev": g["home"], "score": g["hs"]}, "awayTeam": {"abbrev": g["away"], "score": g["as"]},
                "gameOutcome": {"lastPeriodType": g["last"]}} for g in gl]})

    # ---------------- players
    pid = 8470000
    roster_rows, sk_rows, go_rows = {}, {y: [] for y in years}, {y: [] for y in years}
    team_games = {y: {t: [g for g in games[y] if t in (g["home"], g["away"])] for t in NHL_TEAMS} for y in years}

    for t in NHL_TEAMS:
        r = {"forwards": [], "defensemen": [], "goalies": []}
        for slot in range(12 + 7 + 2):
            pid += 1
            pos = "G" if slot >= 19 else ("D" if slot >= 12 else random.choice("CLR"))
            name = (random.choice(FIRST), random.choice(LAST))
            exp_seasons = int(rng.integers(0, 12))
            age = 20 + exp_seasons + int(rng.integers(0, 3))
            ent = {"id": pid, "firstName": {"default": name[0]}, "lastName": {"default": name[1]},
                   "positionCode": pos, "sweaterNumber": int(rng.integers(2, 99))}
            r["goalies" if pos == "G" else ("defensemen" if pos == "D" else "forwards")].append(ent)

            if pos == "G":
                q = rng.normal(1.0, 0.06)
                starter = slot == 19
                season_totals, glogs = [], {}
                for y in years:
                    if cur - y >= exp_seasons + 1:
                        continue
                    tg = team_games[y][t]
                    starts = [g for g in tg if rng.random() < (0.68 if starter else 0.32)]
                    gp = len(starts)
                    if gp == 0:
                        continue
                    xga = rng.gamma(gp * 30, 0.0947 / 1)
                    ga = int(rng.poisson(xga * q))
                    sa = int(gp * rng.normal(28.5, 1.5))
                    go_rows[y].append({"playerId": pid, "season": y, "name": " ".join(name), "team": NHL_TO_MP.get(t, t),
                                       "position": "G", "situation": "all", "games_played": gp,
                                       "icetime": gp * 3550, "xGoals": round(xga, 2), "goals": ga, "ongoal": sa})
                    if y >= cur - 2:
                        glogs[y] = [{"gameId": g["id"], "gameDate": g["date"], "teamAbbrev": t,
                                     "opponentAbbrev": g["away"] if g["home"] == t else g["home"],
                                     "homeRoadFlag": "H" if g["home"] == t else "R", "gamesStarted": 1,
                                     "shotsAgainst": int(rng.normal(29, 5)), "goalsAgainst": int(rng.poisson(2.9 * q)),
                                     "toi": "60:00", "decision": random.choice("WLO")} for g in starts]
                for y in years[:3]:
                    _write(cache / f"nhl/gamelog/{pid}_{season_id(y)}.json", {"gameLog": glogs.get(y, [])[::-1]})
                _write(cache / f"nhl/landing/{pid}.json", {"birthDate": f"{2026 - age}-03-01", "seasonTotals": []})
                continue

            F = pos != "D"
            toi = rng.uniform(12, 21) if F else rng.uniform(16, 25)
            s60 = rng.uniform(5, 11) if F else rng.uniform(3, 6)
            sh = rng.uniform(0.07, 0.16) if F else rng.uniform(0.03, 0.07)
            a60 = rng.uniform(0.8, 2.2) if F else rng.uniform(0.6, 1.6)
            injured = rng.random() < 0.06
            season_totals = []
            car = {"gamesPlayed": 0, "goals": 0, "assists": 0, "points": 0, "shots": 0}
            for back in range(exp_seasons, -1, -1):
                y = cur - back
                if y == cur:
                    tg = [g for g in team_games[cur][t]]
                    if injured:
                        tg = tg[:-4]
                else:
                    tg = team_games[y][t] if y in team_games else [None] * int(rng.integers(55, 82))
                    tg = tg[: int(len(tg) * rng.uniform(0.7, 1.0))]
                gp = len(tg)
                if gp == 0:
                    continue
                hrs = gp * toi / 60
                s = int(rng.poisson(s60 * hrs))
                gls = int(rng.binomial(s, sh))
                ast = int(rng.poisson(a60 * hrs))
                xg = s * sh * rng.normal(1, 0.08) * (0.9 + 0.2 * rng.random())
                season_totals.append({"season": int(season_id(y)), "gameTypeId": 2, "leagueAbbrev": "NHL",
                                      "gamesPlayed": gp, "goals": gls, "assists": ast, "points": gls + ast, "shots": s})
                for k, v in (("gamesPlayed", gp), ("goals", gls), ("assists", ast), ("points", gls + ast), ("shots", s)):
                    car[k] += v
                if y in years:
                    sk_rows[y].append({"playerId": pid, "season": y, "name": " ".join(name), "team": NHL_TO_MP.get(t, t),
                                       "position": pos, "situation": "all", "games_played": gp,
                                       "icetime": int(hrs * 3600), "I_F_shotsOnGoal": s, "I_F_goals": gls,
                                       "I_F_xGoals": round(xg, 2), "I_F_primaryAssists": int(ast * 0.55),
                                       "I_F_secondaryAssists": ast - int(ast * 0.55), "I_F_points": gls + ast})
                if y >= cur - 1:
                    log = []
                    for g in tg:
                        tt = max(5, rng.normal(toi, 2.2))
                        ss = int(rng.poisson(s60 * tt / 60))
                        gg = int(rng.binomial(ss, sh))
                        aa = int(rng.poisson(a60 * tt / 60))
                        log.append({"gameId": g["id"], "gameDate": g["date"], "teamAbbrev": t,
                                    "opponentAbbrev": g["away"] if g["home"] == t else g["home"],
                                    "homeRoadFlag": "H" if g["home"] == t else "R", "goals": gg, "assists": aa,
                                    "points": gg + aa, "shots": ss, "powerPlayPoints": 0, "toi": _mmss(tt)})
                    _write(cache / f"nhl/gamelog/{pid}_{season_id(y)}.json", {"gameLog": log[::-1]})
            for y in (cur, cur - 1):
                p = cache / f"nhl/gamelog/{pid}_{season_id(y)}.json"
                if not p.exists():
                    _write(p, {"gameLog": []})
            _write(cache / f"nhl/landing/{pid}.json", {
                "birthDate": f"{2026 - age}-06-15", "seasonTotals": season_totals,
                "careerTotals": {"regularSeason": car}})
        _write(cache / f"nhl/roster/{t}.json", r)

    for y in years:
        _write(cache / f"mp/skaters_{y}.csv", pd.DataFrame(sk_rows[y]))
        _write(cache / f"mp/goalies_{y}.csv", pd.DataFrame(go_rows[y]))

    # ---------------- team season summaries
    for y in years:
        rows = []
        for t in teams_for_season(y):
            tg = [g for g in games[y] if t in (g["home"], g["away"])]
            gp = len(tg)
            if not gp:
                continue
            gf = sum(g["hs"] if g["home"] == t else g["as"] for g in tg)
            ga = sum(g["as"] if g["home"] == t else g["hs"] for g in tg)
            for sit, share_t, share_g in (("all", 60.5, 1.0), ("5on5", 48.5, 0.72), ("5on4", 5.6, 0.19),
                                          ("4on5", 5.6, 0.0), ("other", 6.3, 0.09)):
                xgf = gf * share_g * rng.normal(1, 0.05) * att[t] / max(att[t], 0.8)
                xga = ga * share_g * rng.normal(1, 0.05)
                if sit == "4on5":
                    xgf, xga = gf * 0.02, ga * 0.19 * rng.normal(1, 0.05)
                rows.append({"team": NHL_TO_MP.get(t, t), "season": y, "name": t, "position": "Team Level",
                             "situation": sit, "games_played": gp, "iceTime": gp * share_t * 60,
                             "xGoalsFor": round(xgf, 2), "xGoalsAgainst": round(xga, 2),
                             "goalsFor": round(gf * share_g) if sit != "4on5" else round(gf * 0.02),
                             "goalsAgainst": round(ga * share_g) if sit != "4on5" else round(ga * 0.19),
                             "shotsOnGoalFor": int(gf * share_g * 9.8), "shotsOnGoalAgainst": int(ga * share_g * 9.8),
                             "highDangerShotsFor": int(gp * share_t / 60 * 10 * att[t]),
                             "highDangerShotsAgainst": int(gp * share_t / 60 * 10 / dfn[t]),
                             "highDangerxGoalsFor": round(xgf * 0.45, 2), "highDangerxGoalsAgainst": round(xga * 0.45, 2)})
        _write(cache / f"mp/teams_{y}.csv", pd.DataFrame(rows))

    # metadata used by `simulate_results`
    _write(cache / "demo_meta.json", {"slate": slate, "date": date})


def simulate_results(cache: Path, seed: int = 11) -> int:
    """Write final boxscores for the demo slate so grading can be tested."""
    rng = np.random.default_rng(seed)
    meta = json.loads((cache / "demo_meta.json").read_text())
    n = 0
    for g in meta["slate"]:
        box = {"id": g["id"], "gameState": "OFF", "homeTeam": {"abbrev": g["home"]}, "awayTeam": {"abbrev": g["away"]},
               "playerByGameStats": {}}
        totals = {}
        for side, t in (("homeTeam", g["home"]), ("awayTeam", g["away"])):
            r = json.loads((cache / f"nhl/roster/{t}.json").read_text())
            sk = lambda grp: [{"playerId": p["id"], "goals": int(rng.poisson(0.28 if grp == "forwards" else 0.1)),
                               "assists": int(rng.poisson(0.45)), "sog": int(rng.poisson(2.2 if grp == "forwards" else 1.4))}
                              for p in r[grp]]
            fw, de = sk("forwards"), sk("defensemen")
            for p in fw + de:
                p["points"] = p["goals"] + p["assists"]
            box["playerByGameStats"][side] = {"forwards": fw, "defense": de, "goalies": []}
            totals[side] = sum(p["goals"] for p in fw + de)
        box["homeTeam"]["score"] = totals["homeTeam"]
        box["awayTeam"]["score"] = totals["awayTeam"]
        _write(cache / f"nhl/box/{g['id']}.json", box)
        n += 1
    return n
