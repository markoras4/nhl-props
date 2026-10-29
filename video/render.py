"""Render the daily preview video (9:16, for TikTok / Instagram Reels / YouTube Shorts).

    python video/render.py --date 2026-10-28 [--data site_data.json] [--out out_dir]

Inputs:  data/previews/<DATE>.json, site-assets/previews/<slug>.webp, and the built site
data (games, model, odds), read from `git show origin/site:app/index.html` unless --data
is given.
Output:  <out>/<DATE>-nhl-previews.mp4, <out>/<DATE>-cover.jpg, <out>/<DATE>-captions.txt
"""
import argparse
import datetime as dt
import json
import os
import re
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
INTRO = 8 * BEAT          # 4.0 s
GAME = 18 * BEAT          # 9.0 s
BOARD = 12 * BEAT         # 6.0 s
OUTRO = 10 * BEAT         # 5.0 s
MAX_FEATURED = 5
SITE = "nhl-props-lab.vercel.app"

# Primary / secondary colours (hex) per team, used for bars and accents only (no logos).
TEAM = {
    "ANA": ("#F47A38", "#B9975B"), "BOS": ("#FFB81C", "#000000"), "BUF": ("#003087", "#FFB81C"),
    "CAR": ("#CE1126", "#A2AAAD"), "CBJ": ("#002654", "#CE1126"), "CGY": ("#D2001C", "#FAAF19"),
    "CHI": ("#CF0A2C", "#000000"), "COL": ("#6F263D", "#236192"), "DAL": ("#006847", "#8F8F8C"),
    "DET": ("#CE1126", "#FFFFFF"), "EDM": ("#FF4C00", "#041E42"), "FLA": ("#C8102E", "#041E42"),
    "LAK": ("#A2AAAD", "#111111"), "MIN": ("#154734", "#A6192E"), "MTL": ("#AF1E2D", "#192168"),
    "NJD": ("#CE1126", "#000000"), "NSH": ("#FFB81C", "#041E42"), "NYI": ("#00539B", "#F47D30"),
    "NYR": ("#0038A8", "#CE1126"), "OTT": ("#DA1A32", "#B79257"), "PHI": ("#F74902", "#000000"),
    "PIT": ("#FCB514", "#000000"), "SEA": ("#99D9D9", "#001628"), "SJS": ("#006D75", "#EA7200"),
    "STL": ("#002F87", "#FCB514"), "TBL": ("#002868", "#FFFFFF"), "TOR": ("#00205B", "#FFFFFF"),
    "UTA": ("#71AFE5", "#090909"), "VAN": ("#00205B", "#00843D"), "VGK": ("#B4975A", "#333F42"),
    "WPG": ("#041E42", "#AC162C"), "WSH": ("#C8102E", "#041E42"),
}


def nv(a, b):
    return (1 / a) / ((1 / a) + (1 / b))


def load_site(path=None):
    if path:
        return json.load(open(path))
    html = subprocess.run(["git", "show", "origin/site:app/index.html"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout
    i = html.index("const DATA=") + len("const DATA=")
    return json.JSONDecoder().raw_decode(html[i:])[0]


def short_name(full):
    # "Philadelphia Flyers" -> "Flyers", "Toronto Maple Leafs" -> "Maple Leafs"
    two = ("Maple Leafs", "Red Wings", "Blue Jackets", "Golden Knights")
    for t in two:
        if full.endswith(t):
            return t
    return full.split()[-1] if full else ""


def first_sentence(text, limit=125):
    s = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    return s if len(s) <= limit else None


def surname(name):
    return name.split()[-1]


def build_spec(date, site, previews):
    games = {g["id"]: g for g in site["games"] if g.get("date") == date}
    P = site.get("players", {})
    et = ZoneInfo("America/New_York")
    items = []
    for pv in previews["previews"]:
        g = games.get(pv["id"])
        if not g:
            continue
        en = pv["en"]
        d = g["default"]
        o = (site.get("odds") or {}).get(str(g["id"])) or {}
        home, away = g["home"], g["away"]
        start = dt.datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(et)
        win = o.get("winner") or {}
        mk_home = nv(win["home"], win["away"]) if win.get("home") and win.get("away") else None
        tot = (o.get("totals") or {}).get("5.5") or {}
        mk_over = nv(tot["over"], tot["under"]) if tot.get("over") and tot.get("under") else None
        # top model chance (1+ point) among active players
        best = None
        for p in g.get("props", []):
            pl = P.get(str(p["id"]))
            if not pl or not pl.get("active") or (pl.get("injury") or None):
                continue
            if best is None or p["p"]["pt"] > best[1]:
                best = (pl["name"], p["p"]["pt"], p["team"])
        names = {short_name(g.get("home_name", "")), short_name(g.get("away_name", ""))}
        for p in g.get("props", []):
            pl = P.get(str(p["id"]))
            if pl:
                names.add(surname(pl["name"]))
        photo = pv.get("photo") or {}
        img = ROOT / "site-assets" / (photo.get("src", "").replace("assets/", "", 1))
        gap = (d["p_home"] - mk_home) if mk_home is not None else 0.0
        items.append({
            "slug": pv["slug"], "home": home, "away": away,
            "home_name": short_name(g.get("home_name", home)), "away_name": short_name(g.get("away_name", away)),
            "hc": TEAM.get(home, ("#3ddc97", "#fff"))[0], "ac": TEAM.get(away, ("#5aa9ff", "#fff"))[0],
            "time": start.strftime("%-I:%M %p ET"),
            "kicker": en["kicker"], "title": en["title"],
            "deck1": first_sentence(en.get("deck", "")),
            "names": sorted(n for n in names if n and len(n) > 2),
            "p_home": d["p_home"], "mk_home": mk_home, "gap": gap,
            "exp_total": d["exp_total"], "over": d["over_5.5"], "mk_over": mk_over,
            "player": {"name": best[0], "p": best[1], "team": best[2]} if best else None,
            "img": str(img) if img.exists() else None,
            "credit": "Photo: %s · %s" % (photo.get("author", "Wikimedia Commons"), photo.get("license", "")),
            "start": g["start"],
        })
    items.sort(key=lambda x: x["start"])
    featured = items
    rest = []
    if len(items) > MAX_FEATURED:
        ranked = sorted(items, key=lambda x: -abs(x["gap"]))
        keep = {x["slug"] for x in ranked[:MAX_FEATURED]}
        featured = [x for x in items if x["slug"] in keep]
        rest = [x for x in items if x["slug"] not in keep]
    split = sum(1 for x in items if abs(x["gap"]) >= 0.06)
    day = dt.date.fromisoformat(date)
    n = len(items)
    hook = ("THE MODEL SPLITS WITH THE MARKET ON %d GAME%s" % (split, "" if split == 1 else "S")) if split else "WHERE THE MODEL STANDS"
    return {
        "date": date, "day": day.strftime("%a · %b %-d").upper(), "n": n,
        "hook": hook, "split": split,
        "games": featured, "rest": rest, "site": SITE,
        "timing": {"intro": INTRO, "game": GAME, "board": BOARD if rest else 0, "outro": OUTRO, "beat": BEAT},
    }


def duration(spec):
    t = spec["timing"]
    return t["intro"] + t["game"] * len(spec["games"]) + t["board"] + t["outro"]


def cues(spec):
    t = spec["timing"]
    c = [t["intro"] + i * t["game"] for i in range(len(spec["games"]))]
    if t["board"]:
        c.append(t["intro"] + t["game"] * len(spec["games"]))
    return c


def captions(spec):
    g = spec["games"] + spec["rest"]
    lines = [x["away"] + " @ " + x["home"] + "  " + x["time"] for x in g]
    tags = set()
    for x in g:
        tags.add("#" + x["home_name"].replace(" ", ""))
        tags.add("#" + x["away_name"].replace(" ", ""))
    base = "#NHL #hockey #NHLPreview #hockeytok #icehockey"
    title = "%d NHL games tonight: what the model says vs the market (%s)" % (spec["n"], spec["day"].title())
    body = "\n".join("• " + x["kicker"] + ": " + x["title"] for x in g)
    disc = "Model numbers, not betting advice. 18+. Full previews: " + SITE
    return "\n\n".join([
        "TITLE (YouTube Shorts)\n" + title[:100],
        "CAPTION (TikTok / Instagram)\n" + title + "\n\n" + body + "\n\n" + disc + "\n\n" + base + " " + " ".join(sorted(tags)),
        "GAMES\n" + "\n".join(lines),
    ]) + "\n"


def render(spec, out_mp4, workdir, fps=FPS, preview_frames=None):
    work = Path(workdir)
    (work / "img").mkdir(parents=True, exist_ok=True)
    shutil.copytree(HERE / "fonts", work / "fonts", dirs_exist_ok=True)
    icon = ROOT / "site-assets" / "icons" / "icon-512.png"
    if icon.exists():
        shutil.copy(icon, work / "img" / "icon.png")
    for x in spec["games"] + spec["rest"]:
        if x["img"]:
            dst = work / "img" / (x["slug"] + Path(x["img"]).suffix)
            from PIL import Image, ImageEnhance, ImageFilter
            im = Image.open(x["img"]).convert("RGB")
            im.thumbnail((1600, 1600))
            im.save(dst.with_suffix(".jpg"), quality=92)
            x["src"] = "img/" + dst.with_suffix(".jpg").name
            # pre-blurred, slightly darker/saturated background (cheaper than CSS filters per frame)
            bw = 270
            bgi = im.resize((bw, round(im.height * bw / im.width)), Image.LANCZOS)
            bgi = bgi.filter(ImageFilter.GaussianBlur(6))
            bgi = ImageEnhance.Color(bgi).enhance(1.25)
            bgi = ImageEnhance.Brightness(bgi).enhance(0.8)
            bgi.save(work / "img" / (x["slug"] + "-bg.jpg"), quality=90)
            x["bsrc"] = "img/" + x["slug"] + "-bg.jpg"
        else:
            x["src"] = x["bsrc"] = None
    html = (HERE / "template.html").read_text().replace("/*SPEC*/null", json.dumps(spec))
    (work / "index.html").write_text(html)

    total = duration(spec)
    nframes = int(round(total * fps))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--disable-web-security", "--allow-file-access-from-files"])
        page = br.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto((work / "index.html").as_uri())
        page.evaluate("document.fonts.ready.then(()=>true)")
        page.wait_for_function("window.READY === true", timeout=60000)
        if preview_frames:
            for t in preview_frames:
                page.evaluate(f"render({t})")
                page.screenshot(path=str(Path(out_mp4).with_suffix("")) + f"-t{t:05.2f}.jpg", type="jpeg", quality=90)
            br.close()
            return
        wav = work / "music.wav"
        music.write_wav(str(wav), music.track(total, cues=cues(spec), bpm=BPM))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
               "-i", "-", "-i", str(wav), "-map", "0:v", "-map", "1:a",
               "-vf", "noise=alls=5:allf=t", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
               "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p", "-profile:v", "high",
               "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out_mp4)]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        cdp = page.context.new_cdp_session(page)
        import base64

        def shot():
            r = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 93, "optimizeForSpeed": True})
            return base64.b64decode(r["data"])
        t0 = time.time()
        for f in range(nframes):
            page.evaluate(f"render({f / fps})")
            ff.stdin.write(shot())
            if f % 150 == 0:
                print(f"  frame {f}/{nframes}  {time.time() - t0:.0f}s", flush=True)
        ff.stdin.close()
        ff.wait()
        # cover image: the hook frame
        page.evaluate(f"render({spec['timing']['intro'] * 0.62})")
        page.screenshot(path=str(Path(out_mp4).parent / (spec["date"] + "-cover.jpg")), type="jpeg", quality=92)
        br.close()
    if ff.returncode:
        raise SystemExit("ffmpeg failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat())
    ap.add_argument("--data", help="site DATA json (default: origin/site)")
    ap.add_argument("--out", default=str(ROOT / "video" / "out"))
    ap.add_argument("--preview", help="comma-separated times: write still frames only")
    a = ap.parse_args()
    pv_path = ROOT / "data" / "previews" / f"{a.date}.json"
    if not pv_path.exists():
        raise SystemExit(f"no previews for {a.date}")
    spec = build_spec(a.date, load_site(a.data), json.load(open(pv_path)))
    if not spec["games"]:
        raise SystemExit("no games matched the site data")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    mp4 = out / f"{a.date}-nhl-previews.mp4"
    (out / f"{a.date}-captions.txt").write_text(captions(spec))
    with tempfile.TemporaryDirectory() as td:
        pf = [float(x) for x in a.preview.split(",")] if a.preview else None
        render(spec, mp4, td, preview_frames=pf)
    print("wrote", mp4 if not a.preview else out, f"({duration(spec):.1f}s, {len(spec['games'])} games)")


if __name__ == "__main__":
    main()
