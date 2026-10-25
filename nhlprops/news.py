"""Game-day news: injuries, starting goalies, projected lines.

Every parser is defensive: if a page changes shape, the dashboard falls back to
estimates (starter by recent starts, lineup by recent ice time) and says so.
"""
from __future__ import annotations

import json
import re
import unicodedata

TEAM_NAMES = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CGY": "Calgary Flames",
    "CAR": "Carolina Hurricanes", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
    "CBJ": "Columbus Blue Jackets", "DAL": "Dallas Stars", "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers",
    "FLA": "Florida Panthers", "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens",
    "NSH": "Nashville Predators", "NJD": "New Jersey Devils", "NYI": "New York Islanders",
    "NYR": "New York Rangers", "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins", "SJS": "San Jose Sharks", "SEA": "Seattle Kraken", "STL": "St. Louis Blues",
    "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WSH": "Washington Capitals", "WPG": "Winnipeg Jets",
}

INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
GOALIES_URL = "https://www.dailyfaceoff.com/starting-goalies/{date}"
LINES_URL = "https://www.dailyfaceoff.com/teams/{slug}/line-combinations"
NHL_LINEUPS_URL = "https://www.nhl.com/news/nhl-lineup-projections-2026-27-season"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower().replace("-", " ")).strip()


_BY_NAME = {norm(v): k for k, v in TEAM_NAMES.items()}
_BY_NAME.update({norm("Utah Hockey Club"): "UTA", norm("Arizona Coyotes"): "UTA", norm("Montréal Canadiens"): "MTL"})


def team_from_name(name: str) -> str | None:
    n = norm(name)
    if n in _BY_NAME:
        return _BY_NAME[n]
    for k, v in _BY_NAME.items():   # nickname only ("Maple Leafs")
        if n and (k.endswith(n) or n.endswith(k.split()[-1])):
            return v
    return None


def slug(abbr: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(TEAM_NAMES[abbr]).replace("st louis", "st louis")).strip("-")


class NameIndex:
    """Match outside names ('Mitch Marner', 'J.T. Miller') to NHL player ids."""

    def __init__(self, players: list[dict]):
        self.full, self.last = {}, {}
        for p in players:
            n = norm(p["name"])
            self.full.setdefault(n, []).append(p)
            self.last.setdefault((n.split()[-1] if n else "", p["team"]), []).append(p)

    def find(self, name: str, team: str | None = None) -> dict | None:
        n = norm(name)
        hits = [p for p in self.full.get(n, []) if team is None or p["team"] == team]
        if len(hits) == 1:
            return hits[0]
        if team and n:
            hits = self.last.get((n.split()[-1], team), [])
            if len(hits) == 1:
                return hits[0]
            first = n.split()[0][:1]
            hits = [p for p in hits if norm(p["name"]).startswith(first)]
            if len(hits) == 1:
                return hits[0]
        return None


def _next_data(html: str | None) -> dict | None:
    if not html:
        return None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


# ------------------------------------------------------------------ injuries
def classify(status: str) -> str:
    s = (status or "").lower()
    if any(k in s for k in ("day-to-day", "day to day", "questionable", "probable", "game-time", "doubtful")):
        return "questionable"
    if s:
        return "out"
    return "questionable"


def parse_injuries(js: dict | None) -> dict[str, list[dict]]:
    """-> {TEAM: [{name, status, kind, detail, return}]}"""
    out: dict[str, list[dict]] = {}
    for team in (js or {}).get("injuries", []) or []:
        abbr = team_from_name(team.get("displayName", ""))
        for inj in team.get("injuries", []) or []:
            ath = inj.get("athlete") or {}
            t = abbr or team_from_name(((ath.get("team") or {}).get("displayName")) or "")
            if not t:
                continue
            det = inj.get("details") or {}
            status = inj.get("status") or (det.get("fantasyStatus") or {}).get("description") or ""
            out.setdefault(t, []).append({
                "name": ath.get("displayName") or ath.get("fullName") or "",
                "status": status, "kind": classify(status),
                "detail": " ".join(x for x in (det.get("type"), det.get("location"), det.get("detail")) if x)
                          or (inj.get("type") or {}).get("description") or "",
                "return": det.get("returnDate"), "comment": inj.get("shortComment") or "",
            })
    return out


# ------------------------------------------------------------ starting goalies
def parse_starting_goalies(html: str | None) -> list[dict]:
    """-> [{name, status, team?}] from the starting-goalies page."""
    data = _next_data(html)
    out = []
    for d in _walk(data or {}):
        for side in ("home", "away"):
            name = d.get(f"{side}GoalieName")
            if not name:
                continue
            status = (d.get(f"{side}NewsStrengthName") or d.get(f"{side}NewsStrength") or "Unconfirmed")
            team = team_from_name(d.get(f"{side}TeamName") or "") if d.get(f"{side}TeamName") else None
            out.append({"name": name, "status": str(status), "team": team})
    return out


# ------------------------------------------------- MoneyPuck likely starters
MP_DAY_URL = "https://moneypuck.com/moneypuck/dates/{ymd}.htm"


def parse_moneypuck_day(html: str | None) -> dict[int, list[dict]]:
    """-> {game_id: [{name (last name), status ('Likely'...), pct}]} from a MoneyPuck day page."""
    out: dict[int, list[dict]] = {}
    for row in re.split(r"<tr[\s>]", html or "", flags=re.I)[1:]:
        m = re.search(r"preview\.htm\?id=(\d+)", row)
        if not m:
            continue
        gid = int(m.group(1))
        for lvl, name, p in re.findall(r">\s*([A-Za-z ]*?)\s*Starter:\s*<br>\s*([^<:]+?)\s*:\s*([\d.]+)\s*%", row, flags=re.I):
            out.setdefault(gid, []).append({"name": name.strip(), "status": (lvl.strip() or "Likely").title(),
                                            "pct": float(p)})
    return out


# ---------------------------------------------------------------- line combos
def parse_lines(html: str | None) -> list[dict]:
    """-> [{name, group, pos}] where group is f1..f4, d1..d3, pp1, pp2, g1, g2 ..."""
    data = _next_data(html)
    out, seen = [], set()
    for d in _walk(data or {}):
        g = d.get("groupIdentifier") or d.get("lineIdentifier")
        name = d.get("name") or d.get("playerName")
        if not g or not name or not isinstance(name, str):
            continue
        key = (name, str(g).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "group": str(g).lower(),
                    "pos": str(d.get("positionIdentifier") or d.get("position") or "").lower(),
                    "injury": d.get("injuryStatus")})
    return out


# ------------------------------------------------- NHL.com game-day lineups
_SEP = re.compile(r"\s+(?:--|–|—)\s+")


def _names(txt: str) -> list[str]:
    txt = txt.strip().strip("*").strip()
    if not txt or txt.lower().startswith("none"):
        return []
    return [n.strip(" .*") for n in re.split(r",\s*|\s+and\s+", txt) if n.strip(" .*")]


def _injured(txt: str) -> list[dict]:
    txt = txt.strip().strip("*").strip()
    if not txt or txt.lower().startswith("none"):
        return []
    out = []
    for m in re.finditer(r"\s*([^,(]+?)\s*(?:\(([^)]*)\))?\s*(?:,|$)", txt):
        name = m.group(1).strip(" .*")
        if name:
            out.append({"name": name, "detail": (m.group(2) or "").strip()})
    return out


def _et_date(iso: str) -> str | None:
    import datetime as dt
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (t - dt.timedelta(hours=5)).date().isoformat()


def parse_nhl_lineups(html: str | None) -> dict:
    """NHL.com 'Projected lineups, starting goalies for today'.
    -> {'date': ET date, 'updated': iso, 'teams': {TEAM: {opp, home, lines:[{name,group,pos}], goalies:[...],
        scratched:[...], injured:[{name, detail}], notes}}}"""
    out = {"date": None, "updated": None, "teams": {}}
    if not html:
        return out
    body = None
    for m in re.finditer(r'<script type="application/ld[^"]*json">(.*?)</script>', html, re.S):
        try:
            js = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(js, dict) and js.get("articleBody"):
            body = js["articleBody"]
            out["updated"] = js.get("dateModified") or js.get("datePublished")
            out["date"] = _et_date(js.get("datePublished") or "") or None
            break
    if not body:
        return out
    for block in re.split(r"(?<!#)## \*\*", body)[1:]:
        hm = re.match(r"([A-Z][A-Z .'\-]+?)\s*\([\d\-]+\)\s+at\s+([A-Z][A-Z .'\-]+?)\s*\(", block)
        if not hm:
            continue
        away, home = team_from_name(hm.group(1).title()), team_from_name(hm.group(2).title())
        if not away or not home:
            continue
        notes = ""
        nm = re.search(r"\*\*Status report\*\*\s*(.*)", block, re.S)
        if nm:
            notes = re.sub(r"\s+", " ", re.sub(r"[*#]", "", nm.group(1))).strip()
            block = block[:nm.start()]
        for sec in re.finditer(r"\*\*([A-Za-z .'\-]+?) projected lineup\*\*(.*?)(?=\*\*[A-Za-z .'\-]+? projected lineup\*\*|$)",
                               block, re.S):
            team = team_from_name(sec.group(1).strip())
            if team not in (home, away):
                continue
            rec = {"opp": away if team == home else home, "home": team == home, "lines": [], "goalies": [],
                   "scratched": [], "injured": [], "notes": notes}
            nf = nd = 0
            for raw in sec.group(2).split("\n"):
                ln = raw.strip()
                if not ln:
                    continue
                sm = re.match(r"\**\s*Scratched:\**\s*(.*)", ln.lstrip("*"), re.I)
                im = re.match(r"\**\s*Injured:\**\s*(.*)", ln.lstrip("*"), re.I)
                if sm:
                    rec["scratched"] = _names(sm.group(1))
                    continue
                if im:
                    rec["injured"] = _injured(im.group(1))
                    continue
                if ln.startswith(("*", "#")):
                    continue
                parts = [x.strip(" *") for x in _SEP.split(ln) if x.strip(" *")]
                if len(parts) == 3:
                    nf += 1
                    rec["lines"] += [{"name": n, "group": f"f{nf}", "pos": "f"} for n in parts]
                elif len(parts) == 2:
                    nd += 1
                    rec["lines"] += [{"name": n, "group": f"d{nd}", "pos": "d"} for n in parts]
                elif len(parts) == 1 and len(parts[0].split()) <= 4 and nd:
                    rec["goalies"].append(parts[0])
            for i, gname in enumerate(rec["goalies"][:2]):
                rec["lines"].append({"name": gname, "group": f"g{i + 1}", "pos": "g"})
            out["teams"][team] = rec
    return out


def fetch_news(src, teams: list[str], dates: list[str], log=print) -> dict:
    injuries = parse_injuries(src.json(INJURIES_URL, "news/injuries.json", 2))
    mp_starters: dict[int, list] = {}
    for d in dates:
        ymd = d.replace("-", "")
        mp_starters.update(parse_moneypuck_day(src.text(MP_DAY_URL.format(ymd=ymd), f"news/mp_day_{ymd}.htm", 0.2)))
    goalies = {}
    for d in dates[:2]:  # backup source, today/tomorrow only
        goalies[d] = parse_starting_goalies(src.text(GOALIES_URL.format(date=d), f"news/goalies_{d}.html", 0.2))
    lines = {t: parse_lines(src.text(LINES_URL.format(slug=slug(t)), f"news/lines_{t}.html", 4)) for t in teams}
    nhl = parse_nhl_lineups(src.text(NHL_LINEUPS_URL, "news/nhl_lineups.html", 0.15))
    n_inj = sum(len(v) for v in injuries.values())
    n_g = sum(len(v) for v in goalies.values())
    n_l = sum(1 for v in lines.values() if v)
    log(f"News: {n_inj} injuries, likely starters for {len(mp_starters)} games (+{n_g} backup reports), "
        f"line combinations for {n_l}/{len(teams)} teams, "
        f"game-day lineups for {len(nhl['teams'])} teams ({nhl['date']}, updated {nhl['updated']})")
    return {"injuries": injuries, "goalies": goalies, "mp_starters": mp_starters, "lines": lines, "nhl": nhl,
            "status": {"injuries": n_inj > 0, "goalies": bool(mp_starters) or n_g > 0, "lines": n_l > 0}}
