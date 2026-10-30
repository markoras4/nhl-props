"""SHL data collector (stdlib only). Runs in the private GitHub workflow; incremental.

Stores (gzip JSON) under data/shl/:
  seasons.json                      season code -> uuid, regular-season game type
  sched_<season>.json.gz            full schedule per season
  games/<season>/<uuid>.json.gz     {"pbp": [...], "box": {...}, "team": {...}} for finished games
  rosters.json.gz                   current rosters (athletes by team)
  summary_players.json.gz, summary_goalies.json.gz   current-season summaries
  odds/<YYYY-MM-DDTHHMM>.json.gz    Unibet (Kambi) SHL offers, and odds/latest.json.gz
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(os.environ.get("SHL_DATA", "data/shl"))
SHL = "https://www.shl.se/api"
SERIES = "qQ9-bb0bzEWUk"
REG = "qQ9-af37Ti40B"           # regular season game type
KAMBI = "https://eu-offering-api.kambicdn.com/offering/v2018/ubse"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
     "Accept": "application/json", "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"}
FIRST_SEASON = 2023             # play-by-play and box scores exist from 2023/24


def get(url, tries=3, timeout=40):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=timeout) as r:
                b = r.read()
                return json.loads(b) if b else None
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                print("  fail", url[:120], str(e)[:80])
                return None
            time.sleep(1.5 * (i + 1))


def wgz(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    tmp.replace(path)


def rgz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def seasons():
    js = get(f"{SHL}/sports-v2/season-series-game-types-filter?series={SERIES}") or {}
    out = {}
    for s in js.get("season", []):
        try:
            code = int(s["code"])
        except (KeyError, ValueError):
            continue
        if code >= FIRST_SEASON:
            out[code] = s["uuid"]
    (ROOT).mkdir(parents=True, exist_ok=True)
    (ROOT / "seasons.json").write_text(json.dumps({"seasons": out, "regular": REG}, indent=1))
    return out


def schedule(code, uuid):
    js = get(f"{SHL}/sports-v2/game-schedule?seasonUuid={uuid}&seriesUuid={SERIES}&gameTypeUuid={REG}&gamePlace=all&played=all")
    if js and js.get("gameInfo"):
        wgz(ROOT / f"sched_{code}.json.gz", js)
        return js
    p = ROOT / f"sched_{code}.json.gz"
    return rgz(p) if p.exists() else {"gameInfo": []}


def game(code, g):
    p = ROOT / "games" / str(code) / f"{g['uuid']}.json.gz"
    if p.exists():
        return False
    pbp = get(f"{SHL}/gameday/play-by-play/{g['uuid']}")
    box = get(f"{SHL}/gameday/player-stats/{g['uuid']}")
    team = get(f"{SHL}/gameday/team-stats/{g['uuid']}")
    if not box or not pbp:
        return False
    wgz(p, {"uuid": g["uuid"], "pbp": pbp, "box": box, "team": team})
    return True


def rosters(sched):
    teams = {}
    for g in sched.get("gameInfo", []):
        for side in ("homeTeamInfo", "awayTeamInfo"):
            t = g.get(side) or {}
            if t.get("uuid"):
                teams[t["uuid"]] = t.get("code")
    out = {}
    def one(tu):
        return tu, get(f"{SHL}/sports-v2/athletes/by-team-uuid/{tu}")
    with ThreadPoolExecutor(6) as ex:
        for tu, js in ex.map(one, teams):
            if js:
                out[tu] = {"code": teams[tu], "athletes": js}
    if out:
        wgz(ROOT / "rosters.json.gz", out)


def logos(sched):
    """team logos (SVG) so the page works offline"""
    d = ROOT / "logos"; d.mkdir(parents=True, exist_ok=True)
    for g in sched.get("gameInfo", []):
        for side in ("homeTeamInfo", "awayTeamInfo"):
            t = g.get(side) or {}
            code, url = t.get("code"), t.get("icon")
            if not code or not url or (d / f"{code}.svg").exists():
                continue
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": H["User-Agent"]}), timeout=30) as r:
                    (d / f"{code}.svg").write_bytes(r.read())
            except Exception as e:  # noqa: BLE001
                print("  logo fail", code, str(e)[:60])


def summaries(ssgt):
    for kind, name in (("players_summary", "summary_players"), ("goalkeepers_summary", "summary_goalies")):
        js = get(f"{SHL}/statistics-v2/stats-info/{kind}?count=1000&ssgtUuid={ssgt}&provider=statnet")
        if js:
            wgz(ROOT / f"{name}.json.gz", js)


def odds():
    lst = get(f"{KAMBI}/listView/ice_hockey/sweden/all/all/matches.json?lang=en_GB&market=SE&useCombined=true") or {}
    evs = [e for e in lst.get("events", []) if [p.get("termKey") for p in e["event"].get("path", [])][-1:] == ["shl"]]
    out = {"fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "events": []}
    def one(e):
        return e, get(f"{KAMBI}/betoffer/event/{e['event']['id']}.json?lang=en_GB&market=SE&includeParticipants=true")
    with ThreadPoolExecutor(6) as ex:
        for e, js in ex.map(one, evs):
            if js:
                out["events"].append({"event": e["event"], "offers": js.get("betOffers", [])})
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M")
    wgz(ROOT / "odds" / f"{stamp}.json.gz", out)
    wgz(ROOT / "odds" / "latest.json.gz", out)
    print(f"  odds: {len(out['events'])} SHL games")


def main():
    ss = seasons()
    print("seasons", ss)
    status = {"seasons": ss, "per_season": {}}
    cur = max(ss)
    for code in sorted(ss):
        sch = schedule(code, ss[code])
        done = [g for g in sch.get("gameInfo", []) if g.get("state") == "post-game"]
        with ThreadPoolExecutor(6) as ex:
            new = sum(ex.map(lambda g: game(code, g), done))
        print(f"  {code}: {len(sch.get('gameInfo', []))} games, {len(done)} finished, {new} new")
        status["per_season"][code] = {"games": len(sch.get("gameInfo", [])), "finished": len(done), "new": new,
                                       "stored": len(list((ROOT / "games" / str(code)).glob("*.json.gz"))) if (ROOT / "games" / str(code)).exists() else 0}
        if code == cur:
            rosters(sch)
            logos(sch)
            if sch.get("ssgtUuid"):
                summaries(sch["ssgtUuid"])
    odds()
    status["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    (ROOT / "status.json").write_text(json.dumps(status, indent=1))


if __name__ == "__main__":
    sys.exit(main())
