"""Stoiximan prices pushed from the owner's browser (Tampermonkey script, see tools/stoiximan-sync.user.js).

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
        rec = {"book": "Stoiximan", "fetched": fg.get("fetched"), "markets": fg.get("markets") or {},
               "totals": fg.get("totals") or {}, "players": {}}
        for name, mk in (fg.get("players") or {}).items():
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
        out[str(best["id"])] = rec
    return out, miss


def pull(data_dir: Path, games: list[dict], players: dict, log=print) -> int:
    feed = _fetch(log)
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
