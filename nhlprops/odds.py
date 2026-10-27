"""Bookmaker odds from Unibet (Kambi offering feed).

Markets: anytime goal, 1+ assist, 1+ point, shots-on-goal lines, game total goals.
Odds come as integers x1000 (2620 -> 2.62).
"""
from __future__ import annotations

import datetime as dt

from .news import NameIndex, team_from_name

BASE = "https://eu-offering-api.kambicdn.com/offering/v2018/ubse"
LIST_URL = BASE + "/listView/ice_hockey/nhl/all/all/matches.json?lang=en_GB&market=SE&useCombined=true"
EVENT_URL = BASE + "/betoffer/event/{eid}.json?lang=en_GB&market=SE&includeParticipants=true"

PLAYER_MARKETS = {
    "to score - including overtime": "g",
    "player to record at least 1 assists": "a",
    "player to score at least 1 points": "pt",
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
                    by.setdefault(o["participant"], {})["yes" if o.get("type") == "OT_YES" else "no"] = _odds(o)
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
