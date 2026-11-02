"""Command line:

    python -m nhlprops build [--date YYYY-MM-DD] [--no-open]
    python -m nhlprops grade
    python -m nhlprops check
    python -m nhlprops build --demo     (simulated data, no internet)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import webbrowser
from pathlib import Path

import numpy as np

from .config import CONFIG
from .gather import gather, season_start_for
from .grade import grade
from .model import build_slate
from .sources import Source

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "template.html"


def nhl_today() -> str:
    """Today's date in New York (NHL dates are North American)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date().isoformat()


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 5)
    return o


def demo_odds(slate: dict) -> dict:
    """Bookmaker-like prices for demo mode: model probability +/- noise, ~7% margin."""
    import random
    rnd = random.Random(3)
    out = {}
    for g in slate["games"][:8]:
        po = {}
        for pr in g["props"]:
            if not slate["players"][str(pr["id"])]["active"]:
                continue
            d = {}
            for m in ("g", "a", "pt", "s1.5", "s2.5", "s3.5"):
                p = min(0.95, max(0.02, pr["p"][m] * rnd.uniform(0.82, 1.18)))
                d[m] = {"o": round(1 / (p * 1.07), 2), "n": round(1 / ((1 - p) * 1.07), 2)}
            po[pr["id"]] = d
        out[g["id"]] = {"book": "Bookmaker", "players": po, "fetched": "demo",
                        "totals": {"5.5": {"over": 1.85, "under": 1.95}, "6.5": {"over": 2.6, "under": 1.5}},
                        "winner": {"home": round(1 / (g["default"]["p_home"] * 1.05), 2),
                                   "away": round(1 / (g["default"]["p_away"] * 1.05), 2)}}
    return out


def save_history(slate: dict, hist_dir: Path):
    """One file per game date. Games that have already started keep their earlier prediction."""
    hist_dir.mkdir(parents=True, exist_ok=True)
    players = slate["players"]
    by_date: dict[str, list] = {}
    for g in slate["games"]:
        if is_locked(g):
            continue
        by_date.setdefault(g["date"], []).append(g)
    for d, games in by_date.items():
        f = hist_dir / f"{d}.json"
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"games": [], "props": []}
        ids = {g["id"] for g in games}
        rec = {
            "date": d, "built": dt.datetime.now().isoformat(timespec="seconds"),
            "games": [g for g in old["games"] if g["id"] not in ids] + [
                {"id": g["id"], "home": g["home"], "away": g["away"],
                 "model": {str(L): g["default"][f"over_{L}"] for L in CONFIG["ou_lines"]},
                 "hist": g["hist_over"],
                 "shots": ({k: g["shots"][k] for k in ("h", "a", "sv_h", "sv_a", "p")} if g.get("shots") else None),
                 "starters": g.get("starters"), "mk": g["default"].get("mk")} for g in games],
            "props": [p for p in old["props"] if p["game"] not in ids] + [
                {"id": pr["id"], "name": players[str(pr["id"])]["name"], "game": g["id"], "p": pr["p"],
                 "o": {m: v["o"] for m, v in (slate.get("odds", {}).get(g["id"], {}).get("players", {})
                                              .get(pr["id"], {})).items()}}
                for g in games for pr in g["props"] if players[str(pr["id"])]["active"]],
        }
        f.write_text(json.dumps(clean(rec)), encoding="utf-8")


from .lock import PRE_STATES, is_locked  # noqa: E402  (a game locks 5 minutes before its scheduled start)


def freeze_started(slate: dict, frozen_dir: Path) -> None:
    """Keep each game exactly as it was at its last refresh before the lock (5 minutes before the scheduled start):
    probabilities, odds and picks; its stakes box in lock_stakes; player, goalie and team details in archive.build.
    Sportsbooks switch to live odds at puck drop and the NHL feed can lag, so nothing after the lock is used."""
    frozen_dir.mkdir(parents=True, exist_ok=True)
    odds = slate.setdefault("odds", {})
    now = dt.datetime.now(dt.timezone.utc)
    by_date: dict[str, list] = {}
    for g in slate["games"]:
        by_date.setdefault(g["date"], []).append(g)
    for d, games in by_date.items():
        f = frozen_dir / f"{d}.json"
        snap = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        changed = False
        for i, g in enumerate(games):
            gid = str(g["id"])
            if not is_locked(g, now):
                o = odds.get(g["id"]) or odds.get(gid)
                prev = snap.get(gid)
                # keep the last snapshot that still had player odds if the book has pulled them early
                if o and not o.get("players") and prev and (prev.get("odds") or {}).get("players"):
                    o = {**o, "players": prev["odds"]["players"]}
                snap[gid] = {"game": clean(g), "odds": clean(o) if o else None,
                             "stakes": (prev or {}).get("stakes"), "at": now.isoformat(timespec="minutes")}
                changed = True
            elif gid in snap:
                live = {k: g.get(k) for k in ("state", "hs", "as", "period", "last_period")}
                frozen = {**snap[gid]["game"], **live, "frozen": snap[gid]["at"]}
                idx = slate["games"].index(g)
                slate["games"][idx] = frozen
                odds.pop(g["id"], None); odds.pop(gid, None)
                if snap[gid].get("odds"):
                    odds[g["id"]] = snap[gid]["odds"]
                # (the players, goalies and teams on its page are restored from the archive snapshot, see archive.build)
            else:
                # first seen after the lock (no earlier refresh): never show prices fetched after it
                odds.pop(g["id"], None); odds.pop(gid, None)
        if changed:
            f.write_text(json.dumps(snap), encoding="utf-8")
    # drop snapshot files older than 10 days
    cutoff = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    for f in frozen_dir.glob("20*.json"):
        if f.stem < cutoff:
            f.unlink()


def save_public(slate: dict, pub_dir: Path) -> None:
    """Keep every game's public betting split as it was at the lock, with the model's numbers and the odds then,
    for a later test of whether money vs tickets gaps say anything the model doesn't. One file per season."""
    pub_dir.mkdir(parents=True, exist_ok=True)
    odds = slate.get("odds") or {}
    f = pub_dir / f"{slate.get('season', 'cur')}.json"
    store = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    added = 0
    for g in slate["games"]:
        gid = str(g["id"])
        o = odds.get(g["id"]) or odds.get(gid) or {}
        if gid in store or not o.get("public") or not is_locked(g):
            continue
        d = g.get("default") or {}
        store[gid] = {"d": g["date"], "h": g["home"], "a": g["away"], "pub": o["public"], "at": g.get("frozen"),
                      "p_home": d.get("p_home"), "p_away": d.get("p_away"),
                      "p_over": {k[5:]: v for k, v in d.items() if k.startswith("over_")},
                      "odds": {"winner": o.get("winner"), "totals": o.get("totals")}}
        added += 1
    if added:
        f.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
        print(f"Public betting: saved {added} locked splits ({len(store)} this season)")


def lock_stakes(slate: dict, frozen_dir: Path) -> None:
    """'What's at stake' and the teams' luck pills for a game, as they were before its lock."""
    F = slate.get("futures") or {}
    fg, ft = F.get("games") or {}, F.get("teams") or {}
    now = dt.datetime.now(dt.timezone.utc)
    by_date: dict[str, list] = {}
    for g in slate["games"]:
        by_date.setdefault(g["date"], []).append(g)
    for d, games in by_date.items():
        f = frozen_dir / f"{d}.json"
        if not f.exists():
            continue
        snap = json.loads(f.read_text(encoding="utf-8"))
        changed = False
        for g in games:
            gid = str(g["id"])
            cur = fg.get(g["id"], fg.get(gid))
            if gid not in snap:
                continue
            if not is_locked(g, now) and not g.get("frozen"):
                if cur is not None:
                    snap[gid]["stakes"] = {"game": cur, "teams": {t: ft[t] for t in (g["home"], g["away"]) if t in ft}}
                    changed = True
            elif snap[gid].get("stakes"):
                st = snap[gid]["stakes"]
                fg.pop(g["id"], None)
                fg[gid] = {**st["game"], "_lk": {t: {k: v.get(k) for k in ("xpts", "gpts", "pts_now", "gp")} for t, v in (st.get("teams") or {}).items()}}
        if changed:
            f.write_text(json.dumps(snap), encoding="utf-8")


def lineup_roles(slate: dict, lu_dir: Path) -> None:
    """Save today's lines/PP units; compare with a lineup from 2-10 days ago to flag promotions."""
    lu_dir.mkdir(parents=True, exist_ok=True)
    today = {}
    for t, ctx in (slate.get("teams") or {}).items():
        lu = ctx.get("lineup") or {}
        if lu.get("source") not in ("projected", "gameday"):
            continue
        today[t] = {str(pid): k for k, ids in (lu.get("groups") or {}).items() for pid in ids}
    d0 = dt.date.fromisoformat(slate["date"])
    f_today = lu_dir / f"{slate['date']}.json"
    old = {}
    if f_today.exists():
        old = json.loads(f_today.read_text())
    f_today.write_text(json.dumps({**old, **today}))
    past = sorted(f for f in lu_dir.glob("20*.json")
                  if (d0 - dt.date.fromisoformat(f.stem)).days >= 2 and (d0 - dt.date.fromisoformat(f.stem)).days <= 10)
    if not past:
        return
    before = json.loads(past[-1].read_text())
    for t, now in today.items():
        prev = before.get(t)
        if not prev:
            continue
        inv_now, inv_prev = {}, {}
        for pid, k in now.items():
            inv_now.setdefault(pid, []).append(k)
        for pid, k in prev.items():
            inv_prev.setdefault(pid, []).append(k)
        for pid, ks in inv_now.items():
            pl = slate["players"].get(pid) or slate["players"].get(int(pid)) if pid.isdigit() else None
            if not pl or pid not in inv_prev:
                continue
            tags = []
            was, isn = inv_prev[pid], ks
            if "pp1" in isn and "pp1" not in was:
                tags.append("pp1_new")
            if "pp1" in was and "pp1" not in isn:
                tags.append("pp1_lost")
            ln = lambda arr, c: min((int(k[1]) for k in arr if k[0] == c and k[1:].isdigit()), default=None)
            for c in ("f", "d"):
                a, b = ln(was, c), ln(isn, c)
                if a and b and b < a and b <= 2:
                    tags.append("line_up")
                elif a and b and b > a and a <= 2:
                    tags.append("line_down")
            if tags:
                pl["role_tags"] = tags
    cutoff = (d0 - dt.timedelta(days=14)).isoformat()
    for f in lu_dir.glob("20*.json"):
        if f.stem < cutoff:
            f.unlink()


def track_open(slate: dict, open_dir: Path) -> None:
    """Remember the first price seen for every market (per game date) and attach it to the odds as 'open'."""
    open_dir.mkdir(parents=True, exist_ok=True)
    odds = slate.get("odds") or {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    by_date: dict[str, list] = {}
    for g in slate["games"]:
        by_date.setdefault(g["date"], []).append(g)
    for d, games in by_date.items():
        f = open_dir / f"{d}.json"
        book = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        changed = False
        for g in games:
            gid = str(g["id"])
            o = odds.get(g["id"]) or odds.get(gid)
            if not o or is_locked(g):
                if o and gid in book:
                    o["open"] = book[gid]
                continue
            rec = book.setdefault(gid, {"players": {}, "winner": {}, "totals": {}, "at": now})
            for pid, mk in (o.get("players") or {}).items():
                for m, v in mk.items():
                    k = m + "@p" if v and v.get("b") == "p" else m
                    if v and v.get("o") and k not in rec["players"].setdefault(str(pid), {}):
                        rec["players"][str(pid)][k] = v["o"]; changed = True
            for side, v in (o.get("winner") or {}).items():
                if v and side not in rec["winner"]:
                    rec["winner"][side] = v; changed = True
            for line, v in (o.get("totals") or {}).items():
                if v and line not in rec["totals"]:
                    rec["totals"][line] = v; changed = True
            o["open"] = rec
        if changed:
            f.write_text(json.dumps(book, separators=(",", ":")), encoding="utf-8")
    cutoff = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    for f in open_dir.glob("20*.json"):
        if f.stem < cutoff:
            f.unlink()


def write_alerts(slate: dict, out: Path) -> None:
    """Small feed for push alerts: props with an edge against the market, and confirmed starting goalies."""
    now = dt.datetime.now(dt.timezone.utc)
    odds, players, goalies = slate.get("odds") or {}, slate["players"], slate.get("goalies") or {}
    edges, starters, plays = [], [], []
    for g in slate["games"]:
        if is_locked(g) or not g.get("start"):
            continue
        try:
            start = dt.datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (now < start < now + dt.timedelta(hours=20)):
            continue
        if start < now + dt.timedelta(hours=12):
            # game-day alerts for players people follow: everyone projected to play, with goal chance and odds
            po_ = ((odds.get(g["id"]) or odds.get(str(g["id"])) or {}).get("players") or {})
            for pr in g["props"]:
                pl = players.get(str(pr["id"])) or players.get(pr["id"])
                if not pl or not pl.get("active") or "g" not in pr["p"]:
                    continue
                bo = (po_.get(pr["id"]) or po_.get(str(pr["id"])) or {}).get("g") or {}
                plays.append({"gid": g["id"], "pid": pr["id"], "name": pl["name"], "team": pr["team"], "opp": pr["opp"],
                              "home": pr["home"], "start": g["start"], "p": round(pr["p"]["g"], 4), "odds": bo.get("o")})
        po = ((odds.get(g["id"]) or odds.get(str(g["id"])) or {}).get("players") or {})
        for pr in g["props"]:
            pl = players.get(str(pr["id"])) or players.get(pr["id"])
            if not pl or not pl.get("active"):
                continue
            for m, b in (po.get(pr["id"]) or po.get(str(pr["id"])) or {}).items():
                if not b or not b.get("o") or m not in pr["p"]:
                    continue
                ev = pr["p"][m] * b["o"] - 1
                if ev >= 0.05:
                    edges.append({"gid": g["id"], "pid": pr["id"], "name": pl["name"], "team": pr["team"], "opp": pr["opp"],
                                  "home": pr["home"], "m": m, "odds": b["o"], "p": round(pr["p"][m], 4), "ev": round(ev, 4),
                                  "start": g["start"]})
        for t in (g["home"], g["away"]):
            st = (g.get("starter_status") or {}).get(t) or ""
            gid_ = (g.get("starters") or {}).get(t)
            if "confirm" in st.lower() and "unconfirm" not in st.lower() and gid_:
                gk = goalies.get(str(gid_)) or goalies.get(gid_) or {}
                starters.append({"gid": g["id"], "team": t, "opp": g["away"] if t == g["home"] else g["home"],
                                 "name": gk.get("name", ""), "start": g["start"]})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"built": now.isoformat(timespec="minutes"), "edges": edges, "goalies": starters, "plays": plays},
                              separators=(",", ":")), encoding="utf-8")


def write_closing(frozen_dir: Path, out: Path) -> None:
    """Closing player odds (last pre-game snapshot) for the last few days: {game: {player: {market: odds}}}."""
    res = {}
    today = nhl_today()
    for f in [x for x in sorted(frozen_dir.glob("20*.json")) if x.stem <= today][-4:]:
        for gid, snap in json.loads(f.read_text(encoding="utf-8")).items():
            pl = ((snap.get("odds") or {}).get("players") or {})
            res[gid] = {pid: {m: v.get("o") for m, v in mk.items() if v and v.get("o")} for pid, mk in pl.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")


def team_logos(src: Source, slate: dict, logo_dir: Path) -> dict:
    import base64
    teams = sorted({g["home"] for g in slate["games"]} | {g["away"] for g in slate["games"]})
    out = {}
    for t in teams:
        v = {}
        for variant in ("dark", "light"):
            svg = src.logo(t, variant, logo_dir)
            if svg:
                v[variant] = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
        if v:
            out[t] = v
    return out


LANDING = Path(__file__).resolve().parent / "landing.html"


def render_landing(slate: dict, out_dir: Path, app_url: str = "./") -> Path:
    """Public landing page: next slate's games (score/win chance only), previews if written."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    upcoming = [g for g in slate["games"] if g.get("state") in (None, "FUT", "PRE")]
    dates = sorted({g["date"] for g in upcoming})
    day = dates[0] if dates else slate["date"]
    games = []
    for g in sorted([g for g in upcoming if g["date"] == day], key=lambda x: x.get("start") or ""):
        d = g["default"]
        games.append({"id": g["id"], "home": g["home"], "away": g["away"], "start": g.get("start"), "venue": g.get("venue"),
                      "mh": d.get("proj_h", 3.0), "ma": d.get("proj_a", 3.0), "ph": d["p_home"], "pa": d["p_away"],
                      "exp": d["exp_total"]})
    pdate, previews = pick_previews(day, slate)
    teams = {x for g in games for x in (g["home"], g["away"])}
    teams |= {x for p in previews for x in (p.get("home"), p.get("away")) if x}
    by_id = {str(g["id"]): g for g in slate["games"]}
    for p in previews:
        if str(p["id"]) in by_id:
            p["game"] = game_summary(by_id[str(p["id"])])
    record = None
    try:   # best picks record summary (main picks, graded ones only) for the landing card
        rows = json.loads((out_dir / "bestpicks.json").read_text(encoding="utf-8")).get("rows") or []
        done = [r for r in rows if r.get("k") == "all" and r.get("r") in ("w", "l")]
        live = [r for r in rows if r.get("k") == "all" and r.get("r") in ("w", "l", "p")]
        if done:
            u = sum(r["u"] for r in done)
            record = {"n": len(done), "w": sum(r["r"] == "w" for r in done), "u": round(u, 2), "roi": round(u / len(done), 4),
                      "clv": round(sum(r.get("clv") or 0 for r in live) / len(live), 4), "since": min(r["d"] for r in rows),
                      "v": sum(1 for r in rows if r.get("k") == "all" and r.get("r") == "v")}
    except Exception:
        record = None
    solid = None
    try:   # Triage ledger: every bet graded Solid at the lock (the landing card shows this record)
        rows = json.loads((out_dir / "ledger.json").read_text(encoding="utf-8")).get("rows") or []
        # blocked shots are graded but kept out of the headline record until ~50 graded blocks bets prove them
        sr = [r for r in rows if r.get("gr") == "S" and not (r["m"][0] == "b" and r["m"][1:2].isdigit())]
        done = [r for r in sr if r.get("r") in ("w", "l")]
        if done:
            u = sum(r["u"] for r in done)
            sh = [r["od"] / r["sf"] - 1 for r in sr if r.get("sf") and r.get("r") in ("w", "l", "p")]
            solid = {"n": len(done), "w": sum(r["r"] == "w" for r in done), "u": round(u, 2), "roi": round(u / len(done), 4),
                     "hit": round(sum(r["r"] == "w" for r in done) / len(done), 4), "exp": round(sum(r["p"] for r in done) / len(done), 4),
                     "sh": round(sum(sh) / len(sh), 4) if sh else None, "since": min(r["d"] for r in rows)}
    except Exception:
        solid = None
    payload = clean({"date": day, "games": games, "previews": previews, "built": now, "record": record, "solid": solid,
                     "logos": {k: v for k, v in (slate.get("logos") or {}).items() if k in teams}})
    js = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = LANDING.read_text(encoding="utf-8").replace("/*__DATA__*/null", js).replace("APP_URL", app_url)
    html = version_assets(html)
    out = out_dir / "landing.html"
    out.write_text(html, encoding="utf-8")
    return out


PREVIEWS = Path(__file__).resolve().parent / "previews.html"


def game_summary(g: dict) -> dict:
    d = g["default"]
    return {"id": g["id"], "home": g["home"], "away": g["away"], "start": g.get("start"), "venue": g.get("venue"),
            "mh": d.get("proj_h", 3.0), "ma": d.get("proj_a", 3.0), "ph": d["p_home"], "pa": d["p_away"],
            "exp": d["exp_total"]}


def version_assets(html: str) -> str:
    """add ?v=<content hash> to the landing page's screenshots so replaced images show up right away (assets are cached a week)"""
    import hashlib
    import re

    def rep(m):
        f = ROOT / "site-assets" / m.group(1)
        return f'assets/{m.group(1)}?v={hashlib.md5(f.read_bytes()).hexdigest()[:8]}' if f.exists() else m.group(0)
    return re.sub(r"assets/([a-z0-9-]+\.(?:webp|mp4|jpg))(?!\?)", rep, html)


def photo_version(p: dict) -> dict:
    """add ?v=<content hash> to the photo so browsers don't keep showing an old image after it is replaced
    (assets are cached for a week)"""
    import hashlib
    ph = p.get("photo") or {}
    src = ph.get("src") or ""
    f = ROOT / "site-assets" / src.replace("assets/", "", 1).split("?")[0]
    if src.startswith("assets/") and f.exists():
        ph["src"] = src.split("?")[0] + "?v=" + hashlib.md5(f.read_bytes()).hexdigest()[:8]
    return p


def pick_previews(day: str, slate: dict | None = None) -> tuple[str | None, list]:
    """Previews for the first date >= day that has a file (else the latest file)."""
    files = sorted((ROOT / "data" / "previews").glob("20*.json"))
    if not files:
        return None, []
    f = next((x for x in files if x.stem >= day), files[-1])
    previews = [photo_version(p) for p in json.loads(f.read_text(encoding="utf-8")).get("previews", [])]
    if slate:
        by_id = {str(g["id"]): g for g in slate["games"]}
        for p in previews:
            g = by_id.get(str(p["id"]))
            if g:
                p["home"], p["away"] = g["home"], g["away"]
    return f.stem, previews


def render_previews(slate: dict, out_dir: Path, app_url: str = "app/") -> Path:
    today = nhl_today()
    pdate, previews = pick_previews(today, slate)
    by_id = {str(g["id"]): g for g in slate["games"]}
    games = {str(p["id"]): game_summary(by_id[str(p["id"])]) for p in previews if str(p["id"]) in by_id}
    teams = {x for g in games.values() for x in (g["home"], g["away"])}
    payload = clean({"date": pdate, "previews": previews, "games": games,
                     "logos": {k: v for k, v in (slate.get("logos") or {}).items() if k in teams}})
    js = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = PREVIEWS.read_text(encoding="utf-8").replace("/*__DATA__*/null", js).replace("APP_URL", app_url)
    out = out_dir / "previews.html"
    out.write_text(html, encoding="utf-8")
    return out


def app_previews(slate: dict) -> dict:
    """game id -> written preview (without the source list) for every game on the slate that has one"""
    out = {}
    for d in sorted({g["date"] for g in slate["games"]}):
        f = ROOT / "data" / "previews" / f"{d}.json"
        if not f.exists():
            continue
        try:
            for p in json.loads(f.read_text(encoding="utf-8")).get("previews", []):
                out[str(p["id"])] = {k: p[k] for k in ("en", "sv", "photo", "slug") if k in photo_version(p)}
        except Exception:
            pass
    return out


def render(slate: dict, graded: dict | None, out_dir: Path, demo: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = clean({**slate, "graded": graded, "config": CONFIG, "demo": demo, "previews": app_previews(slate),
                     "built": dt.datetime.now().isoformat(timespec="minutes")})
    js = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", js)
    dated = out_dir / f"nhl-props-{slate['date']}{'-demo' if demo else ''}.html"
    dated.write_text(html, encoding="utf-8")
    (out_dir / ("index-demo.html" if demo else "index.html")).write_text(html, encoding="utf-8")
    return dated


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nhlprops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="download data, run the model, write the dashboard")
    b.add_argument("--date", default=nhl_today(),
                   help="slate date (NHL / North American date). Default: today in New York")
    b.add_argument("--no-open", action="store_true")
    b.add_argument("--offline", action="store_true", help="use cached data only")
    b.add_argument("--demo", action="store_true", help="simulated data, no internet")
    gp = sub.add_parser("grade", help="grade saved predictions against final results")
    gp.add_argument("--demo", action="store_true")
    sub.add_parser("check", help="test that every data source answers and has the expected columns")
    pg = sub.add_parser("pregame", help="exit 0 and print run=true if a game starts within the window (for the hourly job)")
    pg.add_argument("--window", type=int, default=75, help="minutes before puck drop (a second refresh runs at 35)")
    bt = sub.add_parser("backtest", help="replay past seasons game by game (does not touch the website)")
    bt.add_argument("--seasons", default="", help="comma-separated start years, default: the two previous seasons")
    bt.add_argument("--totals", action="store_true", help="test game totals instead of player props")
    bt.add_argument("--hb", action="store_true", help="test hits and blocked-shots props")
    bt.add_argument("--shots", action="store_true", help="test team shots and goalie saves")
    bt.add_argument("--attempts", action="store_true", help="test shot attempts vs shots for shots-on-goal props")
    bt.add_argument("--markets", action="store_true", help="test game markets (1X2, puck line, team totals, BTTS, 1st period)")
    bt.add_argument("--context", action="store_true", help="test referees and rest/travel")
    bt.add_argument("--hot", action="store_true", help="test player hot streaks vs the model")
    bt.add_argument("--form", action="store_true", help="test a recent-form term for player props")
    bt.add_argument("--goalies", action="store_true", help="test goalie rating weights and regression")
    bt.add_argument("--extra", action="store_true", help="test multi-point, PIM, PP points and faceoff props")
    bt.add_argument("--oppos", action="store_true", help="test what the opponent allows by position")
    bt.add_argument("--depth", action="store_true", help="test depth players / rookies and a role-based prior")
    bt.add_argument("--depthcal", action="store_true", help="test a goal/point correction for low-minute forwards")
    bt.add_argument("--roadtrip", action="store_true", help="test the last game of road trips and trip length")
    bt.add_argument("--b2b", action="store_true", help="fit the back-to-back adjustment")
    bt.add_argument("--schedule", action="store_true", help="test busy stretches, look-ahead and late-season stakes")
    bt.add_argument("--stakes", action="store_true", help="test late-season stakes against the player model")
    bt.add_argument("--ppp", action="store_true", help="test power-play points calibration")
    bt.add_argument("--a1", action="store_true", help="test weighting primary assists more than secondary")
    bt.add_argument("--proj", action="store_true", help="test the player season projection ranges")
    bt.add_argument("--due", action="store_true", help="test whether unlucky ('due') players score more than the model says")
    bt.add_argument("--onice", action="store_true", help="test on-ice xGF (useful plus/minus) for assists and points")
    bt.add_argument("--tail", action="store_true", help="test multi-point / 2+ goal long-shot probabilities")
    bt.add_argument("--shooters", action="store_true", help="test whether high-volume shooters are underrated on shots")
    bt.add_argument("--usage", action="store_true", help="test trusting this season more when a player's shooting breaks from his history")
    bt.add_argument("--homeroad", action="store_true", help="test team-specific home ice")
    bt.add_argument("--season", action="store_true", help="test the season simulation (playoff chances, game importance)")
    bt.add_argument("--teamluck", action="store_true", help="test whether team luck affects player props")
    bt.add_argument("--matchups", action="store_true", help="test line matchups (which defence pairs forwards face) from shift charts")
    bt.add_argument("--ptsluck", action="store_true", help="test whether team points luck predicts future team results")
    args = ap.parse_args(argv)

    data_dir = ROOT / "data"
    if getattr(args, "demo", False):
        from . import demo
        cache = data_dir / "demo_cache"
        date = getattr(args, "date", None)
        if date and (not (cache / "demo_meta.json").exists()
                     or json.loads((cache / "demo_meta.json").read_text())["date"] != date):
            print("Generating simulated data ...")
            demo.generate(cache, args.date)
        src = Source(cache, offline=True)
        hist_dir = data_dir / "demo_history"
    else:
        src = Source(data_dir / "cache", offline=getattr(args, "offline", False),
                     interval=CONFIG["request_interval_s"])
        hist_dir = data_dir / "history"

    if args.cmd == "check":
        return check(src)
    if args.cmd == "pregame":
        return pregame(src, data_dir / "pregame.json", args.window)
    if args.cmd == "backtest":
        from .backtest import run as run_backtest
        cur = season_start_for(nhl_today())
        seasons = [int(x) for x in args.seasons.split(",") if x.strip()] or [cur - 2, cur - 1]
        if args.ptsluck:
            from .backtest_ptsluck import run as run_pl
            run_pl(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.matchups:
            from .backtest_matchups import run as run_mu
            run_mu(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.teamluck:
            from .backtest_teamluck import run as run_tl
            run_tl(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.season:
            from .backtest_season import run as run_ss
            run_ss(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.schedule:
            from .backtest_schedule import run as run_sc
            run_sc(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.stakes:
            from .backtest_stakes import run as run_stk
            run_stk(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.ppp:
            from .backtest_ppp import run as run_ppp
            run_ppp(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.a1:
            from .backtest_a1 import run as run_a1
            run_a1(src, seasons, CONFIG, ROOT / "backtest")
        if args.proj:
            from .backtest_proj import run as run_proj
            run_proj(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.due:
            from .backtest_due import run as run_due
            run_due(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.onice:
            from .backtest_onice import run as run_oi
            run_oi(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.tail:
            from .backtest_tail import run as run_tl2
            run_tl2(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.shooters:
            from .backtest_shooters import run as run_sh
            run_sh(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.usage:
            from .backtest_usage import run as run_us
            run_us(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.homeroad:
            from .backtest_homeroad import run as run_hr
            run_hr(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.b2b:
            from .backtest_b2b import run as run_bb
            run_bb(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.roadtrip:
            from .backtest_roadtrip import run as run_rt
            run_rt(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.depthcal:
            from .backtest_depthcal import run as run_dc
            run_dc(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.depth:
            from .backtest_depth import run as run_dp
            run_dp(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.oppos:
            from .backtest_oppos import run as run_op
            run_op(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.extra:
            from .backtest_extra import run as run_ex
            run_ex(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.goalies:
            from .backtest_goalies import run as run_gk
            run_gk(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.form:
            from .backtest_form import run as run_form
            run_form(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.hot:
            from .backtest_hot import run as run_hot
            run_hot(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.context:
            from .backtest_context import run as run_cx
            run_cx(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.markets:
            from .backtest_markets import run as run_mk
            run_mk(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.attempts:
            from .backtest_attempts import run as run_at
            run_at(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.shots:
            from .backtest_shots import run as run_sh
            run_sh(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.hb:
            from .backtest_hb import run as run_hb
            run_hb(src, seasons, CONFIG, ROOT / "backtest")
            return 0
        if args.totals:
            from .backtest_totals import run as run_totals
            run_totals(src, seasons, CONFIG, ROOT / "backtest")
        else:
            run_backtest(src, seasons, CONFIG, ROOT / "backtest")
        return 0
    if args.cmd == "grade":
        if not hist_dir.exists() or not any(hist_dir.glob("20*.json")):
            print("No saved predictions yet. Run `build` before games start, then `grade` after they finish.")
            return 0
        grade(src, hist_dir)
        print("Run `build` again to see the results on the Track record tab.")
        return 0

    raw = gather(src, args.date, CONFIG)
    if not raw["games"]:
        if raw["errors"]:
            print("Could not download the NHL schedule:", raw["errors"][0])
            print("Check your internet connection, then run `python -m nhlprops check`.")
        else:
            print(f"No NHL games found for {args.date}. Try another --date.")
        return 1
    raw["pbp"] = {}
    if not args.demo:
        try:   # this season's play-by-play: shot maps, power-play points, faceoffs, linemates, penalties
            from .shots import collect as collect_pbp
            raw["pbp"] = collect_pbp(src, raw["league_games"], raw["season"], data_dir / "shots", CONFIG["workers"])
        except Exception as e:
            print("Play-by-play skipped:", e)
    slate = build_slate(raw, CONFIG)
    if args.demo:
        slate["odds"] = demo_odds(slate)
    else:
        from .odds import fetch_odds, fetch_pinnacle, merge_second
        try:
            slate["odds"] = fetch_odds(src, slate["games"], raw["skaters"])
        except Exception as e:  # odds are optional
            print("Odds unavailable:", e)
            slate["odds"] = {}
        try:   # second, sharper price; also fills markets the main feed lacks
            merge_second(slate["odds"], fetch_pinnacle(src, slate["games"], raw["skaters"]))
        except Exception as e:
            print("Sharp-book odds unavailable:", e)
        if not args.demo:
            try:   # newest Stoiximan prices pushed from the owner's browser (written as a manual-odds file)
                from .stx import pull as stx_pull
                stx_pull(data_dir, slate["games"], slate["players"])
            except Exception as e:  # never break the build
                print("Stoiximan sync skipped:", e)
        try:   # prices copied by hand from another sportsbook fill the markets the feeds lack
            from .odds import merge_manual
            merge_manual(slate["odds"], data_dir, slate["games"])
        except Exception as e:
            print("Manual odds skipped:", e)
        try:   # public betting splits (tickets and money per side), kept with the odds so they lock with them
            from .odds import fetch_public
            for gid, rec in fetch_public(src, slate["games"]).items():
                slate["odds"].setdefault(gid, {"book": "Bookmaker", "players": {}, "totals": {}, "winner": {}, "fetched": None})["public"] = rec
        except Exception as e:
            print("Public betting unavailable:", e)
    track_open(slate, data_dir / ("demo_odds_open" if args.demo else "odds_open"))
    freeze_started(slate, data_dir / ("demo_frozen" if args.demo else "frozen"))
    if not args.demo:
        try:
            save_public(slate, data_dir / "public")
        except Exception as e:  # never break the build
            print("Public betting archive skipped:", e)
    lineup_roles(slate, data_dir / ("demo_lineups" if args.demo else "lineups"))
    write_closing(data_dir / ("demo_frozen" if args.demo else "frozen"), ROOT / "output" / "closing.json")
    try:
        write_alerts(slate, ROOT / "output" / "alerts.json")
    except Exception as e:  # alerts are optional; never break the build
        print("Alerts feed skipped:", e)
    save_history(slate, hist_dir)  # after odds, so the book price at prediction time is kept
    if not args.demo:
        try:   # best picks as they stand now (games not locked yet); the last refresh before the lock is official
            from .bestpicks import snapshot as bp_snapshot
            bp_snapshot(slate, data_dir / "bestpicks")
        except Exception as e:  # never break the build
            print("Best picks snapshot skipped:", e)
        try:   # every value bet with its Triage grade, as it stands now; the last refresh before the lock is official
            from .ledger import snapshot as led_snapshot
            led_snapshot(slate, data_dir / "ledger")
        except Exception as e:  # never break the build
            print("Triage ledger snapshot skipped:", e)
    if raw.get("pbp"):
        try:
            from .shots import player_file
            player_file(raw["pbp"], ROOT / "output" / "shots.json")
        except Exception as e:  # shot maps are optional; never break the build
            print("Shot maps skipped:", e)
    if not args.demo:
        try:   # playoff / round / Cup chances and how much each game matters (season simulation)
            from .season import live as season_live
            slate["futures"] = season_live(raw, slate, CONFIG)
            lock_stakes(slate, data_dir / ("demo_frozen" if args.demo else "frozen"))
            try:
                from .odds import fetch_futures
                slate["futures"]["odds"] = fetch_futures(src)
            except Exception as e:  # futures odds are optional
                print("Futures odds unavailable:", e)
        except Exception as e:  # never break the build
            print("Season simulation skipped:", e)
        try:   # every skater's season simulated: ranges, milestones, trophy chances (Futures, Explorer, profiles)
            from .proj import project
            tgp = {t: v.get("gp", 0) for t, v in ((slate.get("futures") or {}).get("teams") or {}).items()}
            if tgp:
                slate["proj"] = project(slate["players"], tgp)
                print(f"Player projections: {len(slate['proj'])} skaters")
                from .proj import project_goalies
                slate["gproj"] = project_goalies(slate.get("goalies") or {}, tgp)
                print(f"Goalie projections: {len(slate['gproj'])} goalies")
        except Exception as e:  # never break the build
            print("Player projections skipped:", e)
        try:   # season-award prices (Stoiximan), shown against the model's chances in Futures -> Awards
            from .stx import awards as stx_awards
            aw = stx_awards(data_dir, slate["players"], slate.get("goalies"))
            if aw:
                slate["awards"] = aw
                print(f"Award prices: {', '.join(f'{k} {len(v)}' for k, v in aw['m'].items())}")
        except Exception as e:  # never break the build
            print("Award prices skipped:", e)
    if not args.demo:
        try:   # three seasons of game-by-game history per player (loaded by the site on demand)
            from .history import build as build_history
            build_history(raw, slate, src, ROOT / "output", data_dir / "cache" / "derived", CONFIG["workers"])
        except Exception as e:  # never break the build
            print("Game-by-game history skipped:", e)
    if not args.demo:
        try:   # the last 7 days as they looked before puck drop, with results (opened from the Games tab)
            from .archive import build as build_archive
            slate["archive_dates"] = build_archive(slate, src, data_dir, ROOT / "output", raw.get("pbp"))
        except Exception as e:  # the archive is optional; never break the build
            print("Archive skipped:", e)
    if not args.demo:
        try:   # the public best picks record (locked picks, graded once final)
            from .bestpicks import grade as bp_grade
            bp_grade(src, data_dir / "bestpicks", ROOT / "output" / "bestpicks.json")
        except Exception as e:  # never break the build
            print("Best picks record skipped:", e)
        try:   # the Triage ledger: every value bet at the lock, all grades, graded once final
            from .ledger import grade as led_grade
            led_grade(src, data_dir / "ledger", ROOT / "output" / "ledger.json")
        except Exception as e:  # never break the build
            print("Triage ledger skipped:", e)
    graded_path = hist_dir / "graded.json"
    graded = json.loads(graded_path.read_text()) if graded_path.exists() else None
    slate["logos"] = team_logos(src, slate, data_dir / ("demo_logos" if args.demo else "logos"))
    out = render(slate, graded, ROOT / "output", args.demo)
    render_landing(slate, ROOT / "output", app_url="app/")
    render_previews(slate, ROOT / "output", app_url="app/")
    print(f"Dashboard written: {out}")
    if raw["errors"]:
        print(f"Note: {len(raw['errors'])} downloads failed - see data/errors.log")
        (data_dir / "errors.log").write_text("\n".join(raw["errors"]), encoding="utf-8")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def pregame(src: Source, state_file: Path, window: int) -> int:
    """Pre-game refresh gate: one rebuild per puck-drop time, within `window` minutes before it."""
    now = dt.datetime.now(dt.timezone.utc)
    today = nhl_today()
    js = src.json(f"https://api-web.nhle.com/v1/schedule/{today}", f"pregame/schedule_{today}.json", 0.0)
    starts = set()
    for day in (js or {}).get("gameWeek", [])[:1]:
        for g in day.get("games", []):
            if g.get("gameState") in ("FUT", "PRE") and g.get("startTimeUTC"):
                starts.add(g["startTimeUTC"])
    done = json.loads(state_file.read_text()) if state_file.exists() else []
    due = []
    for s in sorted(starts):   # three refreshes per puck drop: ~1 hour before, ~30 min before, and ~15 min before (warmups)
        mins = (dt.datetime.fromisoformat(s.replace("Z", "+00:00")) - now).total_seconds() / 60
        for w in (window, 35, 20):
            if 0 < mins <= w and f"{s}|{w}" not in done:
                due.append(f"{s}|{w}")
    # post-game refresh: once every game of a night is final, rebuild so results show and picks get graded
    yday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    for d in (yday, today):
        sj = src.json(f"https://api-web.nhle.com/v1/score/{d}", f"pregame/score_{d}.json", 0.0) or {}
        gs = sj.get("games") or []
        if gs and all(g.get("gameState") in ("OFF", "FINAL") for g in gs) and f"final|{d}" not in done:
            due.append(f"final|{d}")
    # goalie watch: on game days, from ~7 hours before the first puck drop until the last one, every check fetches
    # the starting-goalie sources and rebuilds as soon as any call changes (confirmed, swapped, likelihood moved)
    watch_file = state_file.with_name("goalie_watch.json")
    if starts:
        first = min(dt.datetime.fromisoformat(s_.replace("Z", "+00:00")) for s_ in starts)
        last = max(dt.datetime.fromisoformat(s_.replace("Z", "+00:00")) for s_ in starts)
        if first - dt.timedelta(hours=7) <= now <= last:
            try:
                from .news import goalie_signature
                sig = goalie_signature(src, today)
                old = (json.loads(watch_file.read_text()) if watch_file.exists() else {}).get(today) or {}
                if sig and sig != old:
                    changed = sorted(k for k in set(sig) | set(old) if sig.get(k) != old.get(k))
                    if not due:
                        due.append("goalies")
                    print(f"Goalie watch: {len(changed)} change(s): {', '.join(changed[:8])}")
                else:
                    print("Goalie watch: no change")
                if sig and (due or sig != old):   # saved with every rebuild, so the next check compares with what's published
                    watch_file.write_text(json.dumps({today: sig}))
            except Exception as e:  # noqa: BLE001  (a source down must never block the normal refreshes)
                print("Goalie watch skipped:", e)
    run = bool(due)
    if run:
        state_file.write_text(json.dumps(sorted(set(done) | set(d_ for d_ in due if d_ != "goalies"))[-120:]))
    print(f"Pre-game check {now:%H:%M} UTC: starts today {sorted(starts)} -> {'refresh for ' + ', '.join(due) if run else 'nothing due'}")
    import os
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"run={'true' if run else 'false'}\n")
    return 0


def check(src: Source) -> int:
    today = dt.date.today().isoformat()
    cur = season_start_for(today)
    ok = True

    def report(name, good, detail=""):
        nonlocal ok
        ok &= good
        print(f"  [{'OK ' if good else 'FAIL'}] {name} {detail}")

    print("Checking data sources ...")
    s = src.schedule(today)
    report("NHL schedule", bool(s and "gameWeek" in s))
    r = src.roster("TOR")
    report("NHL roster", bool(r and r.get("forwards")))
    c = src.club_schedule("TOR", cur, True)
    report("NHL club schedule", bool(c and c.get("games")))
    if r and r.get("forwards"):
        pid = r["forwards"][0]["id"]
        land = src.landing(pid)
        report("NHL player landing", bool(land and land.get("careerTotals")),
               "" if land and land.get("careerTotals") else "(no careerTotals)")
        gl = src.game_log(pid, cur - 1, False)
        has = gl and gl.get("gameLog") and all(k in gl["gameLog"][0] for k in ("goals", "shots", "toi", "gameDate"))
        report("NHL game log", bool(has))
    need = {
        "skaters": ["playerId", "situation", "icetime", "I_F_shotsOnGoal", "I_F_goals", "I_F_xGoals",
                    "I_F_primaryAssists", "I_F_secondaryAssists"],
        "goalies": ["playerId", "situation", "icetime", "xGoals", "goals", "ongoal"],
        "teams": ["team", "situation", "iceTime", "xGoalsFor", "xGoalsAgainst", "goalsFor", "goalsAgainst",
                  "shotsOnGoalFor", "shotsOnGoalAgainst", "highDangerShotsFor"],
    }
    for kind, cols in need.items():
        df = src.moneypuck(kind, cur - 1, False)
        if df is None:
            report(f"MoneyPuck {kind}", False, "(download failed)")
            continue
        miss = [c for c in cols if c not in df.columns]
        report(f"MoneyPuck {kind}", not miss, f"missing: {miss}" if miss else f"({len(df)} rows)")
    if src.errors:
        print("Errors:", *src.errors[:5], sep="\n  ")
    print("All good." if ok else "Some checks failed - send me the output above and I'll adapt the code.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
