"""Render the daily "best bets" video (9:16, for TikTok / Instagram Reels / YouTube Shorts).

    python video/render.py --date 2026-10-28 [--site path/to/app/index.html] [--out out_dir] [--preview 2,5,9]

Numbers come from the built site itself: the page (origin/site:app/index.html unless --site is given) is
opened in headless Chromium and video/extract.js calls the site's own functions (bestPicks, pickReasons,
prob, book odds, public splits, futures). Headlines and photos come from data/previews/<DATE>.json and
site-assets/previews/.

Output: <out>/<DATE>-nhl-best-bets.mp4, <DATE>-cover.jpg, <DATE>-captions.txt
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

W, H, FPS, BPM = 1080, 1920, 30, 120
BEAT = 60 / BPM
INTRO = 8 * BEAT          # 4 s
GAME = 32 * BEAT          # 16 s: story 3 s, team table + zoom 3.5 s, site profile scroll 6.5 s, bet + game numbers 3 s
OUTRO = 16 * BEAT         # 8 s: features, then brand
MAX_GAMES = 4
MIN_PHOTO_W = 3000        # originals at least this wide count as "very high quality"
SITE = "nhl-props-lab.vercel.app"

TEAM = {
    "ANA": "#F47A38", "BOS": "#FFB81C", "BUF": "#2a5cc9", "CAR": "#CE1126", "CBJ": "#2a5bb5", "CGY": "#D2001C",
    "CHI": "#CF0A2C", "COL": "#8a2f50", "DAL": "#00a36c", "DET": "#CE1126", "EDM": "#FF4C00", "FLA": "#C8102E",
    "LAK": "#A2AAAD", "MIN": "#1f7a52", "MTL": "#AF1E2D", "NJD": "#CE1126", "NSH": "#FFB81C", "NYI": "#1f6fd1",
    "NYR": "#2a5fd8", "OTT": "#DA1A32", "PHI": "#F74902", "PIT": "#FCB514", "SEA": "#5fc4c4", "SJS": "#00a3ad",
    "STL": "#2a5fd8", "TBL": "#2a5cc9", "TOR": "#2a5fd8", "UTA": "#71AFE5", "VAN": "#1b8a4f", "VGK": "#B4975A",
    "WPG": "#3a6fc4", "WSH": "#C8102E",
}  # brightened where the official colour is too dark on the black background


def short_name(full):
    for t in ("Maple Leafs", "Red Wings", "Blue Jackets", "Golden Knights"):
        if full.endswith(t):
            return t
    return full.split()[-1] if full else ""


def site_html(path=None):
    if path:
        return Path(path).read_text()
    return subprocess.run(["git", "show", "origin/site:app/index.html"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout


def extract(date, html, work):
    """Run video/extract.js inside the built site page and return its JSON."""
    from playwright.sync_api import sync_playwright
    p = Path(work) / "site.html"
    p.write_text(html)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(locale="en-US")
        pg.route(lambda u: not u.startswith("file:"), lambda r: r.abort())
        pg.goto(p.as_uri())
        pg.wait_for_function("typeof bestPicks === 'function' && typeof DATA !== 'undefined'", timeout=60000)
        out = pg.evaluate((HERE / "extract.js").read_text(), date)
        br.close()
    return out


PROF_VW, PROF_DPR, PROF_MAX = 430, 2.5, 2600   # phone-sized site viewport (CSS px), pixel ratio, max capture height


def capture_profiles(spec, html, work):
    """Open each chosen best-pick player's profile on the real site (dark theme, phone width) and save a tall
    screenshot of it, from his table row down. The video scrolls through it inside a phone frame."""
    from playwright.sync_api import sync_playwright
    p = Path(work) / "site.html"
    p.write_text(html)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": PROF_VW, "height": 932}, device_scale_factor=PROF_DPR,
                         locale="en-US", color_scheme="dark", reduced_motion="reduce")
        pg.route(lambda u: not u.startswith("file:"), lambda r: r.abort())
        pg.goto(p.as_uri())
        pg.wait_for_function("typeof bestPicks === 'function' && typeof DATA !== 'undefined'", timeout=60000)
        for x in spec["games"]:
            pid, team = x["pick"]["id"], x["pick"]["team"]
            box = pg.evaluate("""([gid, team, pid]) => {
                document.documentElement.setAttribute('data-theme', 'dark');
                go({tab: 'games', game: String(gid), side: team}); state.open = String(pid); render();
                const r = document.querySelector(`tr.row[data-id="${pid}"]`);
                if (!r || !r.nextElementSibling) return null;
                const a = r.getBoundingClientRect(), b = r.nextElementSibling.getBoundingClientRect();
                const c = r.nextElementSibling.querySelector('.l10box')?.getBoundingClientRect();
                return {top: a.top + scrollY, bottom: b.bottom + scrollY, row: a.height,
                        chart: c ? [c.top - a.top + 8, c.bottom - a.top + 8] : null};
            }""", [x["id"], team, pid])
            if not box:
                continue
            pg.wait_for_timeout(1200)  # charts settle
            h = min(box["bottom"] - box["top"] + 8, PROF_MAX)
            out = Path(work) / "img" / (x["slug"] + "-prof.jpg")
            pg.screenshot(path=str(out), type="jpeg", quality=90, full_page=True,
                          clip={"x": 0, "y": max(0, box["top"] - 8), "width": PROF_VW, "height": h})
            x["prof"] = {"src": "img/" + out.name, "w": PROF_VW, "h": h, "row": box["row"], "chart": box["chart"]}
        br.close()


def build_spec(date, ext, previews):
    et = ZoneInfo("America/New_York")
    credits = {}
    cp = ROOT / "site-assets" / "previews" / "credits.json"
    if cp.exists():
        credits = json.load(open(cp))
    pvs = {p["id"]: p for p in previews["previews"]}
    cands = []
    for g in ext["games"]:
        pv = pvs.get(g["id"])
        if not pv or not g["picks"]:
            continue
        photo = pv.get("photo") or {}
        img = ROOT / "site-assets" / (photo.get("src", "").replace("assets/", "", 1))
        if not photo or not img.exists():
            continue
        cr = credits.get(pv["slug"], {})
        w, h = cr.get("w", 0), cr.get("h", 1)
        pick = g["picks"][0]
        start = dt.datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(et)
        names = {short_name(g["home_name"]), short_name(g["away_name"])} | {r["name"].split()[-1] for r in g["table"]}
        cands.append({
            **g, "slug": pv["slug"], "title": pv["en"]["title"], "kicker": pv["en"]["kicker"],
            "time": start.strftime("%-I:%M %p ET"), "hc": TEAM.get(g["home"], "#3ddc97"), "ac": TEAM.get(g["away"], "#5aa9ff"),
            "home_short": short_name(g["home_name"]), "away_short": short_name(g["away_name"]),
            "names": sorted(n for n in names if len(n) > 2),
            "img": str(img), "photo_w": w, "photo_q": (w >= MIN_PHOTO_W and w > h),
            "credit": "Photo: %s · %s" % (photo.get("author", "Wikimedia Commons"), photo.get("license", "")),
            "pick": pick, "pick_score": pick["ev"] if pick["ev"] is not None else pick["v"] - 1,
        })
    # very high quality photos first, then the strongest best bet; keep 3-4 games, shown in start order
    hq = [c for c in cands if c["photo_q"]]
    pool = hq if len(hq) >= 3 else sorted(cands, key=lambda c: -c["photo_w"])[:max(3, len(hq))]
    chosen = sorted(pool, key=lambda c: -c["pick_score"])[:MAX_GAMES]
    chosen.sort(key=lambda c: c["start"])
    day = dt.date.fromisoformat(date)
    fut = ext.get("futures") or {}
    cup = None
    if fut.get("cup"):
        t, p, name = fut["cup"][0]
        o = (fut.get("cup_odds") or {}).get(t)
        cup = {"team": t, "name": name, "p": p, "implied": (1 / o) if o else None}
    return {
        "date": date, "day": day.strftime("%a · %b %-d").upper(), "n": len(ext["games"]),
        "games": chosen, "site": SITE, "cup": cup,
        "timing": {"intro": INTRO, "game": GAME, "outro": OUTRO, "beat": BEAT},
    }


def duration(spec):
    t = spec["timing"]
    return t["intro"] + t["game"] * len(spec["games"]) + t["outro"]


def cues(spec):
    t = spec["timing"]
    return [t["intro"] + i * t["game"] for i in range(len(spec["games"]))] + [t["intro"] + t["game"] * len(spec["games"])]


def captions(spec):
    g = spec["games"]
    tags = set()
    for x in g:
        tags.add("#" + x["home_short"].replace(" ", ""))
        tags.add("#" + x["away_short"].replace(" ", ""))
    def bet(x):
        p = x["pick"]
        odds = (" @ %.2f" % p["od"]) if p["od"] else ""
        return "• %s @ %s: %s %s%s (model %d%%)" % (x["away"], x["home"], p["name"], p["market"] if any(c.isdigit() for c in p["market"]) else p["market"].lower(), odds, round(p["v"] * 100))
    title = "NHL best bets tonight: what the model likes vs the market (%s)" % spec["day"].title()
    body = "\n".join(bet(x) for x in g)
    disc = "Model numbers, not betting advice. 18+, play responsibly. Every player, shots, hits, lines and playoff odds: " + SITE
    base = "#NHL #hockey #NHLBets #NHLProps #hockeytok #sportsbetting"
    return "\n\n".join([
        "TITLE (YouTube Shorts)\n" + title[:100],
        "CAPTION (TikTok / Instagram)\n" + title + "\n\n" + body + "\n\n" + disc + "\n\n" + base + " " + " ".join(sorted(tags)),
    ]) + "\n"


def prep_images(spec, work):
    from PIL import Image, ImageEnhance, ImageFilter
    for x in spec["games"]:
        im = Image.open(x["img"]).convert("RGB")
        im.thumbnail((2000, 2000))
        im.save(work / "img" / (x["slug"] + ".jpg"), quality=93)
        x["src"] = "img/" + x["slug"] + ".jpg"
        bw = 270  # pre-blurred background (cheaper than a CSS blur on every frame)
        b = im.resize((bw, round(im.height * bw / im.width)), Image.LANCZOS).filter(ImageFilter.GaussianBlur(6))
        b = ImageEnhance.Brightness(ImageEnhance.Color(b).enhance(1.25)).enhance(0.8)
        b.save(work / "img" / (x["slug"] + "-bg.jpg"), quality=90)
        x["bsrc"] = "img/" + x["slug"] + "-bg.jpg"


def render(spec, out_dir, work, fps=FPS, preview_frames=None):
    out_dir = Path(out_dir)
    (work / "img").mkdir(parents=True, exist_ok=True)
    shutil.copytree(HERE / "fonts", work / "fonts", dirs_exist_ok=True)
    icon = ROOT / "site-assets" / "icons" / "icon-512.png"
    if icon.exists():
        shutil.copy(icon, work / "img" / "icon.png")
    prep_images(spec, work)
    (work / "index.html").write_text((HERE / "template.html").read_text().replace("/*SPEC*/null", json.dumps(spec)))
    total = duration(spec)
    base = out_dir / f"{spec['date']}-nhl-best-bets"
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
        music.write_wav(str(wav), music.track(total, cues=cues(spec), bpm=BPM))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
               "-i", "-", "-i", str(wav), "-map", "0:v", "-map", "1:a",
               "-vf", "noise=alls=4:allf=t", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
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
        page.evaluate(f"render({spec['timing']['intro'] * 0.9})")
        page.screenshot(path=str(out_dir / f"{spec['date']}-cover.jpg"), type="jpeg", quality=92)
        br.close()
    if ff.returncode:
        raise SystemExit("ffmpeg failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat())
    ap.add_argument("--site", help="built site page (default: origin/site:app/index.html)")
    ap.add_argument("--out", default=str(ROOT / "video" / "out"))
    ap.add_argument("--preview", help="comma-separated times: write still frames only")
    a = ap.parse_args()
    pv_path = ROOT / "data" / "previews" / f"{a.date}.json"
    if not pv_path.exists():
        raise SystemExit(f"no previews for {a.date}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        html = site_html(a.site)
        ext = extract(a.date, html, work)
        spec = build_spec(a.date, ext, json.load(open(pv_path)))
        if not spec["games"]:
            raise SystemExit("no game has both a preview photo and a best pick")
        (work / "img").mkdir(parents=True, exist_ok=True)
        capture_profiles(spec, html, work)
        (out / f"{a.date}-captions.txt").write_text(captions(spec))
        print("games:", ", ".join(f"{g['away']}@{g['home']} ({g['pick']['name']} {g['pick']['market']})" for g in spec["games"]))
        render(spec, out, work, preview_frames=[float(x) for x in a.preview.split(",")] if a.preview else None)
    print(f"done: {duration(spec):.0f}s, {len(spec['games'])} games -> {out}")


if __name__ == "__main__":
    main()
