# Probe 3: SHL history depth (play-by-play / box scores per season) and SHL odds at Kambi (Unibet SE) and Pinnacle.
import json, urllib.request, os
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "Accept": "application/json"}
OUT = "probe-shl3"; os.makedirs(OUT, exist_ok=True)
def get(u, t=40):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=t); b = r.read(); return r.status, b
    except Exception as e:
        return getattr(e, "code", None), str(e).encode()
rep = []
SEASONS = {"2026": "ndcf81nlb3", "2025": "xs4m9qupsi", "2024": "qeb-73bZkIm9A", "2023": "qcz-3NvSZ2Cmh", "2022": "qbN-XMFfjGVt", "2021": "qZl-8qa6OaFXf", "2019": "qWX-334j11U5o1"}
for code, su in SEASONS.items():
    s, b = get(f"https://www.shl.se/api/sports-v2/game-schedule?seasonUuid={su}&seriesUuid=qQ9-bb0bzEWUk&gameTypeUuid=qQ9-af37Ti40B&gamePlace=all&played=all")
    try: gi = json.loads(b)["gameInfo"]
    except Exception: rep.append({"season": code, "schedule": s}); continue
    done = [g for g in gi if g.get("state") == "post-game"]
    row = {"season": code, "games": len(gi), "played": len(done)}
    for i, g in enumerate([done[0], done[len(done) // 2]] if done else []):
        for kind in ("play-by-play", "player-stats", "team-stats"):
            s2, b2 = get(f"https://www.shl.se/api/gameday/{kind}/{g['uuid']}")
            row[f"{kind}_{i}"] = [s2, len(b2)]
            if i == 0: open(f"{OUT}/{code}_{kind}.json", "wb").write(b2[:400000])
    s3, b3 = get(f"https://www.shl.se/api/statistics-v2/stats-info/players_summary?count=5&ssgtUuid={gi[0]['ssgtUuid']}&provider=statnet") if gi else (None, b"")
    row["players_summary"] = [s3, len(b3)]
    rep.append(row)
# Kambi (Unibet SE): SHL list and one event's offers
K = "https://eu-offering-api.kambicdn.com/offering/v2018/ubse"
for path in ["ice_hockey/sweden/shl", "ice_hockey/sweden"]:
    s, b = get(f"{K}/listView/{path}/all/all/matches.json?lang=en_GB&market=SE&useCombined=true")
    rep.append({"kambi_list": path, "status": s, "bytes": len(b)})
    open(f"{OUT}/kambi_list_{path.replace('/','_')}.json", "wb").write(b)
    try:
        evs = json.loads(b).get("events", [])
        rep.append({"kambi_events": len(evs), "names": [e["event"]["name"] for e in evs[:8]]})
        if evs:
            eid = evs[0]["event"]["id"]
            s2, b2 = get(f"{K}/betoffer/event/{eid}.json?lang=en_GB&market=SE&includeParticipants=true")
            open(f"{OUT}/kambi_event.json", "wb").write(b2)
            offers = json.loads(b2).get("betOffers", [])
            from collections import Counter
            rep.append({"kambi_offer_types": Counter(o.get("criterion", {}).get("label") for o in offers).most_common(60)})
            break
    except Exception as e:
        rep.append({"kambi_err": str(e)[:200]})
# Pinnacle: find an SHL league
s, b = get("https://guest.api.arcadia.pinnacle.com/0.1/sports/19/leagues?all=false")
try:
    lg = [x for x in json.loads(b) if "Sweden" in x.get("name", "") or "SHL" in x.get("name", "")]
    rep.append({"pinnacle_leagues": [(x["id"], x["name"]) for x in lg]})
    for x in lg[:2]:
        s2, b2 = get(f"https://guest.api.arcadia.pinnacle.com/0.1/leagues/{x['id']}/matchups")
        m = json.loads(b2) if s2 == 200 else []
        rep.append({"pinnacle_league": x["name"], "matchups": len(m), "special": sum(1 for y in m if y.get("type") == "special"), "sample": [y.get("special", {}).get("description") for y in m if y.get("type") == "special"][:12]})
except Exception as e:
    rep.append({"pinnacle_err": str(e)[:200], "status": s})
json.dump(rep, open(f"{OUT}/report.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(rep, indent=1, ensure_ascii=False)[:6000])
