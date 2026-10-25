"""Command line:

    python -m nhlprops build [--date YYYY-MM-DD] [--no-open]
    python -m nhlprops grade
    python -m nhlprops check
    python -m nhlprops build --demo     (simulated data, no internet)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import webbrowser
from pathlib import Path

import numpy as np

from .config import CONFIG
from .gather import gather, season_start_for
from .grade import grade
from .model import build_slate
from .sources import Source

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "template.html"


def nhl_today() -> str:
    """Today's date in New York (NHL dates are North American)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date().isoformat()


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 5)
    return o


def demo_odds(slate: dict) -> dict:
    """Bookmaker-like prices for demo mode: model probability +/- noise, ~7% margin."""
    import random
    rnd = random.Random(3)
    out = {}
    for g in slate["games"][:8]:
        po = {}
        for pr in g["props"]:
            if not slate["players"][str(pr["id"])]["active"]:
                continue
            d = {}
            for m in ("g", "a", "pt", "s1.5", "s2.5", "s3.5"):
                p = min(0.95, max(0.02, pr["p"][m] * rnd.uniform(0.82, 1.18)))
                d[m] = {"o": round(1 / (p * 1.07), 2), "n": round(1 / ((1 - p) * 1.07), 2)}
            po[pr["id"]] = d
        out[g["id"]] = {"book": "Bookmaker", "players": po, "fetched": "demo",
                        "totals": {"5.5": {"over": 1.85, "under": 1.95}, "6.5": {"over": 2.6, "under": 1.5}},
                        "winner": {"home": round(1 / (g["default"]["p_home"] * 1.05), 2),
                                   "away": round(1 / (g["default"]["p_away"] * 1.05), 2)}}
    return out


def save_history(slate: dict, hist_dir: Path):
    """One file per game date. Games that have already started keep their earlier prediction."""
    hist_dir.mkdir(parents=True, exist_ok=True)
    players = slate["players"]
    by_date: dict[str, list] = {}
    for g in slate["games"]:
        if g.get("state") not in (None, "FUT", "PRE"):
            continue
        by_date.setdefault(g["date"], []).append(g)
    for d, games in by_date.items():
        f = hist_dir / f"{d}.json"
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"games": [], "props": []}
        ids = {g["id"] for g in games}
        rec = {
            "date": d, "built": dt.datetime.now().isoformat(timespec="seconds"),
            "games": [g for g in old["games"] if g["id"] not in ids] + [
                {"id": g["id"], "home": g["home"], "away": g["away"],
                 "model": {str(L): g["default"][f"over_{L}"] for L in CONFIG["ou_lines"]},
                 "hist": g["hist_over"]} for g in games],
            "props": [p for p in old["props"] if p["game"] not in ids] + [
                {"id": pr["id"], "name": players[str(pr["id"])]["name"], "game": g["id"], "p": pr["p"],
                 "o": {m: v["o"] for m, v in (slate.get("odds", {}).get(g["id"], {}).get("players", {})
                                              .get(pr["id"], {})).items()}}
                for g in games for pr in g["props"] if players[str(pr["id"])]["active"]],
        }
        f.write_text(json.dumps(clean(rec)), encoding="utf-8")


def team_logos(src: Source, slate: dict, logo_dir: Path) -> dict:
    import base64
    teams = sorted({g["home"] for g in slate["games"]} | {g["away"] for g in slate["games"]})
    out = {}
    for t in teams:
        v = {}
        for variant in ("dark", "light"):
            svg = src.logo(t, variant, logo_dir)
            if svg:
                v[variant] = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
        if v:
            out[t] = v
    return out


def render(slate: dict, graded: dict | None, out_dir: Path, demo: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = clean({**slate, "graded": graded, "config": CONFIG, "demo": demo,
                     "built": dt.datetime.now().isoformat(timespec="minutes")})
    js = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", js)
    dated = out_dir / f"nhl-props-{slate['date']}{'-demo' if demo else ''}.html"
    dated.write_text(html, encoding="utf-8")
    (out_dir / ("index-demo.html" if demo else "index.html")).write_text(html, encoding="utf-8")
    return dated


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nhlprops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="download data, run the model, write the dashboard")
    b.add_argument("--date", default=nhl_today(),
                   help="slate date (NHL / North American date). Default: today in New York")
    b.add_argument("--no-open", action="store_true")
    b.add_argument("--offline", action="store_true", help="use cached data only")
    b.add_argument("--demo", action="store_true", help="simulated data, no internet")
    gp = sub.add_parser("grade", help="grade saved predictions against final results")
    gp.add_argument("--demo", action="store_true")
    sub.add_parser("check", help="test that every data source answers and has the expected columns")
    pg = sub.add_parser("pregame", help="exit 0 and print run=true if a game starts within the window (for the hourly job)")
    pg.add_argument("--window", type=int, default=75, help="minutes before puck drop")
    bt = sub.add_parser("backtest", help="replay past seasons game by game (does not touch the website)")
    bt.add_argument("--seasons", default="", help="comma-separated start years, default: the two previous seasons")
    bt.add_argument("--totals", action="store_true", help="test game totals instead of player props")
    args = ap.parse_args(argv)

    data_dir = ROOT / "data"
    if getattr(args, "demo", False):
        from . import demo
        cache = data_dir / "demo_cache"
        date = getattr(args, "date", None)
        if date and (not (cache / "demo_meta.json").exists()
                     or json.loads((cache / "demo_meta.json").read_text())["date"] != date):
            print("Generating simulated data ...")
            demo.generate(cache, args.date)
        src = Source(cache, offline=True)
        hist_dir = data_dir / "demo_history"
    else:
        src = Source(data_dir / "cache", offline=getattr(args, "offline", False),
                     interval=CONFIG["request_interval_s"])
        hist_dir = data_dir / "history"

    if args.cmd == "check":
        return check(src)
    if args.cmd == "pregame":
        return pregame(src, data_dir / "pregame.json", args.window)
    if args.cmd == "backtest":
        from .backtest import run as run_backtest
        cur = season_start_for(nhl_today())
        seasons = [int(x) for x in args.seasons.split(",") if x.strip()] or [cur - 2, cur - 1]
        if args.totals:
            from .backtest_totals import run as run_totals
            run_totals(src, seasons, CONFIG, ROOT / "backtest")
        else:
            run_backtest(src, seasons, CONFIG, ROOT / "backtest")
        return 0
    if args.cmd == "grade":
        if not hist_dir.exists() or not any(hist_dir.glob("20*.json")):
            print("No saved predictions yet. Run `build` before games start, then `grade` after they finish.")
            return 0
        grade(src, hist_dir)
        print("Run `build` again to see the results on the Track record tab.")
        return 0

    raw = gather(src, args.date, CONFIG)
    if not raw["games"]:
        if raw["errors"]:
            print("Could not download the NHL schedule:", raw["errors"][0])
            print("Check your internet connection, then run `python -m nhlprops check`.")
        else:
            print(f"No NHL games found for {args.date}. Try another --date.")
        return 1
    slate = build_slate(raw, CONFIG)
    if args.demo:
        slate["odds"] = demo_odds(slate)
    else:
        from .odds import fetch_odds
        try:
            slate["odds"] = fetch_odds(src, slate["games"], raw["skaters"])
        except Exception as e:  # odds are optional
            print("Odds unavailable:", e)
            slate["odds"] = {}
    save_history(slate, hist_dir)  # after odds, so the book price at prediction time is kept
    graded_path = hist_dir / "graded.json"
    graded = json.loads(graded_path.read_text()) if graded_path.exists() else None
    slate["logos"] = team_logos(src, slate, data_dir / ("demo_logos" if args.demo else "logos"))
    out = render(slate, graded, ROOT / "output", args.demo)
    print(f"Dashboard written: {out}")
    if raw["errors"]:
        print(f"Note: {len(raw['errors'])} downloads failed - see data/errors.log")
        (data_dir / "errors.log").write_text("\n".join(raw["errors"]), encoding="utf-8")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def pregame(src: Source, state_file: Path, window: int) -> int:
    """Pre-game refresh gate: one rebuild per puck-drop time, within `window` minutes before it."""
    now = dt.datetime.now(dt.timezone.utc)
    today = nhl_today()
    js = src.json(f"https://api-web.nhle.com/v1/schedule/{today}", f"pregame/schedule_{today}.json", 0.0)
    starts = set()
    for day in (js or {}).get("gameWeek", [])[:1]:
        for g in day.get("games", []):
            if g.get("gameState") in ("FUT", "PRE") and g.get("startTimeUTC"):
                starts.add(g["startTimeUTC"])
    done = json.loads(state_file.read_text()) if state_file.exists() else []
    due = sorted(s for s in starts if s not in done and
                 0 < (dt.datetime.fromisoformat(s.replace("Z", "+00:00")) - now).total_seconds() / 60 <= window)
    run = bool(due)
    if run:
        state_file.write_text(json.dumps(sorted(set(done) | set(due))[-60:]))
    print(f"Pre-game check {now:%H:%M} UTC: starts today {sorted(starts)} -> {'refresh for ' + ', '.join(due) if run else 'nothing due'}")
    import os
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"run={'true' if run else 'false'}\n")
    return 0


def check(src: Source) -> int:
    today = dt.date.today().isoformat()
    cur = season_start_for(today)
    ok = True

    def report(name, good, detail=""):
        nonlocal ok
        ok &= good
        print(f"  [{'OK ' if good else 'FAIL'}] {name} {detail}")

    print("Checking data sources ...")
    s = src.schedule(today)
    report("NHL schedule", bool(s and "gameWeek" in s))
    r = src.roster("TOR")
    report("NHL roster", bool(r and r.get("forwards")))
    c = src.club_schedule("TOR", cur, True)
    report("NHL club schedule", bool(c and c.get("games")))
    if r and r.get("forwards"):
        pid = r["forwards"][0]["id"]
        land = src.landing(pid)
        report("NHL player landing", bool(land and land.get("careerTotals")),
               "" if land and land.get("careerTotals") else "(no careerTotals)")
        gl = src.game_log(pid, cur - 1, False)
        has = gl and gl.get("gameLog") and all(k in gl["gameLog"][0] for k in ("goals", "shots", "toi", "gameDate"))
        report("NHL game log", bool(has))
    need = {
        "skaters": ["playerId", "situation", "icetime", "I_F_shotsOnGoal", "I_F_goals", "I_F_xGoals",
                    "I_F_primaryAssists", "I_F_secondaryAssists"],
        "goalies": ["playerId", "situation", "icetime", "xGoals", "goals", "ongoal"],
        "teams": ["team", "situation", "iceTime", "xGoalsFor", "xGoalsAgainst", "goalsFor", "goalsAgainst",
                  "shotsOnGoalFor", "shotsOnGoalAgainst", "highDangerShotsFor"],
    }
    for kind, cols in need.items():
        df = src.moneypuck(kind, cur - 1, False)
        if df is None:
            report(f"MoneyPuck {kind}", False, "(download failed)")
            continue
        miss = [c for c in cols if c not in df.columns]
        report(f"MoneyPuck {kind}", not miss, f"missing: {miss}" if miss else f"({len(df)} rows)")
    if src.errors:
        print("Errors:", *src.errors[:5], sep="\n  ")
    print("All good." if ok else "Some checks failed - send me the output above and I'll adapt the code.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
