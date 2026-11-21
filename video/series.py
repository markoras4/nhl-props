"""evenout short videos: four series rendered from the site's own numbers, one MP4 each (9:16, 30 fps, music).

    py video/series.py --date 2026-11-16 --all                  # what the day's schedule asks for
    py video/series.py --date 2026-11-16 --series board,hot,report
    py video/series.py --date 2026-11-16 --series board --preview 2,8,20   # still frames only

Series
  board   Tonight's Board (every game day): the day's best graded value picks (Solid / Lean), 1 per ~3 games, max 4,
          each with the grade and why; sometimes one "looks like value... but Fragile" teaching pick.
  hot     Hot or Lucky? (Tue / Thu / Sat): the hottest and coldest regulars by Heat Index, what the run means.
  report  Report Card (Mondays, every week good or bad): last week's Triage results by grade, one per player,
          the rolling 30 days and season, and the pick the model got most wrong.
  night   Last night (every morning, YouTube + TikTok, stats only): yesterday's final scores, the star of the night,
          then the strongest few of wall of the night, line of the night, finishing vs xG, where the goals came from and
          playoff-chance movers. Data: origin/site:data/archive/<yesterday>.json, data/shots.json, data/frozen/<yesterday>.json
          (playoff chances at lock) and the built page (night_extract.js: numbers after last night). Never odds or picks.

Numbers come from the built app page (default origin/site:app/index.html) with the Pro data merged in (--pro, default
output/pro/latest.json): the page's own triageRows / heat index, so videos always match the site. Track-record numbers
come from the Triage ledger (data/ledger.json). Music: music.series_track (original, synthesised, varies by day).

Output in --out/<DATE>/: <series>-<DATE>.mp4, <series>-<DATE>-cover.jpg and manifest.json (titles, descriptions and
captions per platform; video/upload.py posts them). One video file serves both platforms. Pick videos (board,
report) go to YouTube only (see PLATFORMS): TikTok treats picks and odds as gambling promotion.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import music  # noqa: E402
from render import TEAM, site_html  # noqa: E402

W, H, FPS = 1080, 1920, 30
SITE = "evenout.app"
ET = ZoneInfo("America/New_York")
MARKETS = {"g": "Goal", "a": "Assist", "pt": "Point", "g2": "2+ goals", "a2": "2+ assists", "pt2": "2+ points", "pt3": "3+ points",
           "ppp": "Power-play point", "fg": "First goal"}
GRADE = {"S": "Solid", "L": "Lean", "F": "Fragile"}


def mname(m: str, side: str = "o") -> str:
    if m[:1] in "shb" and m[1:2].isdigit():
        what = {"s": "shots", "h": "hits", "b": "blocks"}[m[0]]
        return f"{'Under' if side == 'n' else 'Over'} {m[1:]} {what}"
    n = MARKETS.get(m, m)
    return ("No " + n.lower()) if side == "n" else n


def seconds(beats: float, bpm: float) -> float:
    return beats * 60 / bpm


# ------------------------------------------------------------------ data
def load_page_data(date: str, html: str, pro: Path | None, work: Path, script: str = "series_extract.js", args: dict | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    page = work / "site.html"
    page.write_text(html, encoding="utf-8")
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(locale="en-US")
        pg.add_init_script("try{localStorage.setItem('age18','1');localStorage.setItem('set:lang','en')}catch(e){}")
        pg.route(lambda u: not u.startswith("file:"), lambda r: r.abort())
        pg.goto(page.as_uri())
        pg.wait_for_function("typeof triageRows === 'function' && typeof DATA !== 'undefined'", timeout=60000)
        if pro and pro.exists():
            pg.evaluate("(X) => { applyPro(X); }", json.loads(pro.read_text(encoding="utf-8")))
        out = pg.evaluate((HERE / script).read_text(encoding="utf-8"),
                          args if args is not None else {"day": date, "testAllowF": bool(__import__("os").environ.get("VIDEO_TEST_ALLOW_F"))})
        br.close()
    return out


def ledger_rows(path: Path | None) -> list:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("rows", [])
    try:
        raw = subprocess.run(["git", "show", "origin/site:data/ledger.json"], cwd=ROOT, capture_output=True, check=True).stdout
        return json.loads(raw).get("rows", [])
    except Exception:  # noqa: BLE001
        return []


def badge(num, name: str) -> str:
    return str(num) if num else "".join(w[0] for w in str(name).split()[:2]).upper()


def team_color(t: str) -> str:
    return TEAM.get(t, "#ff4fa8")


def short_day(d: dt.date) -> str:
    return d.strftime("%a · %b ").upper() + str(d.day)


# ------------------------------------------------------------------ series specs
def spec_board(ext: dict, day: dt.date, bpm: float) -> dict | None:
    picks = list(ext["board"])
    if not picks:
        return None
    use_trap = ext.get("trap") and (day.toordinal() % 2 == 0 or len(picks) <= 2)   # a teaching pick about every other day
    intro, per, outro = seconds(6, bpm), seconds(14, bpm), seconds(8, bpm)
    parts, t = [], intro
    for p in picks + ([ext["trap"]] if use_trap else []):
        trap = p is ext.get("trap")
        parts.append({"kind": "pick", "a": t, "b": t + per, "gid": str(p["game"]), "name": p["name"], "num": badge(p["num"], p["name"]), "color": team_color(p["team"]),
                      "meta": p["meta"], "market": p["market"], "marketP": p["market_p"], "grade": p["grade"],
                      "gradeTxt": GRADE[p["grade"]], "model": p["model"], "edge": p["edge"],
                      "trap": "Looks like value... but" if trap else None, "why": p["why"][:3]})
        t += per
    total = t + outro
    k = len(picks)
    return {"series": "board", "badge": "Tonight's Board", "day": short_day(day), "title1": "Tonight's", "title2": "Board",
            "sub": f"{ext['n_games']} games · {k} model pick{'s' if k > 1 else ''}",
            "chips": [["SOLID", "#ff4fa8"], ["LEAN", "#ffb547"], ["FRAGILE", "#ff6a3d"]],
            "parts": parts, "timing": {"intro": intro, "outroAt": t, "total": total, "beat": 60 / bpm},
            "linkSub": "Every player, every game", "outro1": "Every player. Every game.", "outro3": "Free to start · link in bio",
            "_picks": picks, "_trap": ext.get("trap") if use_trap else None}


HEAT_LEVEL = [(4, "Very hot"), (2.5, "Hot"), (1, "Warm"), (-1, "Normal"), (-2.5, "Cool")]


def heat_level(v: float) -> str:
    for th, n in HEAT_LEVEL:
        if v >= th:
            return n
    return "Cold"


def spec_hot(ext: dict, day: dt.date, bpm: float) -> dict | None:
    hot = [p for p in ext["hot"] if p["v"] >= 2.5][:1]
    cold = [p for p in ext["cold"] if p["v"] <= -2.5][:1]
    players = hot + cold
    if not players:
        return None
    intro, per, outro = seconds(6, bpm), seconds(20, bpm), seconds(8, bpm)
    parts, t = [], intro
    for p in players:
        v = p["v"]
        if v >= 4:
            verdict, sub = "Partly real", "Mostly luck, but at +4 and hotter some of it carries over: in four seasons these players still beat expectations a little."
        elif v >= 1:
            verdict, sub = "Mostly luck", "About 1 point in 10 of a run like this carries over. Don't pay extra for the streak."
        else:
            verdict, sub = "Slump won't last", "Cold runs don't carry over: in four seasons the model's number held for cold players."
        sh = ""
        if p.get("sh_cur") is not None and p.get("sh_true"):
            sh = f"Shooting {p['sh_cur'] * 100:.1f}% this season vs {p['sh_true'] * 100:.1f}% true talent"
        nxt = p.get("next") or {}
        if nxt.get("pt"):
            sub += f" Next: {nxt['meta']}, {round(nxt['pt'] * 100)}% for a point."
        parts.append({"kind": "heat", "a": t, "b": t + per, "gid": str((p.get("next") or {}).get("game") or ""), "name": p["name"], "num": badge(p["num"], p["name"]), "color": team_color(p["team"]),
                      "meta": f"{p['team']} · {p.get('pos') or ''}".strip(" ·"), "v": v, "n": p["n"], "level": heat_level(v),
                      "line": f"{p['x']} point{'' if p['x'] == 1 else 's'} in his last {p['n']} games vs {p['e']:.1f} expected for his ice time",
                      "games": p["games"][-10:], "sh": sh, "verdict": verdict, "verdictSub": sub})
        t += per
    return {"series": "hot", "badge": "Hot or Lucky?", "day": short_day(day), "title1": "Hot", "title2": "or lucky?",
            "sub": "The Heat Index: points vs what his ice time should give", "chips": [["HOT", "#ff6a3d"], ["COLD", "#b794ff"]],
            "parts": parts, "timing": {"intro": intro, "outroAt": t, "total": t + outro, "beat": 60 / bpm},
            "linkSub": "Heat Index for every player", "outro1": "Streak or skill? Check any player.", "outro3": "Free to start · link in bio",
            "_players": players}


# Where each series is posted. TikTok removes pick/odds videos as gambling promotion (the Board was taken down on
# 2026-11-17 for Community Guidelines), so picks stay on YouTube; TikTok gets the stats-only series.
PLATFORMS = {"board": ("youtube",), "hot": ("youtube", "tiktok"), "report": ("youtube",), "night": ("youtube", "tiktok")}

VIDEO_PICKS = ROOT / "data" / "video_picks.json"


def log_board_picks(date: str, picks: list) -> None:
    """data/video_picks.json: every pick shown in Tonight's Board (committed by the videos workflow)."""
    try:
        log = json.loads(VIDEO_PICKS.read_text(encoding="utf-8")) if VIDEO_PICKS.exists() else {}
    except Exception:  # noqa: BLE001
        log = {}
    log[date] = [{"g": p["game"], "pid": int(p["id"]) if str(p["id"]).isdigit() else p["id"], "n": p["name"], "market": p["market"],
                  "grade": p["grade"], "p": p["model"], "od": p.get("od")} for p in picks]
    VIDEO_PICKS.parent.mkdir(parents=True, exist_ok=True)
    VIDEO_PICKS.write_text(json.dumps(dict(sorted(log.items())), indent=1, ensure_ascii=False), encoding="utf-8")


def shown_results(rows: list, start: dt.date, end: dt.date) -> dict | None:
    """The board picks of the week, graded from the ledger (same game, player, grade-day)."""
    try:
        log = json.loads(VIDEO_PICKS.read_text(encoding="utf-8")) if VIDEO_PICKS.exists() else {}
    except Exception:  # noqa: BLE001
        return None
    shown = [(d, x) for d, xs in log.items() if start.isoformat() <= d <= end.isoformat() for x in xs]
    if not shown:
        return None
    w = l = 0; u = 0.0
    for d, x in shown:
        cand = [r for r in rows if str(r["g"]) == str(x["g"]) and str(r["pid"]) == str(x["pid"]) and r.get("r") in ("w", "l")]
        best = max(cand, key=lambda r: abs((r.get("p") or 0) - (x.get("p") or 0)) * -1, default=None)
        if best:
            w += best["r"] == "w"; l += best["r"] == "l"; u += best.get("u") or 0
    return {"w": w, "l": l, "u": round(u, 2), "n": len(shown)} if w + l else None


def spec_report(rows: list, day: dt.date, bpm: float) -> dict | None:
    from nhlprops.__main__ import one_per_player
    end = day - dt.timedelta(days=1)                    # last full week: the 7 days before today
    start = end - dt.timedelta(days=6)
    def done(rs):
        return [r for r in one_per_player([r for r in rs if not (r["m"][0] == "b" and r["m"][1:2].isdigit())]) if r.get("r") in ("w", "l", "v", "p")]
    wk = done([r for r in rows if start.isoformat() <= r["d"] <= end.isoformat()])
    if not wk:
        return None
    m30 = done([r for r in rows if (end - dt.timedelta(days=29)).isoformat() <= r["d"] <= end.isoformat()])
    season = done(rows)
    def stat(rs):
        w = sum(r["r"] == "w" for r in rs); l = sum(r["r"] == "l" for r in rs); v = sum(r["r"] in ("v", "p") for r in rs)
        u = round(sum(r.get("u") or 0 for r in rs), 2)
        return {"w": w, "l": l, "v": v, "u": u, "n": len(rs), "hit": (w / (w + l)) if w + l else 0}
    g = {k: stat([r for r in wk if r.get("gr") == k]) for k in "SLF"}
    tot = stat(wk)
    rowsSpec = [{"g": k, "name": GRADE[k], **g[k]} for k in "SLF" if g[k]["n"]]
    # an honest one-liner that fits what happened
    s_hit, f_hit = g["S"]["hit"], g["F"]["hit"]
    if g["F"]["n"] and g["S"]["n"] and s_hit > f_hit:
        note_h, note = "The grades did their job", f"Solid hit {s_hit * 100:.0f}%, Fragile {f_hit * 100:.0f}%. That gap is why Fragile is Fragile."
    elif tot["u"] < 0:
        note_h, note = "A losing week", "It happens, even with an edge. Every pick stays on the record, locked before puck drop."
    else:
        note_h, note = "Steady", "Every pick stays on the record, locked before puck drop, wins and losses."
    intro, outro = seconds(6, bpm), seconds(8, bpm)
    a, parts = intro, []
    parts.append({"kind": "grades", "a": a, "b": a + seconds(18, bpm), "head": f"Week of {start.strftime('%b')} {start.day}–{end.day} · by grade",
                  "rows": rowsSpec, "noteH": note_h, "note": note}); a = parts[-1]["b"]
    s30, ss = stat(m30), stat(season)
    shown = shown_results(rows, start, end)
    roi = lambda s: f"{(s['u'] / s['n'] * 100 if s['n'] else 0):+.1f}%"
    parts.append({"kind": "totals", "a": a, "b": a + seconds(14, bpm), "head": "Last week, all grades", "u": tot["u"],
                  "sub": f"{tot['w']}–{tot['l']} · {tot['n']} picks · ROI {roi(tot)}",
                  "kv": ([["Picks shown in our videos", f"{shown['w']}–{shown['l']} · {shown['u']:+.1f}u", "#ff4fa8" if shown["u"] >= 0 else "#ff6a3d"]] if shown else []) + [["Last 30 days", f"{s30['u']:+.1f}u", "#ff4fa8" if s30["u"] >= 0 else "#ff6a3d"],
                         ["Season", f"{ss['u']:+.1f}u", "#ff4fa8" if ss["u"] >= 0 else "#ff6a3d"]],
                  "foot": "1 unit per pick · one pick per player per game · the full ledger is on the site"}); a = parts[-1]["b"]
    miss = sorted([r for r in wk if r["r"] == "l" and r.get("gr") == "S"] or [r for r in wk if r["r"] == "l"], key=lambda r: -r["p"])
    if miss:
        m = miss[0]
        parts.append({"kind": "miss", "a": a, "b": a + seconds(14, bpm), "head": "What the model got wrong", "name": m["n"], "num": badge("", m["n"]),
                      "color": team_color(m["t"]), "meta": f"{m['t']} {'vs' if m['t'] == m['h'] else '@'} {m['op']} · {m['d'][5:]}",
                      "market": mname(m["m"], m.get("s", "o")), "model": m["p"],
                      "lesson": f"The model gave it {m['p'] * 100:.0f}% ({GRADE.get(m.get('gr'), '')}). Even good chances miss; this is why we track every pick."})
        a = parts[-1]["b"]
    return {"series": "report", "badge": "Report Card", "day": f"{start.strftime('%b').upper()} {start.day}–{end.day}",
            "title1": "Weekly", "title2": "Report card", "sub": "Every graded pick. Wins and losses.",
            "chips": [["HONEST", "#b794ff"], ["EVERY WEEK", "#ff4fa8"]], "parts": parts,
            "timing": {"intro": intro, "outroAt": a, "total": a + outro, "beat": 60 / bpm},
            "linkSub": "The full track record", "outro1": "Every pick. On the record.", "outro3": "Free to start · link in bio",
            "_tot": tot, "_g": g, "_start": start.isoformat(), "_end": end.isoformat()}


# ------------------------------------------------------------------ last night (stats only)
# Posted to TikTok too, so nothing here may come from odds, picks, grades, prop lines or prices: box scores, shot
# locations, season stats, xG, the Heat Index and the season simulation's playoff chances only.
def git_json(spec: str):
    try:
        return json.loads(subprocess.run(["git", "show", spec], cwd=ROOT, capture_output=True, check=True).stdout)
    except Exception:  # noqa: BLE001
        return None


def frozen_file(d: str):
    """data/frozen/<d>.json (pre-game snapshot incl. the teams' futures at lock): working tree, else origin/main."""
    f = ROOT / "data" / "frozen" / f"{d}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return git_json(f"origin/main:data/frozen/{d}.json")


def load_night(date: str, html: str, work: Path) -> dict | None:
    """Last night = the day before the run date (New York). None when there is no archive (no games) for it."""
    y = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    arch = git_json(f"origin/site:data/archive/{y}.json")
    if not arch or not arch.get("results"):
        return None
    pids = sorted({pid for r in arch["results"].values() for pid in (r.get("players") or {})})
    page = {}
    try:
        page = load_page_data(date, html, None, work, script="night_extract.js", args={"pids": pids})
    except Exception as e:  # noqa: BLE001
        print("night: site page numbers unavailable, using the archive only:", e)
    return {"y": y, "arch": arch, "shots": git_json("origin/site:data/shots.json") or {}, "frozen": frozen_file(y) or {},
            "frozen_today": frozen_file(date) or {}, "page": page or {}}


def _min(toi) -> float:
    try:
        m, s = str(toi).split(":")
        return int(m) + int(s) / 60
    except Exception:  # noqa: BLE001
        return 0.0


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _plural(n, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def spec_night(n: dict | None, day: dt.date, bpm: float) -> dict | None:
    if not n:
        return None
    A, page = n["arch"], n.get("page") or {}
    y = n["y"]
    yd = dt.date.fromisoformat(y)
    fresh = page.get("date") == day.isoformat()       # the site page was built after last night's games
    PP, PG = page.get("players") or {}, page.get("goalies") or {}
    games = []
    for g in sorted(A.get("games") or [], key=lambda g: g.get("start") or ""):
        r = (A.get("results") or {}).get(str(g["id"]))
        if r and r.get("hs") is not None and r.get("as") is not None:
            games.append({**g, "_r": r})
    if not games:
        return None
    info = lambda pid: (A.get("players") or {}).get(pid) or (A.get("goalies") or {}).get(pid) or PP.get(pid) or PG.get(pid) or {}
    after = lambda pid: PP.get(pid) if fresh and (PP.get(pid) or {}).get("last") == y else None   # the site's numbers incl. last night

    def res(team, g):
        r = g["_r"]; home = team == g["home"]
        ts, os_ = (r["hs"], r["as"]) if home else (r["as"], r["hs"])
        last = r.get("last") or g.get("last_period") or "REG"
        return f"{'W' if ts > os_ else 'L'} {ts}–{os_} {'vs' if home else '@'} {g['away'] if home else g['home']}" + (f" ({last})" if last in ("OT", "SO") else "")

    rows = []                                           # every skater / goalie line of the night
    for g in games:
        for pid, st in (g["_r"].get("players") or {}).items():
            p = info(pid)
            if p.get("team") in (g["home"], g["away"]):
                rows.append((pid, st, p, g))
    skaters = [x for x in rows if not x[1].get("gk")]
    goalies = [x for x in rows if x[1].get("gk")]
    seedf = lambda k: (zlib_crc(f"{y}|{k}") % 1000) / 1000

    # ---- 1. scoreboard
    tot_goals = sum(g["_r"]["hs"] + g["_r"]["as"] for g in games)
    n_ot = sum((g["_r"].get("last") or g.get("last_period")) in ("OT", "SO") for g in games)
    def gl_(st):                                        # "2G 1A", "2G", "3A"
        return " ".join(f"{st.get(k, 0)}{k.upper()}" for k in ("g", "a") if st.get(k, 0)) or "0 PTS"

    def tops(g):
        xs = sorted([x for x in skaters if x[3] is g and x[1].get("g", 0) + x[1].get("a", 0) >= 2 or (x[3] is g and x[1].get("g", 0) >= 2)],
                    key=lambda x: (-(x[1].get("g", 0) + x[1].get("a", 0)), -x[1].get("g", 0)))[:2]
        return " · ".join(f"{x[2].get('name', '').split(' ', 1)[-1]} {gl_(x[1])}" for x in xs)
    scores = {"kind": "scores", "head": f"Final scores · {yd.strftime('%b')} {yd.day}",
              "games": [{"away": g["away"], "home": g["home"], "as": g["_r"]["as"], "hs": g["_r"]["hs"],
                         "last": g["_r"].get("last") or g.get("last_period") or "REG", "ac": team_color(g["away"]), "hc": team_color(g["home"]), "top": tops(g) if len(games) <= 5 else ""} for g in games],
              "foot": f"{_plural(tot_goals, 'goal')} in {_plural(len(games), 'game')}" + (f" · {n_ot} went past regulation" if n_ot else ""),
              "_fact": "Final scores: " + ", ".join(f"{g['away']} {g['_r']['as']}–{g['_r']['hs']} {g['home']}"
                                                     + (f" ({g['_r'].get('last')})" if g["_r"].get("last") in ("OT", "SO") else "") for g in games)}

    # ---- 2. star of the night
    star = None
    if skaters:
        pid, st, p, g = max(skaters, key=lambda x: (x[1].get("g", 0) + x[1].get("a", 0), x[1].get("g", 0), x[1].get("s", 0)))
        G, Ast, S = st.get("g", 0), st.get("a", 0), st.get("s", 0)
        if G + Ast >= 2:
            pa = after(pid)
            cur = (pa or {}).get("cur")
            if not cur and (p.get("windows") or {}).get("cur"):
                c = p["windows"]["cur"]
                cur = {"gp": c.get("gp", 0) + 1, "g": c.get("g", 0) + G, "a": c.get("a", 0) + Ast, "p": c.get("p", 0) + G + Ast}
            season = (f"Season: <em>{cur['g']}</em> goals, <em>{cur['p']}</em> points in {cur['gp']} games" if cur and cur.get("gp") else "")
            heat = (pa or {}).get("heat")
            if not heat:                                 # recompute the site's Heat Index with last night added
                fg, rt = (p.get("form") or {}).get("games") or [], p.get("rates") or {}
                prev = [x for x in fg if x.get("d", "") < y][:9]
                if len(prev) >= 7 and rt.get("g60") is not None:
                    xx = G + Ast + sum(x.get("g", 0) + x.get("a", 0) for x in prev)
                    ee = (rt["g60"] + rt.get("a60", 0)) * (_min(st.get("toi")) + sum(x.get("t", 0) for x in prev)) / 60
                    heat = {"v": round(xx - ee, 2), "x": xx, "e": round(ee, 2), "n": len(prev) + 1}
            hp = None
            if heat and heat.get("n", 0) >= 8:
                hp = {"v": heat["v"], "n": heat["n"], "level": heat_level(heat["v"]),
                      "line": f"{_plural(heat['x'], 'point')} in his last {heat['n']} games vs {heat['e']:.1f} expected for his ice time"}
            what = "hat trick" if G >= 3 else f"{G + Ast}-point night"
            star = {"kind": "star", "gid": str(g["id"]), "head": "Star of the night", "name": p.get("name", ""), "num": badge(p.get("num"), p.get("name", "")),
                    "color": team_color(p["team"]), "meta": f"{p['team']} · {res(p['team'], g)}",
                    "tiles": [["G", G], ["A", Ast], ["PTS", G + Ast], ["SOG", S], ["TOI", (st.get("toi") or "–").lstrip("0") or "0"]],
                    "season": season, "heat": hp,
                    "_head": f"{p.get('name', '')}'s {what}", "_pid": pid,
                    "_fact": f"Star of the night: {p.get('name', '')} ({p['team']}), {_plural(G, 'goal')}, {_plural(Ast, 'assist')}, {S} shots on goal in {st.get('toi')}"
                             + (f" ({cur['g']} goals, {cur['p']} points in {cur['gp']} games this season)" if cur and cur.get("gp") else "")}

    opt = []                                            # (interest, part); the best few make the video
    # ---- 3. wall of the night
    gl = [x for x in goalies if x[1].get("sa", 0) >= 15 and _min(x[1].get("toi")) >= 40]
    if gl:
        def wscore(x):
            st, g, t = x[1], x[3], x[2]["team"]
            won = (g["_r"]["hs"] > g["_r"]["as"]) == (t == g["home"])
            return st.get("sv", 0) + 5 * won + 10 * (st.get("ga", 1) == 0)
        pid, st, p, g = max(gl, key=wscore)
        sv, sa, ga = st.get("sv", 0), st.get("sa", 0), st.get("ga", 0)
        svp = sv / sa if sa else 0
        if sv >= 30 or (svp >= .94 and sa >= 25) or (ga == 0 and sa >= 20):
            team = p["team"]; opp = g["away"] if team == g["home"] else g["home"]
            won = (g["_r"]["hs"] > g["_r"]["as"]) == (team == g["home"])
            own = sum(x[1].get("s", 0) for x in skaters if x[3] is g and x[2].get("team") == team)
            shots = (f"{opp} outshot {team} {sa}–{own}" + (f" and still lost" if won else "") if sa > own + 2 else f"Shots: {team} {own}, {opp} {sa}") if own else ""
            pg_ = PG.get(pid) if fresh else None
            season = ""
            if pg_ and pg_.get("rk"):
                season = (f"Season: <em>{pg_['gsax']:+.1f}</em> goals saved above expected, {_ord(pg_['rk'])} of {pg_['of']} goalies"
                          f" (5+ games)")
            elif pg_ and pg_.get("gp") and pg_.get("sv"):
                season = f"Season: <em>{pg_['sv']:.3f}</em> save % in {pg_['gp']} games".replace(">0.", ">.")
            elif (p.get("cur") or {}).get("gp"):
                season = f"Before last night: <em>{p['cur']['gsax']:+.1f}</em> goals saved above expected in {p['cur']['gp']} games"
            opt.append(((sv - 24) / 10 + (1 if ga == 0 else 0), {
                "kind": "wall", "gid": str(g["id"]), "head": "Shutout of the night" if ga == 0 else "Wall of the night", "name": p.get("name", ""),
                "num": badge(p.get("num"), p.get("name", "")), "color": team_color(p["team"]), "meta": f"{p['team']} · {res(p['team'], g)}",
                "sv": sv, "sa": sa, "svp": round(svp, 4), "sub": f"{sv} saves on {sa} shots" + (" · shutout" if ga == 0 else ""), "season": season, "shots": shots,
                "_fact": f"Wall of the night: {p.get('name', '')} ({p['team']}), {sv} saves on {sa} shots ({svp:.3f})".replace("(0.", "(.")}))

    # ---- 4. line of the night (pre-game lines, combined points)
    best = None
    for g in games:
        stats = g["_r"].get("players") or {}
        for t in (g["home"], g["away"]):
            tm = (A.get("teams") or {}).get(t) or {}
            groups = ((tm.get("lineup") or {}).get("groups") or {})
            for k in ("f1", "f2", "f3", "f4"):
                ids = [str(x) for x in groups.get(k) or []]
                if len(ids) != 3 or not all(i in stats for i in ids):
                    continue
                pts = sum(stats[i].get("g", 0) + stats[i].get("a", 0) for i in ids)
                scorers = sum(stats[i].get("g", 0) + stats[i].get("a", 0) > 0 for i in ids)
                if pts >= 4 and scorers >= 2 and (not best or pts > best[0]):
                    best = (pts, t, k, ids, g, tm)
    if best:
        pts, t, k, ids, g, tm = best
        stats = g["_r"]["players"]
        mates = [{"name": info(i).get("name", ""), "num": badge(info(i).get("num"), info(i).get("name", "")),
                  "st": gl_(stats[i])} for i in ids]
        l5 = ((tm.get("lines5") or {}).get(k) or {})
        l5p = None
        if l5.get("gp", 0) >= 3 and l5.get("toi", 0) >= 60 and l5.get("xgf") is not None:
            d = l5["gf"] - l5["xgf"]
            verdict = (f"{_plural(l5['gf'], 'goal')} on {l5['xgf']:.1f} expected: finishing above their chances" if d >= 1.5 else
                       f"{_plural(l5['gf'], 'goal')} on {l5['xgf']:.1f} expected: their chances deserved more" if d <= -1.5 else
                       "Scoring about what their chances deserve")
            l5p = {"head": f"Together at 5v5 before last night · {l5['toi']:.0f} min", "gf": l5["gf"], "xgf": float(l5["xgf"]), "verdict": verdict}
        opt.append((pts / 5 + (.2 if l5p else 0), {
            "kind": "line", "gid": str(g["id"]), "head": "Line of the night", "meta": f"{t} · {'first second third fourth'.split()[int(k[1]) - 1]} line · {res(t, g)}",
            "color": team_color(t), "mates": mates, "pts": pts, "bigLab": "combined points", "l5": l5p,
            "_fact": f"Line of the night: {' - '.join(m['name'] for m in mates)} ({t}), {pts} points combined"}))

    # ---- 5. luck or real: finishing vs expected goals
    def season_g_xg(pid, p, g_last):
        pa = after(pid)
        if pa and pa.get("xg") and pa["xg"].get("xg") is not None:
            return max(pa["xg"]["g"], (pa.get("cur") or {}).get("g") or 0), pa["xg"]["xg"]   # xG can lag a game behind
        xw = (p.get("xg_windows") or {}).get("cur")
        if xw and xw.get("xg") is not None:
            return xw.get("g", 0) + g_last, xw["xg"]
        return None
    luck = []
    for pid, st, p, g in skaters:
        if star and pid == star["_pid"]:
            continue
        G, S = st.get("g", 0), st.get("s", 0)
        sg = season_g_xg(pid, p, G)
        if not sg or not sg[1]:
            continue
        sgl, sxg = int(round(sg[0])), float(sg[1])
        if S >= 6 and G == 0:
            luck.append(((S - 5) / 3 + seedf("snake" + pid) * .3, pid, st, p, g, "snake", sgl, sxg))
        if G >= 1 and sgl - sxg >= 3 and sgl >= 1.5 * sxg:
            luck.append(((sgl - sxg) / 4 + seedf("hot" + pid) * .3, pid, st, p, g, "hot", sgl, sxg))
    if luck:
        sc, pid, st, p, g, kind, sgl, sxg = max(luck, key=lambda x: x[0])
        G, S = st.get("g", 0), st.get("s", 0)
        nm = p.get("name", "")
        if kind == "snake":
            if sgl < sxg - 1:
                verdict, sub = "Snakebitten", f"{_plural(sgl, 'goal')} on {sxg:.1f} expected this season. Chances like these tend to turn into goals over time."
            else:
                verdict, sub = "Just not his night", f"The chances are there: {_plural(sgl, 'goal')} on {sxg:.1f} expected this season."
            part = {"bigN": str(S), "bigLab": "shots on goal,<br>no goal", "dots": {"n": min(S, 12), "g": 0}, "col": "var(--purple)",
                    "head": "Unlucky or cold?", "_fact": f"{nm} ({p['team']}): {S} shots on goal, no goal. Season: {sgl} goals on {sxg:.1f} xG"}
        else:
            verdict, sub = "Finishing hot", f"{_plural(sgl, 'goal')} on {sxg:.1f} expected this season: {sgl / sxg:.1f}x what his chances suggest. Finishing runs this far above xG usually cool off."
            part = {"bigN": str(G), "bigLab": f"{'goal' if G == 1 else 'goals'} last night<br>on {_plural(S, 'shot')}", "dots": {"n": min(S, 12), "g": min(G, 12)},
                    "col": "var(--amber)", "head": "Skill or luck?", "_fact": f"{nm} ({p['team']}) scored again: {sgl} goals this season on {sxg:.1f} xG"}
        opt.append((sc, {"kind": "luck", "gid": str(g["id"]), "name": nm, "num": badge(p.get("num"), nm), "color": team_color(p["team"]),
                         "meta": f"{p['team']} · {res(p['team'], g)}", "g": sgl, "xg": round(float(sxg), 2), "seasonHead": "Season so far · goals vs expected (xG)",
                         "verdict": verdict, "verdictSub": sub, **part}))

    # ---- 6. playoff race: chance before the game (frozen at lock) vs after (site page / today's lock)
    fz, fzt = n.get("frozen") or {}, n.get("frozen_today") or {}
    later = {}
    for v in fzt.values():
        for t, f in (((v or {}).get("stakes") or {}).get("teams") or {}).items():
            if f.get("playoffs") is not None:
                later[t] = f["playoffs"]
    if fresh:
        later.update({t: f["playoffs"] for t, f in (page.get("futures") or {}).items() if f.get("playoffs") is not None})
    moves = []
    for g in games:
        st = ((fz.get(str(g["id"])) or {}).get("stakes") or {}).get("teams") or {}
        for t in (g["home"], g["away"]):
            b = (st.get(t) or {}).get("playoffs")
            if b is not None and later.get(t) is not None:
                moves.append({"team": t, "color": team_color(t), "res": res(t, g), "before": round(b, 4), "after": round(later[t], 4), "d": round(later[t] - b, 4)})
    moves = [m for m in moves if abs(m["d"]) >= .04]
    if moves:
        up = sorted([m for m in moves if m["d"] > 0], key=lambda m: -m["d"])
        dn = sorted([m for m in moves if m["d"] < 0], key=lambda m: m["d"])
        pick = (up[:1] + dn[:1])
        pick += sorted([m for m in moves if m not in pick], key=lambda m: -abs(m["d"]))[:3 - len(pick)]
        pick.sort(key=lambda m: -m["d"])
        opt.append((max(abs(m["d"]) for m in pick) / .06, {
            "kind": "race", "head": "Playoff chances · after last night", "rows": pick,
            "foot": "From the season simulation: every remaining game played out thousands of times",
            "_fact": "Playoff chances moved: " + ", ".join(f"{m['team']} {m['before'] * 100:.0f}% → {m['after'] * 100:.0f}%" for m in pick)}))

    # ---- 7. where the goals came from (shots.json, kind G on last night's date)
    dots, played = [], {t for g in games for t in (g["home"], g["away"])}
    for pid, v in (n.get("shots") or {}).items():
        ds = v.get("d") or []
        if y not in ds:
            continue
        i = ds.index(y)
        t = info(pid).get("team")
        if t not in played:
            continue
        for s in v.get("s") or []:
            if len(s) > 3 and s[3] == i and s[2] == "G":
                x, yy = float(s[0]), float(s[1])
                if x < 0:
                    x, yy = -x, -yy
                dots.append({"x": round(min(98.0, max(27.0, x)), 1), "y": round(min(40.0, max(-40.0, yy)), 1), "c": team_color(t), "_d": ((89 - x) ** 2 + yy ** 2) ** .5})
    if len(dots) >= 4 and len(dots) >= .7 * tot_goals:
        close = sum(d["_d"] <= 20 for d in dots)
        far = len(dots) - close
        lab = "goals from inside 20 feet" if close >= far else "goals from 20+ feet out"
        k = close if close >= far else far
        opt.append((len(dots) / 9, {"kind": "goals", "head": "Where last night's goals came from", "dots": [{kk: vv for kk, vv in d.items() if kk != "_d"} for d in dots],
                                    "bigN": f"{k}/{len(dots)}", "bigLab": lab, "_fact": f"{k} of {len(dots)} mapped goals came {lab[6:]}"}))

    # ---- choose: the scoreboard, the star, then the strongest others (a little day-to-day variety)
    room = 4 - (1 if star else 0)
    opt.sort(key=lambda x: -(x[0] + seedf(x[1]["kind"]) * .8))
    chosen = [o[1] for o in opt[:room]]
    order = ["wall", "line", "luck", "goals", "race"]
    chosen.sort(key=lambda x: order.index(x["kind"]))
    plan = [scores] + ([star] if star else []) + chosen
    b = 60 / bpm
    intro, outro = 6 * b, 8 * b
    t, parts = intro, []
    for P in plan:
        d = (12 if len(games) > 7 else 10) * b if P["kind"] == "scores" else 11 * b
        parts.append({**{k: v for k, v in P.items() if not k.startswith("_")}, "a": t, "b": t + d})
        t += d
    facts = [P["_fact"] for P in plan]
    head = star["_head"] if star else (chosen[0]["_fact"].split(":")[0] if chosen else f"{_plural(len(games), 'game')}, {_plural(tot_goals, 'goal')}")
    return {"series": "night", "badge": "Last night", "day": short_day(yd), "title1": "Last", "title2": "night",
            "sub": f"{yd.strftime('%A')} · {_plural(len(games), 'game')} · {_plural(tot_goals, 'goal')}",
            "chips": [["SCORES", "#ff4fa8"], ["STARS", "#ffb547"], ["STATS", "#b794ff"]],
            "parts": parts, "timing": {"intro": intro, "outroAt": t, "total": t + outro, "beat": b},
            "cover": 1 if star else 0,
            "linkSub": "Every player, every game", "outro1": "Every player. Every game.", "outro3": "Free to start · link in bio",
            "disclaimer": "Stats from NHL game data · xG and playoff chances are model estimates",
            "_facts": facts, "_head": head, "_y": yd, "_star": {"pid": star["_pid"], "gid": star["gid"], "name": star["name"]} if star else None,
            "_luck": next(({"name": P["name"]} for P in chosen if P["kind"] in ("luck", "wall")), None)}


def zlib_crc(s: str) -> int:
    import zlib
    return zlib.crc32(s.encode())


# ------------------------------------------------------------------ real images: preview photos + app clips
STAT_OF = {"Goal": "g", "2+ goals": "g", "First goal": "g", "Assist": "a", "2+ assists": "a"}


def _mini(P: dict, big: str, lab: str) -> dict:
    """The headline card in small, for the split beat (card left, the real page on the phone right)."""
    return {"name": P["name"], "num": P.get("num", ""), "color": P.get("color", "#ff4fa8"), "meta": P.get("meta", ""), "big": big, "lab": lab}


def clip_plan(spec: dict, day: dt.date) -> list:
    """The "on evenout" moments of a Short (2-3, about a quarter of the runtime), each tied to what is on screen:
    right after the headline card the same player on the real app (split beat), later a page that fits the story
    (Explorer, Futures playoff chances, the game page's team ranks), the last one just before the outro.
    [{"after": index of the part it follows (None = before the outro), "req": what to record, "cap": caption,
      "split": mini card or None}]. Every page is odds-free (appclips.py), the same rules for every series."""
    s, parts, out = spec["series"], spec["parts"], []
    if s == "board" and spec.get("_picks"):
        p = spec["_picks"][0]
        m = p["market"]
        stat = "s" if "shots" in m.lower() else STAT_OF.get(m, "p")
        i0 = next((i for i, P in enumerate(parts) if P["kind"] == "pick"), 0)
        out.append({"after": i0, "req": {"type": "player", "pid": p["id"], "stat": stat, "game": p["game"]},
                    "cap": f"{p['name']}'s last 10 games", "split": _mini(parts[i0], p["market"], parts[i0].get("gradeTxt", ""))})
        if sum(P["kind"] == "pick" for P in parts) < 2:
            pass                                         # one pick: the clips would run back to back, one is enough
        elif day.toordinal() % 2:
            out.append({"after": None, "req": {"type": "futures", "game": p["game"]}, "cap": "Playoff chances, both teams"})
        else:
            out.append({"after": None, "req": {"type": "game", "view": "teams", "game": p["game"], "focus": "^xGF%"}, "cap": "His game tonight: team ranks"})
    elif s == "hot" and spec.get("_players"):
        ps = spec["_players"]
        i0 = next(i for i, P in enumerate(parts) if P["kind"] == "heat")
        P0 = parts[i0]
        out.append({"after": i0, "req": {"type": "player", "pid": ps[0]["id"], "stat": "p", "game": (ps[0].get("next") or {}).get("game")},
                    "cap": f"{ps[0]['name']}'s Heat Index & last 10", "split": _mini(P0, f"{P0['v']:+.1f}", "Heat Index")})
        out.append({"after": None, "req": {"type": "explore", "q": ps[-1]["name"].split()[-1]}, "cap": "Goals vs expected goals, every player"})
    elif s == "night":
        star = next((i for i, P in enumerate(parts) if P["kind"] == "star"), None)
        if star is not None and spec.get("_star"):
            P = parts[star]
            pts = next((v for k, v in P.get("tiles", []) if k == "PTS"), "")
            out.append({"after": star, "req": {"type": "player", "pid": spec["_star"]["pid"], "stat": "p", "game": None},
                        "cap": f"{spec['_star']['name']}'s last 10 games", "split": _mini(P, str(pts), "points last night")})
        luck = next((i for i, P in enumerate(parts) if P["kind"] == "luck"), None)
        if luck is not None:
            out.append({"after": luck, "req": {"type": "explore", "q": parts[luck]["name"].split()[-1]}, "cap": "Goals vs expected goals, every player"})
        race = next((P for P in parts if P["kind"] == "race"), None)
        if race:
            out.append({"after": None, "req": {"type": "futures", "teams": [r["team"] for r in race["rows"]][:2]}, "cap": "Playoff chances after last night"})
        elif luck is None and spec.get("_luck"):
            out.append({"after": None, "req": {"type": "explore", "q": spec["_luck"]["name"].split()[-1]}, "cap": "Goals vs expected goals, every player"})
        elif star is not None:
            out.append({"after": None, "req": {"type": "futures", "team": parts[star]["meta"].split(" · ")[0]},
                        "cap": f"{parts[star]['meta'].split(' · ')[0]} in the playoff race"})
    return out[:3]


CLIP_DUR, CLIP_LEAD, CLIP_TAIL = 4.0, .45, .45


def record_clip(C, req: dict, name: str, cap: str) -> dict | None:
    t = req["type"]
    if t == "player":
        return C.player(req["pid"], stat=req.get("stat", "p"), kind="phone", name=name, caption=cap, prefer_game=req.get("game"), dur=CLIP_DUR)
    if t == "explore":
        return C.explorer(req["q"], kind="phone", name=name, caption=cap, dur=CLIP_DUR + .2)
    if t == "game":
        return C.game(req["game"], req["view"], kind="phone", name=name, caption=cap, focus=req.get("focus"), dur=CLIP_DUR + .2)
    if t == "futures":
        teams = req.get("teams")
        if not teams:
            g = C.game_info(gid=req["game"]) if req.get("game") else C.game_info(team=req["team"]) if req.get("team") else None
            teams = [g["away"], g["home"]] if g else ([req["team"]] if req.get("team") else [])
        return C.futures(teams, kind="phone", name=name, caption=cap, dur=CLIP_DUR + .2) if teams else None
    return None


def insert_app(spec: dict, after, clip: dict, split=None) -> float:
    """Put an app clip after part `after` (None: before the outro); every later scene moves back."""
    T, parts = spec["timing"], spec["parts"]
    a = T["outroAt"] if after is None else parts[after]["b"]
    d = CLIP_LEAD + clip["dur"] + CLIP_TAIL
    for P in parts:
        if P["a"] >= a - 1e-6:
            P["a"] += d
            P["b"] += d
    gid = clip.get("game")
    bg = next((P.get("bg") for P in parts if P.get("bg") and gid and str(P.get("gid")) == str(gid)), None) or \
        (parts[after].get("bg") if after is not None else None) or next((P.get("bg") for P in parts if P.get("bg")), None)
    part = {"kind": "app", "a": a, "b": a + d, "lead": CLIP_LEAD, "src": f"img/{clip['dir']}/", "n": clip["n"], "w": clip["w"], "h": clip["h"],
            "cap": clip["caption"], "url": clip["url"].replace("https://", ""), "bg": bg}
    if split:
        part["split"] = split
    parts.insert(len(parts) if after is None else after + 1, part)
    T["outroAt"] += d
    T["total"] += d
    return d


def add_media(spec: dict, day: dt.date, sw: Path, get_clips) -> None:
    """Preview photos behind the cards (where the game has one) and 2-3 app clips (clip_plan). A clip that can't be
    recorded is left out and the timeline closes up."""
    import preview_photos as PH
    s = spec["series"]
    pdate = (spec["_y"].isoformat() if s == "night" else day.isoformat())
    photos = PH.game_photos(pdate) if s != "report" else {}
    used = []
    for P in spec["parts"]:
        ph = photos.get(str(P.get("gid") or ""))
        if not ph:
            continue
        P["photo"] = "img/" + Path(PH.hero(ph, sw / "img" / f"ph-{ph['slug']}.jpg", (968, 420), dark=.62, edge=.7)).name
        P["bg"] = "img/" + Path(PH.backdrop(ph, sw / "img" / f"bg-{ph['slug']}.jpg", (1080, 1920))).name
        used.append(ph)
    spec["_photos"] = used
    plan = clip_plan(spec, day)
    if not plan:
        return
    got = []                                            # (moment, clip): all recorded first, then inserted
    C = None
    try:
        C = get_clips()
    except Exception as e:  # noqa: BLE001
        print(f"  app clips unavailable ({e})")
    for i, m in enumerate(plan):
        clip = None
        try:
            clip = record_clip(C, m["req"], f"app{i}", m["cap"]) if C else None
        except Exception as e:  # noqa: BLE001
            print(f"  app clip {i} failed ({e}): video without it")
        if clip:
            got.append((m, clip))
    # from the back, so the part indices in the plan stay valid
    for m, clip in sorted(got, key=lambda x: -(10 ** 6 if x[0]["after"] is None else x[0]["after"])):
        insert_app(spec, m["after"], clip, m.get("split"))
    spec["_clips"] = [c for _, c in got]
    spec["_clip"] = got[0][1] if got else None


def _clips_in(c, d: Path):
    c.work = Path(d)                                    # frames go next to the video's page (img/app/...)
    return c


# ------------------------------------------------------------------ captions
TAGS = ["NHL", "hockey", "NHLstats", "hockeytok", "analytics"]


def captions(spec: dict, day: dt.date) -> dict:
    """Titles, descriptions and captions; photo credits (no credits on screen) go at the end of each."""
    import preview_photos as PH
    cap = _captions(spec, day)
    cr = PH.credit_lines(spec.get("_photos") or [])
    if cr["long"]:
        cap["youtube"]["description"] += "\n\n" + cr["long"]
    if cr["short"] and "tiktok" in cap:
        cap["tiktok"]["caption"] += "\n" + cr["short"]
    return cap


def _captions(spec: dict, day: dt.date) -> dict:
    yt = f"https://{SITE}/?ref=yt"
    if spec["series"] == "night":                       # stats only, also on TikTok: no picks, odds or advice wording
        yd = spec["_y"]
        title = f"Last night in the NHL: {spec['_head']}"
        body = f"The NHL games of {yd.strftime('%A, %b')} {yd.day}, in numbers.\n" + "\n".join(f"• {f}" for f in spec["_facts"])
        tags = ["NHL", "hockey", "NHLstats", "NHLhighlights", "analytics"]
        return {
            "youtube": {"title": title[:100], "tags": tags,
                        "description": f"{body}\n\nEvery player, every game, free to start: {yt}\n\nStats only.\n\n#" + " #".join(tags)},
            "tiktok": {"caption": f"{title}\n\n{body}\n\nLink in bio: {SITE}\nStats only.\n#NHL #hockey #NHLstats #hockeytok"},
        }
    disc = "18+. Model numbers and statistics, not betting advice."
    s = spec["series"]
    if s == "board":
        lines = [f"• {p['name']}: {p['market']} ({GRADE[p['grade']]}, model {round(p['model'] * 100)}% vs market {round(p['market_p'] * 100)}%)" for p in spec["_picks"]]
        title = f"Tonight's Board: NHL model picks, {day.strftime('%b')} {day.day}"
        body = "The model's best graded spots tonight, Solid or Lean, and why.\n" + "\n".join(lines)
    elif s == "hot":
        lines = [f"• {p['name']}: Heat Index {p['v']:+.1f} ({heat_level(p['v'])})" for p in spec["_players"]]
        title = "Hot or lucky? NHL streaks checked against expected points"
        body = "Is the streak real or luck? The Heat Index compares points with what each player's ice time should give.\n" + "\n".join(lines)
    else:
        tot = spec["_tot"]
        title = f"NHL model report card: week of {spec['day'].title()}"
        body = (f"Last week, every graded pick: {tot['w']}-{tot['l']}, {tot['u']:+.1f} units. By grade, with the pick the model got most wrong.\n"
                "Posted every Monday, good weeks and bad.")
    tags = TAGS + {"board": ["NHLpicks", "playerprops"], "hot": ["HeatIndex", "streak"], "report": ["trackrecord"]}[s]
    return {
        "youtube": {"title": title[:100], "description": f"{body}\n\nEvery player, every game, free to start: {yt}\n\n{disc}\n\n#" + " #".join(tags[:6]), "tags": tags},
        "tiktok": {"caption": f"{title}\n\n{body}\n\nLink in bio: {SITE}\n{disc}\n#" + " #".join(tags)},
    }


# ------------------------------------------------------------------ render
# render(t), then wait until a changed app-clip frame (an <img>) is decoded, so every capture shows the right frame
RENDER = "async t => { render(t); const p = window.PEND; window.PEND = null; if (p) await p; }"


def render_video(spec: dict, work: Path, out_base: Path, seed: str, preview=None) -> None:
    from playwright.sync_api import sync_playwright
    (work / "img").mkdir(parents=True, exist_ok=True)
    shutil.copytree(HERE / "fonts", work / "fonts", dirs_exist_ok=True)
    shutil.copy(ROOT / "site-assets" / "icons" / "icon-512.png", work / "img" / "icon.png")
    shutil.copy(ROOT / "site-assets" / "brand" / "evenout-logo-dark.svg", work / "img" / "logo.svg")
    clean = {k: v for k, v in spec.items() if not k.startswith("_")}
    (work / "index.html").write_text((HERE / "series.html").read_text(encoding="utf-8").replace("/*SPEC*/null", json.dumps(clean)), encoding="utf-8")
    total = spec["timing"]["total"]
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--allow-file-access-from-files"])
        page = br.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.on("pageerror", lambda e: print("  page error:", e))
        page.goto((work / "index.html").as_uri())
        page.wait_for_function("window.READY === true", timeout=60000)
        if preview:
            for t in preview:
                page.evaluate(RENDER, t)
                page.screenshot(path=f"{out_base}-t{t:05.2f}.jpg", type="jpeg", quality=88)
            br.close()
            return
        cues = [p["a"] for p in spec["parts"]] + [spec["timing"]["outroAt"]]
        wav = work / "music.wav"
        song = music.series_track(spec["series"], total, cues=cues, seed=seed) if hasattr(music, "series_track") else music.track(total, cues=cues)
        music.write_wav(str(wav), song)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
               "-i", str(wav), "-map", "0:v", "-map", "1:a", "-vf", "noise=alls=3:allf=t", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
               "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p",
               "-profile:v", "high", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", f"{out_base}.mp4"]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        cdp = page.context.new_cdp_session(page)
        n = int(round(total * FPS)); t0 = time.time()
        for f in range(n):
            page.evaluate(RENDER, f / FPS)
            r = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 92, "optimizeForSpeed": True})
            ff.stdin.write(base64.b64decode(r["data"]))
            if f % 150 == 0:
                print(f"  {spec['series']}: frame {f}/{n} {time.time() - t0:.0f}s", flush=True)
        ff.stdin.close(); ff.wait()
        # cover: the first pick / first player, fully drawn
        p0 = spec["parts"][min(spec.get("cover", 0), len(spec["parts"]) - 1)]
        page.evaluate(RENDER, min(p0['b'] - 0.6, p0['a'] + 4.5))
        page.screenshot(path=f"{out_base}-cover.jpg", type="jpeg", quality=90)
        br.close()
    if ff.returncode:
        raise SystemExit("ffmpeg failed")


# Series about games already played (posted in the morning slot); the rest are about tonight's games.
PAST_SERIES = {"report", "night"}


def todays_series(day: dt.date) -> list:
    wd = day.weekday()                  # Mon 0
    s = ["night", "board"]                # night: last night's games, every morning (empty when there were none)
    if wd == 0:
        s.insert(0, "report")
    if wd in (1, 3, 5):
        s.append("hot")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(ET).date().isoformat())
    ap.add_argument("--series", help="comma list: board,hot,report,night")
    ap.add_argument("--all", action="store_true", help="the day's schedule (night + board daily, report Mondays, hot Tue/Thu/Sat)")
    ap.add_argument("--slot", choices=["morning", "upcoming"],
                    help="with --all: morning = videos about past games (last night, weekly report), upcoming = tonight's games")
    ap.add_argument("--site", help="built app page (default origin/site:app/index.html)")
    ap.add_argument("--pro", default=str(ROOT / "output" / "pro" / "latest.json"), help="Pro data patch (output/pro/latest.json)")
    ap.add_argument("--ledger", default=str(ROOT / "output" / "ledger.json"))
    ap.add_argument("--out", default=str(ROOT / "video" / "out"))
    ap.add_argument("--preview", help="comma-separated times: still frames only")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date)
    series = a.series.split(",") if a.series else todays_series(day) if a.all else ["board"]
    if a.slot:
        series = [x for x in series if (x in PAST_SERIES) == (a.slot == "morning")]
    if not series:
        print(f"Nothing scheduled for {a.date} in the {a.slot} slot.")
        return
    out = Path(a.out) / a.date
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"date": a.date, "items": []}
    mpath = out / "manifest.json"
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    clips = {}

    def get_clips():
        if "c" not in clips:
            try:
                from appclips import AppClips
                clips["c"] = AppClips(work / "appsite")
            except Exception as e:  # noqa: BLE001
                print("app clips unavailable:", e)
                clips["c"] = None
        return clips["c"]
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        ext = None
        if any(s in ("board", "hot") for s in series):
            ext = load_page_data(a.date, site_html(a.site), Path(a.pro) if a.pro else None, work)
            print(f"page data: {ext['n_games']} games, {len(ext['board'])} board picks, trap={'yes' if ext.get('trap') else 'no'}, "
                  f"hot {[round(p['v'], 1) for p in ext['hot']]}, cold {[round(p['v'], 1) for p in ext['cold']]}")
        night = None
        if "night" in series:
            night = load_night(a.date, site_html(a.site), work)
            print(f"night: {night['y']}, {len(night['arch']['results'])} games, page date {night['page'].get('date')}" if night
                  else f"night: no games archived for the day before {a.date}")
        jobs = []                                       # first every spec + its media (one browser for the app clips) ...
        for s in series:
            styles = getattr(music, "SERIES_STYLES", {}).get(s)
            bpm = {"board": 120, "hot": 140, "report": 96, "night": 104}.get(s, 120)
            spec = (spec_board(ext, day, bpm) if s == "board" else spec_hot(ext, day, bpm) if s == "hot"
                    else spec_night(night, day, bpm) if s == "night" else spec_report(ledger_rows(Path(a.ledger)), day, bpm))
            if not spec:
                print(f"{s}: nothing to show today, skipped")
                continue
            sw = work / s
            (sw / "img").mkdir(parents=True)
            t0 = time.time()
            add_media(spec, day, sw, lambda: get_clips() and _clips_in(clips["c"], sw / "img"))
            T_ = spec["timing"]; app_s = sum(P["b"] - P["a"] for P in spec["parts"] if P["kind"] == "app")
            print(f"{s}: {len(spec.get('_photos') or [])} photo(s), {len(spec.get('_clips') or [])} app clip(s), {app_s:.1f}s of {T_['total']:.1f}s "
                  f"({app_s / T_['total'] * 100:.0f}%) ({time.time() - t0:.0f}s)")
            for P in spec["parts"]:
                if P["kind"] == "app":
                    print(f"    app {P['a']:5.1f}-{P['b']:5.1f}s{' split' if P.get('split') else ''}: {P['cap']}")
            jobs.append((s, spec, sw, styles))
        if clips.get("c"):
            clips["c"].close()
        for s, spec, sw, styles in jobs:                # ... then the renders (Playwright can't nest two sessions)
            base = out / f"{s}-{a.date}"
            print(f"{s}: {spec['timing']['total']:.1f}s, {len(spec['parts'])} scenes" + (f", music styles {styles}" if styles else ""))
            t0 = time.time()
            render_video(spec, sw, base, seed=a.date, preview=[float(x) for x in a.preview.split(",")] if a.preview else None)
            print(f"{s}: rendered in {time.time() - t0:.0f}s")
            if a.preview:
                continue
            if s == "board":
                log_board_picks(a.date, spec["_picks"])
            cap = captions(spec, day)
            rel = lambda p: str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/") if str(Path(p).resolve()).startswith(str(ROOT)) else str(p)
            manifest["items"] = [i for i in manifest["items"] if i.get("series") != s]
            for plat, c in cap.items():
                if plat not in PLATFORMS[s]:
                    continue
                manifest["items"].append({"id": f"{s}-{a.date}", "series": s, "platform": plat, "file": rel(f"{base}.mp4"),
                                          "cover": rel(f"{base}-cover.jpg"), **c})
    if not a.preview:
        mpath.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        print("manifest:", mpath)


if __name__ == "__main__":
    main()
