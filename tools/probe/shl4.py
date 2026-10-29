import json, urllib.request, os
from collections import Counter
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
OUT = "probe-shl4"; os.makedirs(OUT, exist_ok=True)
K = "https://eu-offering-api.kambicdn.com/offering/v2018/ubse"
rep = []
for eid in (1027771620, 1027771572, 1027771621):
    b = urllib.request.urlopen(urllib.request.Request(f"{K}/betoffer/event/{eid}.json?lang=en_GB&market=SE&includeParticipants=true", headers=H), timeout=40).read()
    open(f"{OUT}/ev_{eid}.json", "wb").write(b)
    d = json.loads(b); offers = d.get("betOffers", [])
    labels = Counter(o.get("criterion", {}).get("label") for o in offers)
    players = sorted({oc.get("participant") for o in offers for oc in o.get("outcomes", []) if oc.get("participant")})
    rep.append({"event": eid, "name": (d.get("events") or [{}])[0].get("name"), "offers": len(offers), "labels": labels.most_common(80), "n_players": len(players), "players_sample": players[:15]})
# NHL event for comparison of market count
lst = json.loads(urllib.request.urlopen(urllib.request.Request(f"{K}/listView/ice_hockey/nhl/all/all/matches.json?lang=en_GB&market=SE&useCombined=true", headers=H), timeout=40).read())
if lst.get("events"):
    eid = lst["events"][0]["event"]["id"]
    d = json.loads(urllib.request.urlopen(urllib.request.Request(f"{K}/betoffer/event/{eid}.json?lang=en_GB&market=SE&includeParticipants=true", headers=H), timeout=40).read())
    rep.append({"nhl_event": lst["events"][0]["event"]["name"], "offers": len(d.get("betOffers", [])), "labels": Counter(o.get("criterion", {}).get("label") for o in d.get("betOffers", [])).most_common(15)})
json.dump(rep, open(f"{OUT}/report.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(rep, indent=1, ensure_ascii=False)[:5000])
