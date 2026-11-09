"""Bookmaker odds from Unibet (Kambi offering feed).

Markets: anytime goal, 1+ assist, 1+ point, shots-on-goal lines, game total goals.
Odds come as integers x1000 (2620 -> 2.62).
"""
from __future__ import annotations

import datetime as dt

from .lock import too_late
from .news import NameIndex, team_from_name

BASE = "https://eu-offering-api.kambicdn.com/offering/v2018/ubse"
LIST_URL = BASE + "/listView/ice_hockey/nhl/all/all/matches.json?lang=en_GB&market=SE&useCombined=true"
EVENT_URL = BASE + "/betoffer/event/{eid}.json?lang=en_GB&market=SE&includeParticipants=true"

PLAYER_MARKETS = {
    "to score - including overtime": "g",
    "to score at least 2 goals": "g2",
    "to score at least 3 goals": "g3",
    "player to record at least 1 assists": "a",
    "player to record at least 2 assists": "a2",
    "player to score at least 1 points": "pt",
    "player to score at least 2 points": "pt2",
    "player to score at least 3 points": "pt3",
    "player to score at least 1 power play points": "ppp",
}


def _odds(o):
    v = o.get("odds")
    return round(v / 1000, 2) if isinstance(v, (int, float)) and v > 0 else None


def parse_event(js: dict | None) -> dict:
    """-> {'players': [(name, market, yes, no)], 'totals': {line: {'over','under'}}}"""
    players, totals, winner = [], {}, []
    for bo in (js or {}).get("betOffers", []) or []:
        lab = ((bo.get("criterion") or {}).get("englishLabel") or "").lower().strip()
        outs = bo.get("outcomes") or []
        if lab.startswith("moneyline - including overtime"):
            winner = [(o.get("participant") or o.get("englishLabel") or "", _odds(o), o.get("type")) for o in outs]
            continue
        if lab.startswith("total goals - including overtime and penalty shootout"):
            for o in outs:
                line = (o.get("line") or 0) / 1000
                side = "over" if o.get("type") == "OT_OVER" else "under" if o.get("type") == "OT_UNDER" else None
                if side and line:
                    totals.setdefault(str(line), {})[side] = _odds(o)
            continue
        lab = " ".join(lab.split())
        mk = next((m for k, m in PLAYER_MARKETS.items() if lab.startswith(k)), None)
        if lab.startswith("player's shots on goal - including overtime"):
            by = {}
            for o in outs:
                if o.get("participant"):
                    key = (o["participant"], (o.get("line") or 0) / 1000)
                    by.setdefault(key, {})["yes" if o.get("type") == "OT_OVER" else "no"] = _odds(o)
            for (name, line), v in by.items():
                if line:
                    players.append((name, f"s{line}", v.get("yes"), v.get("no")))
            continue
        if mk:
            by = {}
            for o in outs:
                if o.get("participant"):
                    by.setdefault(o["participant"], {})["yes" if o.get("type") in ("OT_YES", "OT_OVER") else "no"] = _odds(o)
            for name, v in by.items():
                players.append((name, mk, v.get("yes"), v.get("no")))
    return {"players": players, "totals": totals, "winner": winner}


def fetch_odds(src, games: list[dict], players: list[dict], log=print) -> dict:
    """-> {game_id: {'book': 'Unibet', 'players': {pid: {market: {'o': yes, 'n': no}}}, 'totals': {...}}}"""
    lst = src.json(LIST_URL, "odds/kambi_list.json", 0.25)
    events = [e.get("event") or {} for e in (lst or {}).get("events", [])]
    idx = NameIndex(players)
    by_key = {}
    for g in games:
        by_key[(g["home"], g["away"], g["date"])] = g
    out, n_p = {}, 0
    for ev in events:
        home = team_from_name(ev.get("homeName", ""))
        away = team_from_name(ev.get("awayName", ""))
        start = ev.get("start") or ""
        if str(ev.get("state") or "").upper() == "STARTED" or too_late(start):
            continue      # live in-game prices or inside the lock window: never used
        try:  # Kambi gives UTC; NHL dates are North American
            d_et = (dt.datetime.fromisoformat(start.replace("Z", "+00:00")) - dt.timedelta(hours=5)).date().isoformat()
        except ValueError:
            continue
        g = by_key.get((home, away, d_et))
        if not g:
            continue
        js = src.json(EVENT_URL.format(eid=ev["id"]), f"odds/kambi_{ev['id']}.json", 0.25)
        parsed = parse_event(js)
        po = {}
        for name, mk, yes, no in parsed["players"]:
            m = idx.find(name, home) or idx.find(name, away)
            if m and yes:
                po.setdefault(m["id"], {})[mk] = {"o": yes, "n": no}
        n_p += len(po)
        win = {}
        for name, od, typ in parsed["winner"]:
            tm = team_from_name(name)
            side = "home" if tm == home else "away" if tm == away else ("home" if typ == "OT_ONE" else "away")
            if od:
                win[side] = od
        out[g["id"]] = {"book": "Bookmaker", "players": po, "totals": parsed["totals"], "winner": win,
                        "fetched": dt.datetime.utcnow().isoformat(timespec="minutes") + "Z"}
    log(f"Odds: bookmaker prices for {len(out)} games, {n_p} players")
    return out


# ------------------------------------------------------------ second price: Pinnacle's public guest API
# Pinnacle is the sharpest sportsbook, so its price is the best market reference. Its NHL player props
# (goals, assists, points, shots on goal, as over/under lines) fill markets the main feed lacks and give a
# second price where both have one. Prices are American; we keep decimal odds for over and under.
PIN = "https://guest.api.arcadia.pinnacle.com/0.1/leagues/1456"
PIN_STAT = {"goals": "g", "assists": "a", "points": "pt", "shots on goal": "s"}
PIN_LINE = {("g", 0.5): "g", ("g", 1.5): "g2", ("g", 2.5): "g3", ("a", 0.5): "a", ("a", 1.5): "a2",
            ("pt", 0.5): "pt", ("pt", 1.5): "pt2", ("pt", 2.5): "pt3"}


def _am(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if p >= 100:
        return round(1 + p / 100, 3)
    if p <= -100:
        return round(1 + 100 / -p, 3)
    return None


def fetch_pinnacle(src, games: list[dict], players: list[dict], log=print) -> dict:
    """-> {game_id: {pid: {market: {'o': over, 'n': under}}}}"""
    import re
    mus = src.json(PIN + "/matchups", "odds/pin_matchups.json", 0.25) or []
    prices = src.json(PIN + "/markets/straight", "odds/pin_straight.json", 0.25) or []
    price_by = {}
    for mk in prices:
        if mk.get("type") == "total" and mk.get("period") == 0 and not mk.get("isAlternate"):
            price_by[mk.get("matchupId")] = mk
    idx = NameIndex(players)
    by_key = {(g["home"], g["away"], g["date"]): g for g in games}
    out, n = {}, 0
    for m in mus:
        sp = m.get("special") or {}
        if m.get("type") != "special" or sp.get("category") != "Player Props":
            continue
        rx = re.match(r"^(.*?) total (goals|assists|points|shots on goal)\b", str(sp.get("description") or ""), re.I)
        par = m.get("parent") or {}
        if not rx or not par or m.get("isLive") or par.get("isLive") or too_late(par.get("startTime")):
            continue
        teams = {p.get("alignment"): team_from_name(p.get("name") or "") for p in par.get("participants") or []}
        try:
            d_et = (dt.datetime.fromisoformat(str(par.get("startTime")).replace("Z", "+00:00")) - dt.timedelta(hours=5)).date().isoformat()
        except ValueError:
            continue
        g = by_key.get((teams.get("home"), teams.get("away"), d_et))
        mk = price_by.get(m.get("id"))
        if not g or not mk:
            continue
        name, stat = rx.group(1).strip(), PIN_STAT[rx.group(2).lower()]
        pl = idx.find(name, g["home"]) or idx.find(name, g["away"])
        if not pl:
            continue
        side = {p.get("id"): str(p.get("name") or "").lower() for p in m.get("participants") or []}
        ov = un = line = None
        for pr in mk.get("prices") or []:
            nm = side.get(pr.get("participantId"), "")
            line = pr.get("points", line)
            if nm.startswith("over"):
                ov = _am(pr.get("price"))
            elif nm.startswith("under"):
                un = _am(pr.get("price"))
        if not ov or line is None:
            continue
        key = f"s{float(line)}" if stat == "s" else PIN_LINE.get((stat, float(line)))
        if not key:
            continue
        out.setdefault(g["id"], {}).setdefault(pl["id"], {})[key] = {"o": ov, "n": un}
        n += 1
    log(f"Odds: sharp-book prices for {len(out)} games, {n} player markets")
    return out


def pinnacle_rows(session=None, log=print) -> list[dict]:
    """Pinnacle player props for upcoming NHL games as plain rows (no player matching), for the hourly sharp-price sync
    (python -m nhlprops sharp -> Supabase pin-sync -> table pin_odds, read by Pro accounts in the app)."""
    import re
    import requests
    from .sources import HEADERS
    ses = session or requests.Session()
    get = lambda path: ses.get(PIN + path, headers=HEADERS, timeout=30)
    r1, r2 = get("/matchups"), get("/markets/straight")
    if r1.status_code != 200 or r2.status_code != 200:
        log(f"Pinnacle: HTTP {r1.status_code} / {r2.status_code}")
        return []
    price_by = {mk.get("matchupId"): mk for mk in r2.json() or []
                if mk.get("type") == "total" and mk.get("period") == 0 and not mk.get("isAlternate")}
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for m in r1.json() or []:
        sp, par = m.get("special") or {}, m.get("parent") or {}
        if m.get("type") != "special" or sp.get("category") != "Player Props" or m.get("isLive") or par.get("isLive"):
            continue
        rx = re.match(r"^(.*?) total (goals|assists|points|shots on goal)", str(sp.get("description") or ""), re.I)
        try:
            start = dt.datetime.fromisoformat(str(par.get("startTime")).replace("Z", "+00:00"))
        except ValueError:
            continue
        mk = price_by.get(m.get("id"))
        if not rx or not mk or start <= now:
            continue
        side = {x.get("id"): str(x.get("name") or "").lower() for x in m.get("participants") or []}
        ov = un = line = None
        for pr in mk.get("prices") or []:
            nm = side.get(pr.get("participantId"), "")
            line = pr.get("points", line)
            if nm.startswith("over"):
                ov = _am(pr.get("price"))
            elif nm.startswith("under"):
                un = _am(pr.get("price"))
        if not ov or line is None:
            continue
        stat = PIN_STAT[rx.group(2).lower()]
        key = f"s{float(line)}" if stat == "s" else PIN_LINE.get((stat, float(line)))
        teams = {x.get("alignment"): x.get("name") for x in par.get("participants") or []}
        if not key or not teams.get("home") or not teams.get("away"):
            continue
        rows.append({"start_time": start.isoformat(), "home_name": teams["home"], "away_name": teams["away"],
                     "player_name": rx.group(1).strip(), "market": key, "over": ov, "under": un})
    log(f"Pinnacle: {len(rows)} player prices in {len({(r['start_time'], r['home_name']) for r in rows})} games")
    return rows


def merge_second(main: dict, pin: dict) -> None:
    """Add the sharp book's price to each player market ('p' over, 'pn' under) and fill markets the main feed lacks
    (marked b='p')."""
    for gid, pl in pin.items():
        o = main.setdefault(gid, {"book": "Bookmaker", "players": {}, "totals": {}, "winner": {}, "fetched": None})
        po = o.setdefault("players", {})
        for pid, mk in pl.items():
            cur = po.setdefault(pid, {})
            for m, v in mk.items():
                if m in cur and cur[m].get("b") != "p":
                    cur[m]["p"], cur[m]["pn"] = v["o"], v.get("n")
                else:
                    cur[m] = {"o": v["o"], "n": v.get("n"), "b": "p"}


def merge_manual(main: dict, data_dir, games: list[dict], log=print) -> None:
    """Odds copied by hand from another sportsbook (data/manual_odds/<date>.json, keyed by game id): fill player markets,
    totals and game markets the feeds lack (marked b='s'); existing feed prices are kept."""
    import json
    from pathlib import Path
    ids = {str(g["id"]) for g in games}
    n = 0
    for f in sorted(Path(data_dir, "manual_odds").glob("*.json")):
        try:
            man = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log(f"  manual odds {f.name} unreadable: {e}")
            continue
        for gid, rec in man.items():
            if gid not in ids:
                continue
            key = int(gid) if any(isinstance(k, int) for k in main) else gid
            o = main.setdefault(key, {"book": "Bookmaker", "players": {}, "totals": {}, "winner": {}, "fetched": None})
            if not o.get("fetched"):
                o["fetched"] = rec.get("fetched")
            o.setdefault("markets", {}).update({k: v for k, v in (rec.get("markets") or {}).items() if k not in o.get("markets", {})})
            tot = o.setdefault("totals", {})
            for L, v in (rec.get("totals") or {}).items():
                tot.setdefault(L, v)
            po = o.setdefault("players", {})
            for pid, mk in (rec.get("players") or {}).items():
                pkey = int(pid) if any(isinstance(k, int) for k in po) else pid
                cur = po.setdefault(pkey, {})
                for m, v in mk.items():
                    if m not in cur:
                        cur[m] = {"o": v["o"], "n": v.get("n"), "b": "s"}
                        n += 1
    if n:
        log(f"  manual odds: {n} player prices added")


FUT_URL = BASE + "/listView/ice_hockey/nhl/all/all/competitions.json?lang=en_GB&market=SE&useCombined=true"


def fetch_futures(src, log=print) -> dict:
    """Season markets: Stanley Cup winner, conference winner (= reaches the final), division winner,
    and per team: to reach the playoffs (yes/no) and regular-season points over/under."""
    lst = src.json(FUT_URL, "odds/kambi_futures.json", 3)
    out = {"cup": {}, "final": {}, "div": {}, "playoffs": {}, "points": {},
           "fetched": dt.datetime.utcnow().isoformat(timespec="minutes") + "Z"}
    for e in (lst or {}).get("events", []):
        ev = e.get("event") or {}
        name = str(ev.get("name") or "")
        labels = " ".join(str((b.get("criterion") or {}).get("label") or "") for b in e.get("betOffers") or [])
        kind = ("cup" if "Winner - Including Playoffs" in labels else "final" if "Conference Winner" in labels
                else "div" if "Division Winner" in labels else "team" if " Markets" in name else None)
        if not kind:
            continue
        js = src.json(EVENT_URL.format(eid=ev["id"]), f"odds/kambi_fut_{ev['id']}.json", 3) or {}
        for b in js.get("betOffers") or []:
            lab = str((b.get("criterion") or {}).get("label") or "")
            oc = b.get("outcomes") or []
            if kind in ("cup", "final", "div"):
                for o in oc:
                    tm = team_from_name(o.get("participant") or o.get("label") or "")
                    od = _odds(o)
                    if tm and od:
                        out[kind][tm] = od
            else:
                tm = team_from_name(name.split(" Markets")[0].strip())
                if not tm:
                    continue
                if lab.startswith("To reach the Playoffs"):
                    yn = {str(o.get("label")).lower(): _odds(o) for o in oc}
                    out["playoffs"][tm] = {"yes": yn.get("yes"), "no": yn.get("no")}
                elif lab.startswith("Team Total Points"):
                    ou = {str(o.get("label")).lower(): (_odds(o), o.get("line")) for o in oc}
                    if "over" in ou and ou["over"][1]:
                        out["points"][tm] = {"line": ou["over"][1] / 1000, "over": ou["over"][0], "under": (ou.get("under") or (None,))[0]}
    log(f"Futures odds: Cup {len(out['cup'])}, conference {len(out['final'])}, division {len(out['div'])}, "
        f"playoffs {len(out['playoffs'])}, points {len(out['points'])}")
    return out


# ------------------------------------------------------------ public betting: share of tickets and money per side
# Action Network's consensus (book 15) of the bets it tracks: moneyline, total and puck line, with the number of bets.
AN = "https://api.actionnetwork.com/web/v2/scoreboard/nhl?bookIds=15&date={d}&periods=event"


def fetch_public(src, games: list[dict], log=print) -> dict:
    """-> {game_id: {'n': bets tracked, 'ml': {'home': [tickets%, money%], 'away': [...]},
                     'tot': {'line': 6.5, 'over': [...], 'under': [...]}, 'pl': {'home': {'line', 't', 'm'}, 'away': {...}}}}"""
    by_key = {(g["home"], g["away"], g["date"]): g for g in games}
    out = {}
    for d in sorted({g["date"] for g in games})[:3]:
        js = src.json(AN.format(d=d.replace("-", "")), f"odds/an_{d}.json", 0.25) or {}
        for ev in js.get("games") or []:
            names = {t.get("id"): (t.get("full_name") or t.get("display_name") or "") for t in ev.get("teams") or []}
            home, away = team_from_name(names.get(ev.get("home_team_id"), "")), team_from_name(names.get(ev.get("away_team_id"), ""))
            g = by_key.get((home, away, d))
            if not g or too_late(ev.get("start_time")) or str(ev.get("status") or "") not in ("scheduled", ""):
                continue
            mk = ((ev.get("markets") or {}).get("15") or {}).get("event") or {}
            pct = lambda o: [int(((o.get("bet_info") or {}).get(k) or {}).get("percent") or 0) for k in ("tickets", "money")]
            rec = {"n": ev.get("num_bets")}
            ml = {o.get("side"): pct(o) for o in mk.get("moneyline") or [] if o.get("side") in ("home", "away")}
            if sum(sum(v) for v in ml.values()):
                rec["ml"] = ml
            tot = {o.get("side"): o for o in mk.get("total") or [] if o.get("side") in ("over", "under")}
            if tot and sum(sum(pct(o)) for o in tot.values()):
                rec["tot"] = {"line": (tot.get("over") or tot.get("under")).get("value"), **{k: pct(o) for k, o in tot.items()}}
            pl = {o.get("side"): o for o in mk.get("spread") or [] if o.get("side") in ("home", "away")}
            if pl and sum(sum(pct(o)) for o in pl.values()):
                rec["pl"] = {k: {"line": o.get("value"), "t": pct(o)[0], "m": pct(o)[1]} for k, o in pl.items()}
            if len(rec) > 1:
                out[g["id"]] = rec
    log(f"Public betting: splits for {len(out)} games")
    return out
