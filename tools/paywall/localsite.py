"""Build a local copy of the site with the paywall applied, from an already built site (e.g. the "site" branch).

    git archive origin/site | tar -x -C .local-test/site-src
    py tools/paywall/localsite.py .local-test/site-src .local-test/site

It re-renders app/, previews.html and landing.html with the CURRENT templates, splits the data the same way the
daily build does (nhlprops/paywall.py), and writes the Pro files to <out>/../pro/ (never inside the site).
Then run tools/paywall/devserver.py to browse it as visitor / free / pro / creator / media.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nhlprops.paywall import split, strip_archive, strip_feed_rows, strip_preview  # noqa: E402


def read_data(html: str, marker: str) -> tuple[dict, int, int]:
    i = html.index(marker) + len(marker)
    while html[i] in " =":
        i += 1
    d, end = json.JSONDecoder().raw_decode(html, i)
    return d, i, end


def inject(template: Path, payload: dict, app_url: str | None = None) -> str:
    js = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = template.read_text(encoding="utf-8").replace("/*__DATA__*/null", js)
    return html.replace("APP_URL", app_url) if app_url is not None else html


def main(src: Path, out: Path) -> None:
    pro_dir = out.parent / "pro"
    if out.exists():
        shutil.rmtree(out)
    if pro_dir.exists():
        shutil.rmtree(pro_dir)
    shutil.copytree(src, out)
    pro_dir.mkdir(parents=True)

    # the app
    full, _, _ = read_data((src / "app" / "index.html").read_text(encoding="utf-8"), "const DATA=")
    pub, pro = split(full)
    (out / "app" / "index.html").write_text(inject(ROOT / "nhlprops" / "template.html", pub), encoding="utf-8")
    (pro_dir / "latest.json").write_text(json.dumps(pro, separators=(",", ":")), encoding="utf-8")

    # landing + previews pages: lock the player-prop bullet
    for name, app_url in (("index.html", "app/"), ("previews.html", "app/")):
        f = src / name
        if not f.exists():
            continue
        d, _, _ = read_data(f.read_text(encoding="utf-8"), "DATA = ")
        d["previews"] = [strip_preview(p) for p in d.get("previews") or []]
        tpl = ROOT / "nhlprops" / ("landing.html" if name == "index.html" else "previews.html")
        html = inject(tpl, d, app_url)
        (out / name).write_text(html, encoding="utf-8")
        if name == "index.html":
            (out / "landing.html").write_text(html, encoding="utf-8")

    # public data files
    al = out / "data" / "alerts.json"
    if al.exists():
        shutil.move(str(al), pro_dir / "alerts.json")
    for name in ("bestpicks.json", "ledger.json"):
        f = out / "data" / name
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            d["rows"] = strip_feed_rows(d.get("rows") or [])
            f.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
    (pro_dir / "archive").mkdir()
    for f in (out / "data" / "archive").glob("*.json"):
        p, full_a = strip_archive(json.loads(f.read_text(encoding="utf-8")))
        (pro_dir / "archive" / f.name).write_text(json.dumps(full_a, separators=(",", ":")), encoding="utf-8")
        f.write_text(json.dumps(p, separators=(",", ":")), encoding="utf-8")
    print(f"site: {out}\npro:  {pro_dir}\npublic app {len((out / 'app' / 'index.html').read_bytes())//1024} KB, "
          f"pro patch {len((pro_dir / 'latest.json').read_bytes())//1024} KB, open players {len(pub['open_ids'])}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
