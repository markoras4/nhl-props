"""Find free-licence photos on Wikimedia Commons for game previews.

    python -m nhlprops.photos candidates queries.json out_dir   # download candidates for review
    python -m nhlprops.photos fetch picks.json site-assets/previews  # download chosen files

Only CC0, public domain, CC BY and CC BY-SA files are accepted. Credits (author, licence,
source page) are kept with every file so the site can show them.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
UA = "NHLPropsLab/1.0 (personal hockey analytics site; contact via GitHub markoras4)"
OK_LICENSES = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? ?\d(\.\d)?.*)$", re.I)


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _plain(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def search(query: str, limit: int = 20, width: int = 1400) -> list[dict]:
    js = _get({"action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
               "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
               "iiprop": "url|size|extmetadata", "iiurlwidth": width})
    out = []
    for p in (js.get("query") or {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        lic = _plain((md.get("LicenseShortName") or {}).get("value"))
        if not OK_LICENSES.match(lic):
            continue
        w, h = ii.get("width", 0), ii.get("height", 0)
        date = _plain((md.get("DateTimeOriginal") or {}).get("value"))[:10]
        out.append({
            "title": p.get("title"), "w": w, "h": h, "thumb": ii.get("thumburl"), "page": ii.get("descriptionurl"),
            "license": lic, "license_url": _plain((md.get("LicenseUrl") or {}).get("value")),
            "author": _plain((md.get("Artist") or {}).get("value"))[:80], "date": date,
            "desc": _plain((md.get("ImageDescription") or {}).get("value"))[:200],
        })
    # landscape, big enough, recent first
    def score(x):
        yr = int(x["date"][:4]) if x["date"][:4].isdigit() else 2000
        return ((x["w"] >= 1000) * 2 + (x["w"] > x["h"] * 1.15) * 2 + min(max(yr - 2016, 0), 10) / 3)
    return sorted(out, key=score, reverse=True)


def download(url: str, path: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_bytes(r.read())
        return True
    except Exception as e:  # noqa: BLE001
        print("download failed", url, e)
        return False


def candidates(queries_file: str, out_dir: str, per_query: int = 4) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    qs = json.loads(Path(queries_file).read_text())
    report = {}
    for key, query in qs.items():
        res = search(query)[:per_query]
        report[key] = []
        for i, c in enumerate(res):
            fn = f"{key}_{i}.jpg"
            if c["thumb"] and download(c["thumb"], out / fn):
                report[key].append({**c, "file": fn})
            time.sleep(0.5)
    (out / "candidates.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: [(x["file"], x["title"], x["license"], x["w"], x["date"]) for x in v] for k, v in report.items()}, indent=1))


def fetch(picks_file: str, out_dir: str) -> None:
    """picks.json: {slug: "File:Name.jpg"} -> downloads 1400px versions + credits.json"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    picks = json.loads(Path(picks_file).read_text())
    credits_path = out / "credits.json"
    credits = json.loads(credits_path.read_text()) if credits_path.exists() else {}
    for slug, title in picks.items():
        if (out / f"{slug}.jpg").exists() and slug in credits:
            continue
        js = _get({"action": "query", "titles": title, "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1400})
        p = next(iter(js["query"]["pages"].values()))
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        lic = _plain((md.get("LicenseShortName") or {}).get("value"))
        if not OK_LICENSES.match(lic):
            print("skipped (licence)", title, lic)
            continue
        if download(ii.get("thumburl") or ii.get("url"), out / f"{slug}.jpg"):
            credits[slug] = {"title": title, "author": _plain((md.get("Artist") or {}).get("value"))[:80],
                             "license": lic, "license_url": _plain((md.get("LicenseUrl") or {}).get("value")),
                             "page": ii.get("descriptionurl")}
        time.sleep(0.5)
    credits_path.write_text(json.dumps(credits, indent=1))
    print(json.dumps(credits, indent=1))


if __name__ == "__main__":
    cmd, a, b = sys.argv[1:4]
    {"candidates": candidates, "fetch": fetch}[cmd](a, b)
