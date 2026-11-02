"""Triage ledger: every value bet the model flags, all three grades (Solid, Lean, Fragile), as it stood at the lock
5 minutes before puck drop, graded after the game at a flat 1 unit. It measures the model itself and whether the
grades separate (Solid should beat Lean, Lean should beat Fragile); the best picks record stays the curated headline.

Same rules as the Triage list on the site: every player market with a sportsbook price (first goalscorer excluded),
overs and unders, positive EV at that price, graded with triage.grade. Every build refreshes the rows of games that
aren't locked yet, so the last refresh before the lock is the official one. Nothing is added or removed afterwards.

Stored in data/ledger/<date>.json; graded rows written to output/ledger.json for the Track record page. 'sf' is the
sharp book's fair price (margin removed) for the side at the lock, so od/sf - 1 is the edge against the sharp close.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .lock import is_locked


def rows_for_game(g: dict, odds: dict, players: dict) -> list[dict]:
    from .triage import grade as tri
    po = (odds or {}).get("players") or {}
    op_all = ((odds or {}).get("open") or {}).get("players") or {}
    st = (g.get("starter_status") or {})
    out = []
    for pr in g.get("props") or []:
        pl = players.get(str(pr["id"])) or players.get(pr["id"]) or {}
        if not pl.get("active"):
            continue
        prices = po.get(pr["id"]) or po.get(str(pr["id"])) or {}
        opn = op_all.get(pr["id"]) or op_all.get(str(pr["id"])) or {}
        for m, b in prices.items():
            if m == "fg" or m.startswith("fo") or not isinstance(b, dict) or not (pr.get("p") or {}).get(m):
                continue
            p = pr["p"][m]
            for side, od in (("o", b.get("o")), ("n", b.get("n"))):
                if not od:
                    continue
                ov = opn.get(m) if side == "o" else None
                try:
                    tg = tri(pl, pr, m, od, side, st.get(pr["opp"], ""), b, ov if isinstance(ov, (int, float)) else None)
                except Exception:  # noqa: BLE001
                    tg = {}
                if not tg:
                    continue
                pm = p if side == "o" else 1 - p
                out.append({"pid": pr["id"], "n": pl.get("name"), "t": pr["team"], "op": pr["opp"], "m": m, "s": side,
                            "p": round(pm, 4), "od": od, "ev": round(pm * od - 1, 4), "gr": tg["gr"], "c": tg.get("c") or [],
                            "sf": tg.get("sf"), "b": b.get("b") or "k", "v": tg.get("views") or {}, "i": tg.get("i") or []})
    return out


EARLY_FROM = "2026-11-02"    # from this date each bet is kept at the first price it was flagged at (see snapshot)


def _close_side(odds: dict, pid, m, side):
    """the book's current price for one side, and the sharp book's fair price for it (margin removed)"""
    po = ((odds or {}).get("players") or {})
    b = po.get(pid) or po.get(str(pid)) or {}
    v = b.get(m)
    if not isinstance(v, dict):
        return None, None
    c = v.get(side)
    sf = None
    so, sn = v.get("p"), v.get("pn")
    if so and sn:
        q = (1 / so) / (1 / so + 1 / sn)
        sf = round(1 / (q if side == "o" else 1 - q), 3)
    return c, sf


def snapshot(slate: dict, led_dir: Path, now: dt.datetime | None = None) -> None:
    """Value bets of every game that isn't locked.
    From EARLY_FROM: a Solid bet is locked the moment it is graded Solid (price, grade, time) and never changes again.
    Lean and Fragile bets follow the market: every build re-grades them with the same three checks, they drop out if
    the value goes, and they lock as Solid the moment they qualify; whatever they are at the game's lock is recorded.
    Every row also keeps where the book price and the sharp fair price close, for the CLV.
    Before EARLY_FROM all rows were refreshed until the lock."""
    led_dir.mkdir(parents=True, exist_ok=True)
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec="minutes")
    odds_all = slate.get("odds") or {}
    by_date: dict[str, list] = {}
    for g in slate["games"]:
        if not g.get("frozen") and not is_locked(g, now):
            by_date.setdefault(g["date"], []).append(g)
    for d, games in by_date.items():
        f = led_dir / f"{d}.json"
        store = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        for g in games:
            odds = odds_all.get(g["id"]) or odds_all.get(str(g["id"]))
            if not odds or not odds.get("players"):
                continue
            fresh = rows_for_game(g, odds, slate["players"])
            gid = str(g["id"])
            if d < EARLY_FROM:
                store[gid] = {"home": g["home"], "away": g["away"], "start": g.get("start"), "at": stamp, "rows": fresh}
                continue
            rec = store.get(gid) or {"home": g["home"], "away": g["away"], "start": g.get("start"), "rows": [], "early": True}
            rec["early"] = True
            rec["start"] = g.get("start")
            key = lambda r: (str(r["pid"]), r["m"], r["s"])
            now_rows = {key(r): r for r in fresh}
            kept = []
            for r in rec["rows"]:
                if r.get("lk") or (r.get("gr") == "S" and "lk" not in r and "fl" not in r):
                    r["lk"] = 1                              # Solid: locked at the price and time it became Solid
                    kept.append(r)
            locked = {key(r) for r in kept}
            for k, r in now_rows.items():                    # Lean / Fragile follow the market; turning Solid locks them
                if k in locked:
                    continue
                if r["gr"] == "S":
                    kept.append({**r, "at": stamp, "lk": 1})
                else:
                    kept.append({**r, "at": stamp, "fl": 1})
            rec["rows"] = kept
            for r in rec["rows"]:                      # closing price and sharp fair price follow the market
                c, sf = _close_side(odds, r["pid"], r["m"], r["s"])
                if c:
                    r["cl"] = c
                if sf:
                    r["csf"] = sf
            rec["at"] = stamp
            store[gid] = rec
        f.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")


def backfill_from_archive(arch: dict, led_dir: Path, log=print) -> int:
    """Rebuild one past night from its archive snapshot (games, odds and players as they were before puck drop)."""
    d = arch.get("date")
    f = led_dir / f"{d}.json"
    if not d or f.exists():
        return 0
    players = arch.get("players") or {}
    store = {}
    for g in arch.get("games") or []:
        odds = (arch.get("odds") or {}).get(str(g["id"])) or (arch.get("odds") or {}).get(g["id"])
        if not odds or not odds.get("players"):
            continue
        store[str(g["id"])] = {"home": g["home"], "away": g["away"], "start": g.get("start"), "at": g.get("frozen"),
                               "rows": rows_for_game(g, odds, players), "backfill": True}
    if store:
        led_dir.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
        log(f"  ledger backfilled {d}: {len(store)} games, {sum(len(v['rows']) for v in store.values())} value bets")
    return len(store)


def grade(src, led_dir: Path, out_path: Path, log=print) -> int:
    from .bestpicks import _result, _pp_points
    from .grade import _box_players, _first_scorers
    pbp = {}
    for fp in sorted(Path("data/shots").glob("*.json")) if Path("data/shots").exists() else []:
        try:
            pbp.update(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for f in sorted(led_dir.glob("20*.json")):
        d = f.stem
        store = json.loads(f.read_text(encoding="utf-8"))
        fgs = None
        for gid, rec in store.items():
            try:
                locked = rec.get("start") and now >= dt.datetime.fromisoformat(rec["start"].replace("Z", "+00:00")) - dt.timedelta(minutes=5)
            except ValueError:
                locked = False
            if not locked:
                continue
            box = src.boxscore(int(gid))
            final = bool(box) and box.get("gameState") in ("OFF", "FINAL")
            stats = _box_players(box) if final else {}
            if final and fgs is None:
                fgs = _first_scorers(src, d)
            gp_ = pbp.get(str(gid)) or {}
            pp_cache = None
            for x in rec.get("rows") or []:
                row = {"d": d, "g": int(gid), "h": rec["home"], "a": rec["away"], **{k: x[k] for k in ("pid", "n", "t", "op", "m", "s", "p", "od", "ev", "gr", "sf", "b")},
                       "c": len(x.get("c") or [])}
                if rec.get("early"):      # Solid: locked when it became Solid; Lean/Fragile: last price before the lock
                    row["e"] = 1
                    if x.get("lk"):
                        row["lk"] = 1
                        row["at"] = x.get("at")
                    if x.get("csf"):
                        row["sf"] = x["csf"]
                    if x.get("cl"):
                        row["cl"] = x["cl"]
                if not final:
                    row["r"] = "p"
                else:
                    st = stats.get(int(x["pid"]))
                    if st is None:
                        row["r"], row["u"] = "v", 0.0
                    else:
                        st = {**st, "id": int(x["pid"])}
                        ppp = None
                        if x["m"] == "ppp":
                            if "gl" in gp_:
                                ppp = sum(1 for g_ in gp_["gl"] if g_[4] and int(x["pid"]) in (g_[0], g_[1], g_[2]))
                            else:
                                if pp_cache is None:
                                    r_ = _pp_points(src, int(gid))
                                    pp_cache = False if r_ is None else r_      # False: summary not available yet
                                ppp = pp_cache.get(int(x["pid"]), 0) if pp_cache is not False else None
                        won = _result(x["m"], st, (fgs or {}).get(int(gid), "?"), ppp)
                        if won is None:
                            row["r"] = "p"
                        else:
                            if x["s"] == "n":
                                won = not won
                            row["r"] = "w" if won else "l"
                            row["u"] = round(x["od"] - 1, 3) if won else -1.0
                rows.append(row)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"built": now.isoformat(timespec="minutes"), "rows": rows}, separators=(",", ":")), encoding="utf-8")
    done = [r for r in rows if r.get("r") in ("w", "l")]
    nb = sum(1 for r in done if r["m"][0] == "b" and r["m"][1:2].isdigit())
    if nb >= 50:
        log(f"Triage ledger: {nb} graded blocks bets: time to review whether blocks join the headline Solid record")
    if done:
        by = {}
        for r in done:
            a = by.setdefault(r["gr"], [0, 0.0])
            a[0] += 1; a[1] += r["u"]
        log("Triage ledger: " + ", ".join(f"{k} {n} bets {u:+.1f}u" for k, (n, u) in sorted(by.items(), key=lambda z: "SLF".index(z[0]))))
    return len(rows)


if __name__ == "__main__":   # one-off: backfill nights from archive files given on the command line
    import sys
    for p in sys.argv[1:]:
        backfill_from_archive(json.loads(Path(p).read_text(encoding="utf-8")), Path("data/ledger"))
