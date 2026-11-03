"""Stoiximan / Betano prices pushed from the owner's browser (Tampermonkey scripts: tools/stoiximan-sync.user.js, and
tools/betano-sync.user.js for when Stoiximan can't be reached; same sportsbook, English market names).

The script reads Stoiximan's NHL pages every hour (every 15 min close to puck drop, and once ~12 min before each
game), and sends them to the Supabase function stx-sync. Each build reads the newest prices per game, matches
games by start time and player names, matches players by name, and writes data/manual_odds/00-stx-live.json,
which odds.merge_manual uses to fill markets the automatic feed lacks. Because the file sorts first, the newest
Stoiximan prices win over older hand-copied ones, so the Triage ledger sees the closing price move.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
import urllib.request
from pathlib import Path

URL = "https://evxnevqxykbfocqfqxid.supabase.co/functions/v1/stx-sync"
# Stoiximan writes a few names in Greek letters
ALIAS = {"Ράσμους Άντερσον": "Rasmus Andersson", "Κόνορ Μέρφι": "Connor Murphy", "Tim Stuetzle": "Tim Stützle",
         "Ukko-Pekka Luukonen": "Ukko-Pekka Luukkonen"}
LIVE = "00-stx-live.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower())


def _fetch(log=print) -> dict | None:
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "nhl-props-build"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        log(f"  Stoiximan sync unavailable: {e}")
        return None


def convert(feed: dict, games: list[dict], players: dict) -> tuple[dict, list]:
    """feed games -> {game id: {book, fetched, markets, totals, players: {pid: {m: {o, n}}}}}"""
    by_team: dict[str, dict] = {}
    for pid, p in players.items():
        by_team.setdefault(p.get("team"), {}).setdefault(_norm(p.get("name", "")), []).append(str(pid))
    out, miss = {}, []
    for fg in feed.get("games") or []:
        start = fg.get("start")
        names = [_norm(n) for n in (fg.get("players") or {})]
        best, score = None, 0
        for g in games:
            try:
                t = dt.datetime.fromisoformat(g["start"].replace("Z", "+00:00")).timestamp() * 1000
            except Exception:  # noqa: BLE001
                continue
            if start and abs(t - start) > 20 * 60 * 1000:
                continue
            pool = {**by_team.get(g["home"], {}), **by_team.get(g["away"], {})}
            sc = sum(1 for n in names if n in pool) + (1 if start else 0)
            if sc > score:
                best, score = g, sc
        if not best:
            continue
        pool: dict[str, list] = {}
        for t in (best["home"], best["away"]):
            for k, v in by_team.get(t, {}).items():
                pool.setdefault(k, []).extend(v)
        rec = {"book": "Betano" if str(fg.get("key", "")).startswith("/game-odds/") else "Stoiximan", "fetched": fg.get("fetched"), "markets": fg.get("markets") or {},
               "totals": fg.get("totals") or {}, "players": {}}
        for name, mk in (fg.get("players") or {}).items():
            name = ALIAS.get(name, name)
            k = _norm(name)
            pids = pool.get(k)
            if not pids:
                last = _norm(name.split()[-1]) if name.split() else ""
                pids = [pid for n2, l in pool.items() for pid in l if last and n2.endswith(last) and n2[:1] == k[:1]]
            if len(pids or []) != 1:
                miss.append(name)
                continue
            d = {}
            for m, v in (mk or {}).items():
                o, _, n = str(v).partition("/")
                try:
                    d[m] = {"o": float(o), "n": float(n) if n else None}
                except ValueError:
                    continue
            # sanity: 2+ / 3+ goal prices must be longer than the shorter market
            if "g2" in d and "g" in d and d["g2"]["o"] <= d["g"]["o"]:
                d.pop("g2")
            if "g3" in d and "g2" in d and d["g3"]["o"] <= d["g2"]["o"]:
                d.pop("g3")
            if d:
                rec["players"][pids[0]] = d
        gid = str(best["id"])
        if gid in out:   # the same game from a second site (Stoiximan and Betano): the newer push keeps its prices, the older fills gaps
            old = out[gid]
            for k, v in rec["markets"].items():
                old["markets"].setdefault(k, v)
            for k, v in rec["totals"].items():
                old["totals"].setdefault(k, v)
            for pid, mk in rec["players"].items():
                cur = old["players"].setdefault(pid, {})
                for m, v in mk.items():
                    cur.setdefault(m, v)
        else:
            out[gid] = rec
    return out, miss


AWARDS_FILE = "awards/stx.json"
AWARD_KEYS = (("hart", "hart"), ("norris", "norris"), ("rocket", "rocket"), ("richard", "rocket"), ("vezina", "vezina"),
              ("calder", "calder"), ("adams", "adams"), ("art ross", "artross"), ("selke", "selke"), ("ted lindsay", "lindsay"))


def _save_awards(data_dir: Path, feed: dict, log=print) -> None:
    """keep the newest award prices on disk, so builds without a fresh push still show them"""
    aw = feed.get("awards") if feed else None
    if not aw:
        return
    f = Path(data_dir) / AWARDS_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"fetched": feed.get("awards_fetched"), "markets": aw}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"  Stoiximan awards: {', '.join(f'{k} {len(v)}' for k, v in aw.items())}")


def awards(data_dir: Path, players: dict, goalies: dict | None, log=print) -> dict | None:
    """saved award prices -> {fetched, book, m: {key: [[name, odds, pid or None, fair chance], ...]}}
    fair chance = 1/odds scaled so the market adds up to 100% (outright books carry a large margin)"""
    f = Path(data_dir) / AWARDS_FILE
    if not f.exists():
        return None
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log(f"  awards file unreadable: {e}")
        return None
    idx: dict[str, list] = {}
    for pool in (players or {}, goalies or {}):
        for pid, p in pool.items():
            idx.setdefault(_norm(p.get("name", "")), []).append(str(pid))
    def games(pid):   # two players with one name (Elias Pettersson): the award candidate is the established one
        p = (players or {}).get(pid) or (players or {}).get(int(pid)) if str(pid).isdigit() else None
        return (((p or {}).get("windows") or {}).get("career") or {}).get("gp") or 0
    for k, l in idx.items():
        if len(l) > 1:
            l.sort(key=games, reverse=True)
            if games(l[0]) > 3 * max(1, games(l[1])):
                idx[k] = l[:1]
    out, miss = {}, []
    for name, rows in (raw.get("markets") or {}).items():
        key = next((k for w, k in AWARD_KEYS if w in name.lower()), None)
        if not key or not rows:
            continue
        tot = sum(1 / o for _, o in rows if o and o > 1) or 1
        lst = []
        for nm, o in rows:
            nm2 = ALIAS.get(nm, nm)
            ids = idx.get(_norm(nm2)) if key != "adams" else None
            if key != "adams" and (not ids or len(ids) != 1):
                last, first = _norm(nm2.split()[-1]) if nm2.split() else "", _norm(nm2)[:1]
                ids = [i for n2, l in idx.items() for i in l if last and n2.endswith(last) and n2[:1] == first] or ids
                if not ids or len(ids) != 1:
                    miss.append(nm2)
            lst.append([nm2, o, ids[0] if ids and len(ids) == 1 else None, round(1 / o / tot, 4)])
        out[key] = lst
    if miss:
        log(f"  awards: no player match for {', '.join(sorted(set(miss))[:10])}")
    return {"fetched": raw.get("fetched"), "book": "Stoiximan", "m": out} if out else None


def pull(data_dir: Path, games: list[dict], players: dict, log=print) -> int:
    feed = _fetch(log)
    _save_awards(data_dir, feed, log)
    if not feed or not feed.get("games"):
        return 0
    out, miss = convert(feed, games, players)
    d = Path(data_dir) / "manual_odds"
    d.mkdir(parents=True, exist_ok=True)
    (d / LIVE).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    n = sum(len(m) for r in out.values() for m in r["players"].values())
    log(f"  Stoiximan sync: {len(out)} games, {n} player prices (last push {feed.get('last')}, {feed.get('last_kind')})"
        + (f"; unmatched: {', '.join(sorted(set(miss))[:8])}" if miss else ""))
    return len(out)
