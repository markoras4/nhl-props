"""Best picks record: the two picks per game the site shows (the "All" tab, and the Multi-point tab kept as a
separate long-shots record), as they stood at the lock 5 minutes before puck drop, graded after the game.

The selection mirrors bestPicks() in the template: positive EV at the sportsbook price, one pick per player, the
more likely option in the same family when the edge is within 3 points, ranked by EV x trust in the player's NHL
history, top two. Every build refreshes the picks of games that aren't locked yet, so the last refresh before the
lock is the official pick. The first price each pick was seen at is kept, for closing line value (CLV): how much
the price moved after the pick appeared (positive = the market moved toward the pick).

Stored in data/bestpicks/<date>.json; graded rows written to output/bestpicks.json for the Track record page.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .lock import is_locked

GROUPS = {"pts": ["g", "a", "pt", "fg"], "pp": ["ppp"], "s": ["s1.5", "s2.5", "s3.5", "s4.5"],
          "h": ["h0.5", "h1.5", "h2.5", "h3.5"], "b": ["b0.5", "b1.5", "b2.5"], "pim": ["pim0.5", "pim2.5"]}
MULTI = ["pt2", "pt3", "g2", "g3", "a2"]
# the official picks ("All" tab, the tracked record): goals, assists, points, power-play points and shots on goal.
# First goal, hits, blocks and penalties can still be browsed in their own tabs on the game page but aren't tracked.
ALL = ["g", "a", "pt", "ppp"] + GROUPS["s"]


def _family(m: str) -> str:
    if m in GROUPS["pts"] or m in MULTI:
        return "pts"
    for k, ms in GROUPS.items():
        if m in ms:
            return k
    return m


def choose(g: dict, odds: dict, players: dict, markets: list[str], n: int = 2) -> list[dict]:
    """The top `n` picks for one game from `markets`, as the site picks them (sportsbook prices only)."""
    po = (odds or {}).get("players") or {}
    cands = []
    for pr in g.get("props") or []:
        pl = players.get(str(pr["id"])) or {}
        if not pl.get("active"):
            continue
        prices = po.get(pr["id"]) or po.get(str(pr["id"])) or {}
        ok = []
        for m in markets:
            p = (pr.get("p") or {}).get(m)
            o = (prices.get(m) or {}).get("o")
            if not p or not o:
                continue
            ev = p * o - 1
            if ev > 0:
                ok.append({"m": m, "p": p, "o": o, "ev": ev})
        if not ok:
            continue
        ok.sort(key=lambda x: -x["ev"])
        pick = ok[0]
        safer = sorted([x for x in ok if _family(x["m"]) == _family(pick["m"]) and x["p"] > pick["p"]
                        and x["ev"] >= pick["ev"] - 0.03], key=lambda x: -x["p"])
        if safer:
            pick = safer[0]
        gp = ((pl.get("windows") or {}).get("career") or {}).get("gp") or 0
        trust = min(1.0, (gp + 10) / 60)
        cands.append({**pick, "pid": pr["id"], "name": pl.get("name"), "team": pr["team"], "opp": pr["opp"],
                      "rank": pick["ev"] * trust})
    cands.sort(key=lambda x: -x["rank"])
    return cands[:n]


def snapshot(slate: dict, bp_dir: Path, now: dt.datetime | None = None) -> None:
    """Refresh the picks of every game that isn't locked; locked games keep their last picks."""
    bp_dir.mkdir(parents=True, exist_ok=True)
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec="minutes")
    odds_all = slate.get("odds") or {}
    by_date: dict[str, list] = {}
    for g in slate["games"]:
        if not g.get("frozen") and not is_locked(g, now):
            by_date.setdefault(g["date"], []).append(g)
    for d, games in by_date.items():
        f = bp_dir / f"{d}.json"
        store = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        for g in games:
            gid = str(g["id"])
            odds = odds_all.get(g["id"]) or odds_all.get(gid)
            if not odds or not odds.get("players"):
                continue
            rec = store.get(gid) or {"seen": {}}
            seen = rec.get("seen") or {}
            out = {}
            for kind, mk in (("all", ALL), ("multi", MULTI)):
                picks = choose(g, odds, slate["players"], mk)
                for p in picks:
                    key = f"{p['pid']}|{p['m']}"
                    seen.setdefault(key, {"o": p["o"], "at": stamp})
                out[kind] = [{"pid": p["pid"], "name": p["name"], "team": p["team"], "opp": p["opp"], "m": p["m"],
                              "p": round(p["p"], 4), "o": p["o"], "ev": round(p["ev"], 4)} for p in picks]
            store[gid] = {"home": g["home"], "away": g["away"], "start": g.get("start"), "at": stamp,
                          "picks": out, "seen": seen}
        f.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")


def _result(m: str, st: dict, fg, ppp) -> bool | None:
    G, A = st["g"], st["a"]
    fixed = {"g": G >= 1, "a": A >= 1, "pt": G + A >= 1, "g2": G >= 2, "g3": G >= 3, "a2": A >= 2,
             "pt2": G + A >= 2, "pt3": G + A >= 3}
    if m in fixed:
        return fixed[m]
    if m == "fg":
        return None if fg == "?" else fg == st["id"]
    if m == "ppp":
        return None if ppp is None else ppp >= 1
    if m.startswith("pim"):
        return st.get("pim", 0) > float(m[3:])
    if m[0] in "shb":
        return st.get(m[0], 0) > float(m[1:])
    return None


def _pp_points(src, gid: int) -> dict | None:
    """player id -> power-play points in a final game, from the NHL game summary (fallback when play-by-play is
    missing); None if the summary isn't available or the game isn't final"""
    js = src.json(f"https://api-web.nhle.com/v1/gamecenter/{gid}/landing", f"nhl/landing_final/{gid}.json", 24) or {}
    if js.get("gameState") not in ("OFF", "FINAL"):
        return None
    out: dict = {}
    for per in (js.get("summary") or {}).get("scoring") or []:
        if (per.get("periodDescriptor") or {}).get("periodType") == "SO":
            continue
        for g in per.get("goals") or []:
            if str(g.get("strength", "")).lower() == "pp":
                for pid in [g.get("playerId")] + [a.get("playerId") for a in g.get("assists") or []]:
                    if pid:
                        out[int(pid)] = out.get(int(pid), 0) + 1
    return out


def grade(src, bp_dir: Path, out_path: Path, log=print) -> int:
    from .grade import _box_players, _first_scorers
    pbp = {}
    for fp in sorted(Path("data/shots").glob("*.json")) if Path("data/shots").exists() else []:
        try:
            pbp.update(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            pass
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for f in sorted(bp_dir.glob("20*.json")):
        d = f.stem
        store = json.loads(f.read_text(encoding="utf-8"))
        fgs = None
        for gid, rec in store.items():
            start = rec.get("start")
            try:
                locked = start and now >= dt.datetime.fromisoformat(start.replace("Z", "+00:00")) - dt.timedelta(minutes=5)
            except ValueError:
                locked = False
            if not locked:
                continue      # still open: the live picks are on the game page (Pro), not in the public record
            box = src.boxscore(int(gid))
            final = bool(box) and box.get("gameState") in ("OFF", "FINAL")
            stats = _box_players(box) if final else {}
            if final and fgs is None:
                fgs = _first_scorers(src, d)
            gp_ = pbp.get(str(gid)) or {}
            for kind in ("all", "multi"):
                for rk, p in enumerate((rec.get("picks") or {}).get(kind) or [], 1):
                    first = ((rec.get("seen") or {}).get(f"{p['pid']}|{p['m']}") or {}).get("o") or p["o"]
                    row = {"d": d, "g": int(gid), "h": rec["home"], "a": rec["away"], "k": kind, "rk": rk,
                           "pid": p["pid"], "n": p["name"], "t": p["team"], "o": p["opp"], "m": p["m"],
                           "od": p["o"], "p": p["p"], "ev": p["ev"], "f": first,
                           "clv": round(first / p["o"] - 1, 4) if p["o"] else 0.0}
                    if not final:
                        row["r"] = "p"            # in play / not final yet
                    else:
                        st = stats.get(int(p["pid"]))
                        if st is None:
                            row["r"], row["u"] = "v", 0.0   # didn't play: void, stake returned
                        else:
                            st = {**st, "id": int(p["pid"])}
                            ppp = None
                            if p["m"] == "ppp" and "gl" in gp_:
                                ppp = sum(1 for g_ in gp_["gl"] if g_[4] and int(p["pid"]) in (g_[0], g_[1], g_[2]))
                            elif p["m"] == "ppp":
                                pp_ = _pp_points(src, int(gid))
                                ppp = None if pp_ is None else pp_.get(int(p["pid"]), 0)
                            won = _result(p["m"], st, (fgs or {}).get(int(gid), "?"), ppp)
                            if won is None:
                                row["r"] = "p"
                            else:
                                row["r"] = "w" if won else "l"
                                row["u"] = round(p["o"] - 1, 3) if won else -1.0
                    rows.append(row)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"built": now.isoformat(timespec="minutes"), "rows": rows},
                                   separators=(",", ":")), encoding="utf-8")
    done = [r for r in rows if r.get("r") in ("w", "l") and r["k"] == "all"]
    if done:
        u = sum(r["u"] for r in done)
        log(f"Best picks record: {len(done)} graded, {sum(r['r'] == 'w' for r in done)} won, {u:+.2f} units")
    return len(rows)


def backfill(frozen_dir: Path, archive_dir: Path, open_dir: Path, bp_dir: Path, players_now: dict,
             before: str, log=print) -> int:
    """Rebuild the picks of nights before the record started, from the lock snapshots (probabilities and odds
    in data/frozen) and the players as they were (archive). Their first price is the day's opening price."""
    bp_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(frozen_dir.glob("20*.json")):
        d = f.stem
        if d >= before or (bp_dir / f"{d}.json").exists():
            continue
        snap = json.loads(f.read_text(encoding="utf-8"))
        arch = archive_dir / f"{d}.json"
        arch = (json.loads(arch.read_text(encoding="utf-8")).get("games") or {}) if arch.exists() else {}
        opens = open_dir / f"{d}.json"
        opens = json.loads(opens.read_text(encoding="utf-8")) if opens.exists() else {}
        store = {}
        for gid, rec in snap.items():
            g, odds = rec.get("game") or {}, rec.get("odds")
            if not odds or not odds.get("players"):
                continue
            players = {**players_now, **((arch.get(gid) or {}).get("players") or {})}
            op = (opens.get(gid) or {}).get("players") or {}
            out, seen = {}, {}
            for kind, mk in (("all", ALL), ("multi", MULTI)):
                picks = choose(g, odds, players, mk)
                for p in picks:
                    book = ((odds["players"].get(str(p["pid"])) or odds["players"].get(p["pid"]) or {}).get(p["m"]) or {})
                    o0 = (op.get(str(p["pid"])) or {}).get(p["m"] + ("@p" if book.get("b") == "p" else ""))
                    seen[f"{p['pid']}|{p['m']}"] = {"o": o0 or p["o"], "at": "open"}
                out[kind] = [{"pid": p["pid"], "name": p["name"], "team": p["team"], "opp": p["opp"], "m": p["m"],
                              "p": round(p["p"], 4), "o": p["o"], "ev": round(p["ev"], 4)} for p in picks]
            store[gid] = {"home": g.get("home"), "away": g.get("away"), "start": g.get("start"), "at": rec.get("at"),
                          "picks": out, "seen": seen, "backfill": True}
            n += 1
        if store:
            (bp_dir / f"{d}.json").write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
            log(f"  backfilled {d}: {len(store)} games")
    return n
