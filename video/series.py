"""evenout short videos: three series rendered from the site's own numbers, one MP4 each (9:16, 30 fps, music).

    py video/series.py --date 2026-11-16 --all                  # what the day's schedule asks for
    py video/series.py --date 2026-11-16 --series board,hot,report
    py video/series.py --date 2026-11-16 --series board --preview 2,8,20   # still frames only

Series
  board   Tonight's Board (every game day): the day's best graded value picks (Solid / Lean), 1 per ~3 games, max 4,
          each with the grade and why; sometimes one "looks like value... but Fragile" teaching pick.
  hot     Hot or Lucky? (Tue / Thu / Sat): the hottest and coldest regulars by Heat Index, what the run means.
  report  Report Card (Mondays, every week good or bad): last week's Triage results by grade, one per player,
          the rolling 30 days and season, and the pick the model got most wrong.

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
def load_page_data(date: str, html: str, pro: Path | None, work: Path) -> dict:
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
        out = pg.evaluate((HERE / "series_extract.js").read_text(encoding="utf-8"), {"day": date, "testAllowF": bool(__import__("os").environ.get("VIDEO_TEST_ALLOW_F"))})
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
        parts.append({"kind": "pick", "a": t, "b": t + per, "name": p["name"], "num": badge(p["num"], p["name"]), "color": team_color(p["team"]),
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
        parts.append({"kind": "heat", "a": t, "b": t + per, "name": p["name"], "num": badge(p["num"], p["name"]), "color": team_color(p["team"]),
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
PLATFORMS = {"board": ("youtube",), "hot": ("youtube", "tiktok"), "report": ("youtube",)}

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


# ------------------------------------------------------------------ captions
TAGS = ["NHL", "hockey", "NHLstats", "hockeytok", "analytics"]


def captions(spec: dict, day: dt.date) -> dict:
    yt = f"https://{SITE}/?ref=yt"
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
                page.evaluate(f"render({t})")
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
            page.evaluate(f"render({f / FPS})")
            r = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 92, "optimizeForSpeed": True})
            ff.stdin.write(base64.b64decode(r["data"]))
            if f % 150 == 0:
                print(f"  {spec['series']}: frame {f}/{n} {time.time() - t0:.0f}s", flush=True)
        ff.stdin.close(); ff.wait()
        # cover: the first pick / first player, fully drawn
        p0 = spec["parts"][0]
        page.evaluate(f"render({min(p0['b'] - 0.6, p0['a'] + 4.5)})")
        page.screenshot(path=f"{out_base}-cover.jpg", type="jpeg", quality=90)
        br.close()
    if ff.returncode:
        raise SystemExit("ffmpeg failed")


def todays_series(day: dt.date) -> list:
    wd = day.weekday()                  # Mon 0
    s = ["board"]
    if wd == 0:
        s.insert(0, "report")
    if wd in (1, 3, 5):
        s.append("hot")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(ET).date().isoformat())
    ap.add_argument("--series", help="comma list: board,hot,report")
    ap.add_argument("--all", action="store_true", help="the day's schedule (board daily, report Mondays, hot Tue/Thu/Sat)")
    ap.add_argument("--site", help="built app page (default origin/site:app/index.html)")
    ap.add_argument("--pro", default=str(ROOT / "output" / "pro" / "latest.json"), help="Pro data patch (output/pro/latest.json)")
    ap.add_argument("--ledger", default=str(ROOT / "output" / "ledger.json"))
    ap.add_argument("--out", default=str(ROOT / "video" / "out"))
    ap.add_argument("--preview", help="comma-separated times: still frames only")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date)
    series = a.series.split(",") if a.series else todays_series(day) if a.all else ["board"]
    out = Path(a.out) / a.date
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"date": a.date, "items": []}
    mpath = out / "manifest.json"
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        ext = None
        if any(s in ("board", "hot") for s in series):
            ext = load_page_data(a.date, site_html(a.site), Path(a.pro) if a.pro else None, work)
            print(f"page data: {ext['n_games']} games, {len(ext['board'])} board picks, trap={'yes' if ext.get('trap') else 'no'}, "
                  f"hot {[round(p['v'], 1) for p in ext['hot']]}, cold {[round(p['v'], 1) for p in ext['cold']]}")
        for s in series:
            styles = getattr(music, "SERIES_STYLES", {}).get(s)
            bpm = {"board": 120, "hot": 140, "report": 96}.get(s, 120)
            spec = (spec_board(ext, day, bpm) if s == "board" else spec_hot(ext, day, bpm) if s == "hot"
                    else spec_report(ledger_rows(Path(a.ledger)), day, bpm))
            if not spec:
                print(f"{s}: nothing to show today, skipped")
                continue
            base = out / f"{s}-{a.date}"
            sw = work / s
            sw.mkdir()
            print(f"{s}: {spec['timing']['total']:.1f}s, {len(spec['parts'])} scenes" + (f", music styles {styles}" if styles else ""))
            render_video(spec, sw, base, seed=a.date, preview=[float(x) for x in a.preview.split(",")] if a.preview else None)
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
