"""The free-licence game-preview photos (Wikimedia Commons, site-assets/previews/<slug>.webp, credits in
data/previews/<date>.json and site-assets/previews/credits.json) as video backgrounds, plus their attribution text.

No photographer credit is drawn on screen: CC licences let the attribution sit wherever is reasonable for the medium,
so it goes in the YouTube description and the TikTok caption (credit_lines). Images are cropped to the target size
(cover, focal point from the preview's CSS object-position), never stretched, darkened, with blurred edges; a
pre-blurred copy serves as a soft backdrop (no CSS blur in the templates).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "site-assets"


def _git_json(spec: str):
    try:
        return json.loads(subprocess.run(["git", "show", spec], cwd=ROOT, capture_output=True, check=True).stdout)
    except Exception:  # noqa: BLE001
        return None


def game_photos(date: str) -> dict:
    """{game id (str): photo} for the previews of one New York date; only photos whose file exists."""
    f = ROOT / "data" / "previews" / f"{date}.json"
    pv = None
    if f.exists():
        try:
            pv = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pv = None
    pv = pv or _git_json(f"origin/main:data/previews/{date}.json") or {}
    credits = {}
    cp = ASSETS / "previews" / "credits.json"
    if cp.exists():
        try:
            credits = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    out = {}
    for p in pv.get("previews") or []:
        ph = p.get("photo") or {}
        src = ph.get("src") or ""
        img = ASSETS / src.replace("assets/", "", 1) if src else None
        if not img or not img.is_file():
            continue
        cr = credits.get(p.get("slug"), {})
        parts = (p.get("slug") or "").split("-")
        out[str(p["id"])] = {"id": str(p["id"]), "slug": p["slug"], "img": img, "pos": ph.get("pos") or "center",
                             "author": ph.get("author") or cr.get("author") or "Wikimedia Commons",
                             "license": ph.get("license") or cr.get("license") or "",
                             "license_url": ph.get("license_url") or cr.get("license_url") or "",
                             "page": ph.get("page") or cr.get("page") or "",
                             "title": cr.get("title") or "", "teams": [x.upper() for x in parts[3:5]]}
    return out


def _focus(pos: str) -> tuple[float, float]:
    """CSS object-position ('center 10%', 'left top', '30% 40%') -> (fx, fy) in 0..1."""
    words = {"left": 0.0, "top": 0.0, "center": .5, "right": 1.0, "bottom": 1.0}
    xs = (pos or "center").split()
    vals = []
    for w in xs[:2]:
        m = re.match(r"^(-?\d+(?:\.\d+)?)%$", w)
        vals.append(float(m.group(1)) / 100 if m else words.get(w, .5))
    if len(vals) == 1:
        vals.append(.5)
    if xs and xs[0] in ("top", "bottom"):
        vals = [vals[1] if len(vals) > 1 else .5, vals[0]]
    return min(1, max(0, vals[0])), min(1, max(0, vals[1]))


def cover(photo: dict, size: tuple[int, int]):
    """PIL image cropped (never stretched) to size around the photo's focal point."""
    from PIL import Image
    im = Image.open(photo["img"]).convert("RGB")
    W, H = size
    s = max(W / im.width, H / im.height)
    w, h = round(im.width * s), round(im.height * s)
    im = im.resize((w, h), Image.LANCZOS)
    fx, fy = _focus(photo.get("pos", "center"))
    x = round((w - W) * fx)
    y = round((h - H) * fy)
    return im.crop((x, y, x + W, y + H))


def hero(photo: dict, out: Path, size: tuple[int, int], dark: float = .55, edge: float = .55) -> str:
    """Darkened photo with soft (blurred) edges, a sharp centre; JPEG at out."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    im = cover(photo, size)
    W, H = size
    blur = im.filter(ImageFilter.GaussianBlur(max(6, W // 90)))
    mask = Image.new("L", (W // 8, H // 8), 0)
    d = ImageDraw.Draw(mask)
    mw, mh = mask.size
    d.ellipse((mw * (1 - edge) / 2 - mw * .15, mh * (1 - edge) / 2 - mh * .2, mw - mw * (1 - edge) / 2 + mw * .15, mh - mh * (1 - edge) / 2 + mh * .2), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(2, mw // 10))).resize((W, H), Image.LANCZOS)
    im = Image.composite(im, blur, mask)
    im = ImageEnhance.Color(im).enhance(.9)
    im = ImageEnhance.Brightness(im).enhance(dark)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=88)
    return str(out)


def backdrop(photo: dict, out: Path, size: tuple[int, int], dark: float = .5) -> str:
    """Heavily blurred, darkened copy (soft scene background)."""
    from PIL import Image, ImageEnhance, ImageFilter
    W, H = size
    small = cover(photo, (max(8, W // 8), max(8, H // 8))).filter(ImageFilter.GaussianBlur(5))
    im = ImageEnhance.Brightness(small.resize((W // 2, H // 2), Image.BICUBIC)).enhance(dark)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=85)
    return str(out)


def credit_lines(photos: list) -> dict:
    """Attribution for the photos a video used: {'long': for YouTube descriptions (author, licence, source link),
    'short': one line for TikTok captions}. Empty strings when no photo was used."""
    seen, ps = set(), []
    for p in photos:
        if p and p["slug"] not in seen:
            seen.add(p["slug"]); ps.append(p)
    if not ps:
        return {"long": "", "short": ""}
    long = "Photos (Wikimedia Commons, cropped and darkened):\n" + "\n".join(
        f"• {p['author']}, {p['license']}" + (f" ({p['license_url']})" if p.get("license_url") else "") + (f": {p['page']}" if p.get("page") else "")
        for p in ps)
    by = {}                                              # one entry per photographer: "Jenn G (CC BY-SA 2.0 / 4.0)"
    for p in ps:
        ls = by.setdefault(p["author"], [])
        if p["license"] not in ls:
            ls.append(p["license"])
    def lic(ls):
        pre = ls[0].rsplit(" ", 1)[0]
        return pre + " " + " / ".join(x.rsplit(" ", 1)[-1] for x in ls) if all(x.rsplit(" ", 1)[0] == pre for x in ls) else ", ".join(ls)
    short = "Photos: " + ", ".join(f"{a} ({lic(ls)})" for a, ls in by.items()) + " via Wikimedia Commons, edited"
    return {"long": long, "short": short}
