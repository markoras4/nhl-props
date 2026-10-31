"""Find freely licensed photos on Wikimedia Commons for blog articles: title, license, author, size, thumbnail URL."""
import json, os, urllib.parse, urllib.request

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "NHLPropsLab-blog/1.0 (research for article images)"}
Q = ["Madison Square Garden hockey", "Madison Square Garden interior", "Stanley Cup trophy", "Mark Messier", "Brian Leetch", "Mike Richter",
     "Pavel Bure", "Trevor Linden", "Kirk McLean", "Adam Graves", "New York Rangers 1994", "Vancouver Canucks 1994", "Pacific Coliseum",
     "Rogers Arena hockey", "ice hockey goal net", "hockey puck ice", "Hockey Hall of Fame Stanley Cup", "Rangers banners Madison Square Garden"]


def get(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


out = {}
for q in Q:
    try:
        js = get({"action": "query", "generator": "search", "gsrsearch": q + " filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 12,
                  "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
    except Exception as e:  # noqa: BLE001
        out[q] = {"error": str(e)}
        continue
    rows = []
    for p in (js.get("query") or {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        v = lambda k: (md.get(k) or {}).get("value", "")
        rows.append({"title": p.get("title"), "w": ii.get("width"), "h": ii.get("height"), "thumb": ii.get("thumburl"), "page": ii.get("descriptionurl"),
                     "license": v("LicenseShortName"), "artist": v("Artist")[:200], "desc": v("ImageDescription")[:300], "date": v("DateTimeOriginal")[:40]})
    out[q] = rows
os.makedirs("probe-commons", exist_ok=True)
json.dump(out, open("probe-commons/commons.json", "w"), indent=1)
print({k: len(v) if isinstance(v, list) else v for k, v in out.items()})
