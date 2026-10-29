"""Render the "last night's results" review video (9:16, TikTok / Instagram Reels / YouTube Shorts).

    python video/review.py [--date 2026-10-28] [--site path/to/app/index.html] [--out out_dir] [--preview 2,5,9]

DATE defaults to yesterday in New York. Every pick comes from the site's Track record (origin/site:
data/bestpicks.json, the picks as they stood at the lock, graded after the games), with that exact date. The
video goes through them one by one with the running profit, sums up the night, then opens the real site profile
(archive of that date, origin/site:data/archive/<DATE>.json, dark theme, phone width) of the players who made a
profit and highlights the numbers behind the pick. Music: music.review_track (a different song from the previews).

Output: <out>/<DATE>-nhl-review.mp4, <DATE>-review-cover.jpg, <DATE>-review-captions.txt
"""
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
import music  # noqa: E402
from render import TEAM, SITE, short_name  # noqa: E402

W, H, FPS, BPM = 1080, 1920, 30, 96
BEAT = 60 / BPM
INTRO = 8 * BEAT          # 5 s
SUMMARY = 6 * BEAT        # 3.75 s
MULTI = 6 * BEAT
BIG_HIT = 5.0             # the long shots (multi-point picks) only get a card when one of them won at odds this big or more
SPOT = 16 * BEAT          # 10 s per profit spotlight
OUTRO = 10 * BEAT         # 6.25 s
MAX_SPOTS = 2
PROF_VW, PROF_DPR, PROF_MAX = 430, 2.5, 2400
PVTAB = {"g": "sc", "a": "sc", "pt": "sc", "fg": "sc", "ppp": "mu", "pt2": "mu", "pt3": "mu", "g2": "mu", "g3": "mu", "a2": "mu"}


def git_show(path):
    r = subprocess.run(["git", "show", f"origin/site:{path}"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def pick_beats(n):
    return 4 if n <= 8 else 3 if n <= 12 else 2


def site_pass(html, archive, rows, spots, work):
    """One headless session on the built site in archive mode for DATE: market names for every pick, and a tall
    phone-width screenshot of each spotlight player's profile with the boxes we highlight."""
    from playwright.sync_api import sync_playwright
    p = Path(work) / "site.html"
    p.write_text(html)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": PROF_VW, "height": 932}, device_scale_factor=PROF_DPR,
                         locale="en-US", color_scheme="dark", reduced_motion="reduce")
        pg.add_init_script("try{localStorage.setItem('age18','1')}catch(e){}")
        pg.route(lambda u: not u.startswith("file:"), lambda r: r.abort())
        pg.goto(p.as_uri())
        pg.wait_for_function("typeof bestPicks === 'function' && typeof DATA !== 'undefined'", timeout=60000)
        pg.evaluate("(A) => { applyArchive(A); document.documentElement.setAttribute('data-theme', 'dark'); }", archive)
        names = pg.evaluate("(ms) => Object.fromEntries(ms.map(m => [m, mName(m)]))", sorted({r["m"] for r in rows}))
        for r in rows:
            r["mn"] = names.get(r["m"], r["m"])
        for sp in spots:
            box = pg.evaluate("""([gid, pid, m, tab]) => {
                const pr = P(pid); state.pvtab = tab;
                go({tab: 'games', game: String(gid), side: pr.team}); state.open = String(pid); render();
                const r = document.querySelector(`tr.row[data-id="${pid}"]`);
                if (!r || !r.nextElementSibling) return null;
                const d = r.nextElementSibling, a = r.getBoundingClientRect();
                const rel = b => [b.left, b.top - a.top + 8, b.width, b.height];
                const boxes = {};
                for (const b of d.querySelectorAll('.box')) {
                  const h = (b.textContent || '').trim().toLowerCase();
                  if (h.startsWith('prices')) boxes.prices = rel(b.getBoundingClientRect());
                  else if (h.startsWith('how the number')) boxes.built = rel(b.getBoundingClientRect());
                  else if (h.startsWith('skill or luck')) boxes.luck = rel(b.getBoundingClientRect());
                  else if (h.startsWith('game by game')) boxes.chart = rel(b.getBoundingClientRect());
                  else if (h.startsWith('last 10')) boxes.l10 = rel(b.getBoundingClientRect());
                }
                // the pick's own market row inside Prices & value (label span + the cells on the same line)
                const lab = [...d.querySelectorAll('span')].find(s => s.textContent.trim().replace(/[✓✗]/g, '').trim() === mName(m));
                if (lab) {
                  const lt = lab.getBoundingClientRect().top;
                  const cells = [...lab.parentElement.children].filter(e => Math.abs(e.getBoundingClientRect().top - lt) < 14 ||
                    (e.getBoundingClientRect().top < lt + 30 && e.getBoundingClientRect().bottom > lt + 4));
                  const rs = cells.map(e => e.getBoundingClientRect());
                  const L = Math.min(...rs.map(x => x.left)), T = Math.min(...rs.map(x => x.top)),
                        R = Math.max(...rs.map(x => x.right)), B = Math.max(...rs.map(x => x.bottom));
                  boxes.row = [L - 6, T - a.top + 8 - 6, R - L + 12, B - T + 12];
                }
                return {top: a.top + scrollY, bottom: d.getBoundingClientRect().bottom + scrollY, row: a.height, boxes};
            }""", [sp["g"], sp["pid"], sp["m"], PVTAB.get(sp["m"], "sh" if sp["m"][0] == "s" else "sc")])
            if not box:
                continue
            pg.wait_for_timeout(1200)
            h = min(box["bottom"] - box["top"] + 8, PROF_MAX)
            out = Path(work) / "img" / f"prof-{sp['pid']}.jpg"
            pg.screenshot(path=str(out), type="jpeg", quality=90, full_page=True,
                          clip={"x": 0, "y": max(0, box["top"] - 8), "width": PROF_VW, "height": h})
            sp["prof"] = {"src": "img/" + out.name, "w": PROF_VW, "h": h, "boxes": box["boxes"]}
        br.close()


def build(date, bp, archive, previews):
    et = ZoneInfo("America/New_York")
    res = archive.get("results") or {}
    games = {str(g["id"]): g for g in archive.get("games") or []}
    pv = {str(p["id"]): p for p in (previews or {}).get("previews") or []}
    rows = [r for r in bp["rows"] if r["d"] == date and r.get("r") in ("w", "l", "v")]
    def enrich(r):
        g = games.get(str(r["g"])) or {}
        R = res.get(str(r["g"])) or {}
        st = (R.get("players") or {}).get(str(r["pid"]))
        tm = r["t"]
        start = g.get("start")
        return {**r, "home": r["h"], "away": r["a"], "hs": R.get("hs", g.get("hs")), "as": R.get("as", g.get("as")),
                "last": R.get("last"), "st": st, "tc": TEAM.get(tm, "#3ddc97"), "hc": TEAM.get(r["h"], "#5aa9ff"),
                "ac": TEAM.get(r["a"], "#5aa9ff"), "home_name": g.get("home_name") or r["h"], "away_name": g.get("away_name") or r["a"],
                "time": dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(et).strftime("%-I:%M %p") if start else "",
                "photo": (pv.get(str(r["g"])) or {}).get("photo"), "slug": (pv.get(str(r["g"])) or {}).get("slug")}
    main = sorted([enrich(r) for r in rows if r["k"] == "all"], key=lambda r: ((games.get(str(r["g"])) or {}).get("start") or "", r["g"], r["rk"]))
    multi = sorted([enrich(r) for r in rows if r["k"] == "multi"], key=lambda r: ((games.get(str(r["g"])) or {}).get("start") or "", r["g"], r["rk"]))
    graded = [r for r in main if r["r"] in ("w", "l")]
    u = sum(r["u"] for r in graded)
    won = [r for r in graded if r["r"] == "w"]
    # long shots are only worth mentioning on a night one of them hit big
    if not any(r["r"] == "w" and r["od"] >= BIG_HIT for r in multi):
        multi = []
    summary = {"n": len(graded), "w": len(won), "l": len(graded) - len(won), "v": sum(r["r"] == "v" for r in main),
               "u": round(u, 2), "roi": (u / len(graded)) if graded else 0,
               "avg_win_odds": (sum(r["od"] for r in won) / len(won)) if won else None,
               "clv": (sum(r.get("clv") or 0 for r in graded) / len(graded)) if graded else 0,
               "mu": round(sum(r["u"] for r in multi if r["r"] in ("w", "l")), 2),
               "mw": sum(r["r"] == "w" for r in multi), "mn": sum(r["r"] in ("w", "l") for r in multi)}
    # profit spotlights: the biggest winners of the night, one per player
    spots, seen = [], set()
    for r in sorted(won, key=lambda r: -r["u"]):
        if r["pid"] in seen:
            continue
        seen.add(r["pid"])
        spots.append(dict(r))
        if len(spots) >= MAX_SPOTS:
            break
    day = dt.date.fromisoformat(date)
    return {"date": date, "day": day.strftime("%a · %b %-d").upper(), "dlong": day.strftime("%A, %B %-d"),
            "ngames": len({r["g"] for r in rows}), "picks": main, "multi": multi, "sum": summary, "spots": spots,
            "site": SITE}


def timing(spec):
    pb = pick_beats(len(spec["picks"]))
    t = {"beat": BEAT, "intro": INTRO, "pick": pb * BEAT, "multi": MULTI if spec["multi"] else 0,
         "summary": SUMMARY, "spot": SPOT, "outro": OUTRO}
    spec["timing"] = t
    return t


def duration(spec):
    t = spec["timing"]
    return t["intro"] + t["pick"] * len(spec["picks"]) + t["multi"] + t["summary"] + t["spot"] * len(spec["spots"]) + t["outro"]


def cues_and_wins(spec):
    t = spec["timing"]
    a = t["intro"]
    cues, wins = [a], []
    for i, r in enumerate(spec["picks"]):
        if r["r"] == "w":
            wins.append(a + i * t["pick"] + t["pick"] * 0.42)
    a += t["pick"] * len(spec["picks"])
    if t["multi"]:
        cues.append(a); a += t["multi"]
    cues.append(a); a += t["summary"]
    for _ in spec["spots"]:
        cues.append(a); a += t["spot"]
    cues.append(a)
    return cues, wins


def captions(spec):
    s = spec["sum"]
    head = f"Last night's NHL picks: {s['w']}-{s['l']}, {s['u']:+.2f} units ({spec['dlong']})"
    lines = []
    for r in spec["picks"]:
        mark = {"w": "✅", "l": "❌", "v": "➖"}[r["r"]]
        mn = r["mn"] if any(c.isdigit() for c in r["mn"]) else r["mn"].lower()
        lines.append(f"{mark} {r['n']} {mn} @ {r['od']:.2f} ({r['a']} @ {r['h']})")
    tags = {"#" + short_name(r["home_name"]).replace(" ", "") for r in spec["picks"]} | {"#" + short_name(r["away_name"]).replace(" ", "") for r in spec["picks"]}
    disc = ("Every pick is locked 5 minutes before puck drop and graded after the game, wins and losses, on the "
            "Track record page: " + SITE + ". 1 unit per pick. Past results don't guarantee future ones. 18+, play responsibly.")
    base = "#NHL #hockey #NHLBets #NHLProps #hockeytok #sportsbetting"
    return "\n\n".join(["TITLE (YouTube Shorts)\n" + head[:100],
                        "CAPTION (TikTok / Instagram)\n" + head + "\n\n" + "\n".join(lines) + "\n\n" + disc + "\n\n" + base + " " + " ".join(sorted(tags))]) + "\n"


def prep_images(spec, work):
    from PIL import Image, ImageEnhance, ImageFilter
    done = {}
    for r in spec["picks"] + spec["multi"] + spec["spots"]:
        ph = r.get("photo")
        if not ph:
            continue
        src = ROOT / "site-assets" / ph["src"].replace("assets/", "", 1)
        if not src.exists():
            continue
        key = r["slug"]
        if key not in done:
            im = Image.open(src).convert("RGB")
            im.thumbnail((1600, 1600))
            im.save(work / "img" / f"{key}.jpg", quality=92)
            b = im.resize((270, round(im.height * 270 / im.width)), Image.LANCZOS).filter(ImageFilter.GaussianBlur(6))
            b = ImageEnhance.Brightness(ImageEnhance.Color(b).enhance(1.2)).enhance(0.75)
            b.save(work / "img" / f"{key}-bg.jpg", quality=90)
            done[key] = {"src": f"img/{key}.jpg", "bsrc": f"img/{key}-bg.jpg", "credit": f"Photo: {ph.get('author', '')} · {ph.get('license', '')}"}
        r.update(done[key])


def render(spec, out_dir, work, preview_frames=None, fps=FPS):
    out_dir = Path(out_dir)
    shutil.copytree(HERE / "fonts", work / "fonts", dirs_exist_ok=True)
    icon = ROOT / "site-assets" / "icons" / "icon-512.png"
    if icon.exists():
        shutil.copy(icon, work / "img" / "icon.png")
    prep_images(spec, work)
    (work / "index.html").write_text((HERE / "review.html").read_text().replace("/*SPEC*/null", json.dumps(spec)))
    total = duration(spec)
    base = out_dir / f"{spec['date']}-nhl-review"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--allow-file-access-from-files"])
        page = br.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto((work / "index.html").as_uri())
        page.wait_for_function("window.READY === true", timeout=60000)
        if preview_frames:
            for t in preview_frames:
                page.evaluate(f"render({t})")
                page.screenshot(path=f"{base}-t{t:05.2f}.jpg", type="jpeg", quality=90)
            br.close()
            return
        wav = work / "music.wav"
        cues, wins = cues_and_wins(spec)
        music.write_wav(str(wav), music.review_track(total, cues=cues, wins=wins, bpm=BPM))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
               "-i", "-", "-i", str(wav), "-map", "0:v", "-map", "1:a",
               "-vf", "noise=alls=3:allf=t", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
               "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M",
               "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(fps),
               "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", f"{base}.mp4"]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        cdp = page.context.new_cdp_session(page)
        nframes = int(round(total * fps))
        t0 = time.time()
        for f in range(nframes):
            page.evaluate(f"render({f / fps})")
            r = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 93, "optimizeForSpeed": True})
            ff.stdin.write(base64.b64decode(r["data"]))
            if f % 150 == 0:
                print(f"  frame {f}/{nframes}  {time.time() - t0:.0f}s", flush=True)
        ff.stdin.close()
        ff.wait()
        page.evaluate(f"render({spec['timing']['intro'] * 0.92})")
        page.screenshot(path=str(out_dir / f"{spec['date']}-review-cover.jpg"), type="jpeg", quality=92)
        br.close()
    if ff.returncode:
        raise SystemExit("ffmpeg failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(dt.datetime.now(ZoneInfo("America/New_York")).date() - dt.timedelta(days=1)).isoformat())
    ap.add_argument("--site", help="built site page (default: origin/site:app/index.html)")
    ap.add_argument("--out", default=str(ROOT / "video" / "out"))
    ap.add_argument("--preview", help="comma-separated times: write still frames only")
    a = ap.parse_args()
    html = Path(a.site).read_text() if a.site else git_show("app/index.html")
    bp = json.loads(git_show("data/bestpicks.json") or "null")
    arch = json.loads(git_show(f"data/archive/{a.date}.json") or "null")
    if not html or not bp or not arch:
        raise SystemExit("site, track record or archive for this date is missing on origin/site (git fetch origin site)")
    pvp = ROOT / "data" / "previews" / f"{a.date}.json"
    spec = build(a.date, bp, arch, json.load(open(pvp)) if pvp.exists() else None)
    if not spec["picks"]:
        raise SystemExit(f"no graded picks for {a.date} in the track record")
    timing(spec)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "img").mkdir()
        site_pass(html, arch, spec["picks"] + spec["multi"] + spec["spots"], spec["spots"], work)
        spec["spots"] = [s for s in spec["spots"] if s.get("prof")]
        (out / f"{a.date}-review-captions.txt").write_text(captions(spec))
        s = spec["sum"]
        print(f"{a.date}: {s['w']}-{s['l']} {s['u']:+.2f}u, {len(spec['picks'])} picks, {len(spec['multi'])} long shots, "
              f"spotlights: {', '.join(x['n'] for x in spec['spots']) or '-'}")
        render(spec, out, work, preview_frames=[float(x) for x in a.preview.split(",")] if a.preview else None)
    print(f"done: {duration(spec):.0f}s -> {out}")


if __name__ == "__main__":
    main()
