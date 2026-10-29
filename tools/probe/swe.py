# Probe: stats.swehockey.se (Swedish Ice Hockey Association) - what's on the pages, per-game detail, history depth, terms.
import json, re, os, urllib.request, html
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"}
OUT = "probe-swe"; os.makedirs(OUT, exist_ok=True)
B = "https://stats.swehockey.se"
def get(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=40); return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return getattr(e, "code", None), str(e)
def tables(h, n=3):
    out = []
    for t in re.findall(r"<table.*?</table>", h, re.S)[:12]:
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", t, re.S):
            cells = [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
            cells = [re.sub(r"\s+", " ", c) for c in cells]
            if any(cells): rows.append(cells)
        if rows: out.append(rows[:n + 3])
    return out
def links(h, pat):
    return sorted(set(re.findall(pat, h)))
rep = {}
pages = {"live": "/ScheduleAndResults/Live/20961", "schedule": "/ScheduleAndResults/Schedule/20961", "scoring": "/Players/Statistics/ScoringLeaders/20961",
         "goalies": "/Players/Statistics/LeadingGoaliesSVS/20961", "teams": "/Teams/Statistics/ScoringAndGoalkeeping/20961", "standings": "/ScheduleAndResults/Standings/20961",
         "rosters": "/Teams/Info/TeamRoster/20961", "overview": "/ScheduleAndResults/Overview/20961"}
allh = ""
for k, p in pages.items():
    s, h = get(B + p); allh += h
    open(f"{OUT}/{k}.html", "w").write(h)
    rep[k] = {"status": s, "bytes": len(h), "tables": tables(h, 3), "nav": links(h, r'href="(/(?:Players|Teams|ScheduleAndResults|Game)/[^"#]+)"')[:80]}
games = links(allh, r'(/Game/(?:Events|LineUps|Statistics|Report|Overview)/\d+)')
rep["game_links"] = games[:30]
gid = None
m = re.findall(r"/Game/Events/(\d+)", allh)
if m: gid = m[0]
else:
    m2 = re.findall(r"openonlinewindow\('(/Game/Events/(\d+))'", allh) or re.findall(r"/Game/\w+/(\d+)", allh)
    gid = m2[0][1] if m2 and isinstance(m2[0], tuple) else (m2[0] if m2 else None)
rep["sample_game"] = gid
if gid:
    for kind in ("Events", "LineUps", "Statistics", "Report", "Overview", "Actions", "Players", "ShotsHeatmap", "Shots"):
        s, h = get(f"{B}/Game/{kind}/{gid}")
        open(f"{OUT}/game_{kind}.html", "w").write(h)
        rep[f"game_{kind}"] = {"status": s, "bytes": len(h), "tables": tables(h, 6)[:6]}
# history: find season ids in a dropdown / links on overview
ids = sorted(set(re.findall(r"/ScheduleAndResults/(?:Overview|Live|Schedule)/(\d{4,6})", allh)))
rep["season_ids_seen"] = ids[:60]
opts = re.findall(r'<option[^>]*value="(\d{4,6})"[^>]*>([^<]+)</option>', allh)
rep["options"] = opts[:120]
# robots / terms
s, h = get(B + "/robots.txt"); rep["robots"] = h[:1500]
for p in ("/Home/About", "/Home/Terms", "/About"):
    s, h = get(B + p); rep["about" + p] = {"status": s, "text": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))[:1500]}
json.dump(rep, open(f"{OUT}/report.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps({k: (v if k in ("game_links", "sample_game", "season_ids_seen") else "...") for k, v in rep.items()}, indent=1)[:3000])
