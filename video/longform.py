"""evenout long-form YouTube video: "Tonight's slate breakdown" (16:9, 1920x1080, 30 fps, music, no voice-over).

    py video/longform.py --date 2026-11-17                          # full render
    py video/longform.py --date 2026-11-17 --preview beats          # one still per beat (end of its animation)
    py video/longform.py --date 2026-11-17 --preview 5,40.5,120     # stills at these seconds
    py video/longform.py --date 2026-11-17 --games 2026020333,2026020342 --max-seconds 60   # override + quick test

Story (the on-screen text carries it, one idea per beat, >= 0.35 s per word and >= 4 s per block):
  cold open (3 games to watch) -> the whole slate (start times ET / CET, model win chance, expected goals) -> why these
  games -> per game: matchup card, the model's view (win chance, expected score), form & style (5v5 xGF%, shots, pace,
  goals for / against, PDO, power play / penalty kill), goalies (save %, GSAx), 2-3 players to watch (Heat Index,
  season line, point chance, line at 5v5), head-to-head & playoff stakes, what decides it -> recap -> evenout.app.
Stats and model numbers only: never odds, sportsbooks, picks or grades.

Data: the built app page (default origin/site:app/index.html) via longform_extract.js, with the Pro patch merged in when
there is one (--pro, default output/pro/latest.json; point chances need it, everything else degrades gracefully).
Output in --out/<DATE>/: slate-<DATE>.mp4, slate-<DATE>-thumb.jpg (1280x720) and the "slate" item (YouTube only) in
manifest.json; video/upload.py posts it. Rendering skips frames where nothing moves (the template flags motion), so a
text-heavy 7-minute video needs only a fraction of the screenshots.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import music  # noqa: E402
from render import TEAM, short_name, site_html  # noqa: E402
from series import heat_level, load_page_data  # noqa: E402

W, H, FPS = 1920, 1080, 30
SITE = "evenout.app"
ET = ZoneInfo("America/New_York")
SE = ZoneInfo("Europe/Stockholm")
SEC_PER_WORD, MIN_BEAT = 0.35, 4.0
SERIES = "slate"


def color(t: str) -> str:
    return TEAM.get(t, "#ff4fa8")


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def pc(x: float) -> str:
    return f"{round(x * 100)}%"


def times(start: str) -> tuple[str, str]:
    t = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    e, s = t.astimezone(ET), t.astimezone(SE)
    return (f"{e.strftime('%I:%M %p').lstrip('0')} ET", f"{s.strftime('%H:%M')} {s.tzname()}")


def tline(start: str) -> str:
    a, b = times(start)
    return f"{a} · {b}"


def nick(T: dict) -> str:
    return short_name(T.get("name") or "") or T["abbr"]


def words(x, read: float = 0) -> float:
    """Reading load of a beat in words: sentences and headings (READ_KEYS) count fully, numbers / tiles / labels that
    are only glanced at count a tenth (numbers count as words). Colours, ids and layout keys don't count."""
    if isinstance(x, str):
        n = len(re.sub(r"\*\*|<[^>]+>", " ", x).split())
        return n * (read or GLANCE)
    if isinstance(x, dict):
        return sum(words(v, read or (1 if k in READ_KEYS else .5 if k in HALF_KEYS else 0)) for k, v in x.items() if k not in SKIP_KEYS)
    if isinstance(x, (list, tuple)):
        return sum(words(v, read) for v in x)
    return 0


GLANCE = 0.1
READ_KEYS = {"take", "why", "wsub", "s", "line", "t1", "t2", "sub", "h2hSum", "skSum", "lines", "noH2h", "vs", "flag"}
HALF_KEYS = {"title", "lab", "kicker", "h", "names", "chips"}            # big headings and row labels: read at a glance
SKIP_KEYS = {"kind", "ac", "hc", "c", "color", "tagc", "chap", "sec", "noLeave", "noChrome", "ci", "a", "b", "pa", "fa", "fh",
             "better", "xt", "pick", "num", "last", "l5", "id", "disc", "t3"}


# ------------------------------------------------------------------ game choice
def interest(g: dict) -> dict:
    """0..~1.5 per storyline; the video's games are the ones with the strongest stories."""
    pl = [p for t in g["players"] for p in g["players"][t]]
    heat = [p for p in pl if p.get("heat") and p["heat"]["n"] >= 8 and (p.get("toi") or 0) >= 13]
    hottest = max(heat, key=lambda p: p["heat"]["v"], default=None)
    pts = [p for p in pl if p.get("pt")]
    toppt = max(pts, key=lambda p: p["pt"], default=None)
    gk = [x for x in g["goalies"].values() if x and x.get("gp", 0) >= 5]
    gap = abs((gk[0].get("gsax") or 0) - (gk[1].get("gsax") or 0)) if len(gk) == 2 else 0
    st = g.get("stakes") or {}
    swing = max((v.get("swing") or 0 for v in st.values()), default=0)
    T = g["teams"]
    same_div = T[g["home"]].get("div") and T[g["home"]].get("div") == T[g["away"]].get("div")
    p = g["p_home"] if g.get("p_home") is not None else .5
    return {"close": max(0.0, 1 - abs(p - .5) / .12),
            "star": min(1.5, max(0.0, ((hottest or {}).get("heat", {}).get("v", 0) - 2) / 5)),
            "point": min(1.0, max(0.0, ((toppt or {}).get("pt") or 0) - .5) / .3),
            "stakes": min(1.3, swing / .09),
            "goalie": min(1.2, gap / 14),
            "rival": (.45 if same_div else 0) + min(.3, .1 * len(g.get("h2h") or [])),
            "_hottest": hottest, "_toppt": toppt, "_swing": swing}


WEIGHT = {"close": 1.0, "star": .9, "point": .5, "stakes": .9, "goalie": .6, "rival": .45}


def choose(games: list, n: int, force: list | None = None) -> list:
    for g in games:
        g["_int"] = interest(g)
        g["_score"] = sum(WEIGHT[k] * g["_int"][k] for k in WEIGHT)
    chosen = [g for g in games if g["id"] in force] if force else []
    if not chosen:
        chosen = sorted(games, key=lambda g: -g["_score"])[:n]
    # one reason per game, different reasons where possible: greedy over (value, game, storyline)
    pairs = sorted(((WEIGHT[k] * g["_int"][k], i, k) for i, g in enumerate(chosen) for k in WEIGHT), reverse=True)
    used_g, used_k = set(), set()
    for v, i, k in pairs:
        if i in used_g or k in used_k:
            continue
        chosen[i]["_why"] = k
        used_g.add(i); used_k.add(k)
    for g in chosen:
        g.setdefault("_why", "close")
    chosen.sort(key=lambda g: g["start"])
    return chosen


def reason(g: dict) -> tuple[str, str]:
    k, I, T = g["_why"], g["_int"], g["teams"]
    A, Hm = g["away"], g["home"]
    if k == "close":
        return "Closest game on paper", f"The model: **{pc(1 - g['p_home'])}** {A}, **{pc(g['p_home'])}** {Hm}."
    if k == "star" and I["_hottest"]:
        p = I["_hottest"]
        return "Hottest player on the slate", f"**{p['name']}**: {p['heat']['x']} points in his last {p['heat']['n']} games."
    if k == "point" and I["_toppt"]:
        p = I["_toppt"]
        return "Star power", f"**{p['name']}**: {pc(p['pt'])} chance of a point tonight."
    if k == "stakes" and g.get("stakes"):
        t, v = max(g["stakes"].items(), key=lambda kv: kv[1].get("swing") or 0)
        return "Big playoff stakes", f"Win or lose moves **{t}**'s playoff chance by {round(v['swing'] * 100)} points."
    if k == "goalie":
        gk = sorted([x for x in g["goalies"].values() if x], key=lambda x: -(x.get("gsax") or 0))
        if gk:
            return "Goalie storyline", f"**{gk[0]['name']}**: {gk[0]['gsax']:+.1f} goals saved above expected."
    if k == "rival":
        return "Division rivals", f"{T[Hm].get('div') or 'Same'} division: points against a direct rival."
    return "Closest game on paper", f"The model: **{pc(1 - g['p_home'])}** {A}, **{pc(g['p_home'])}** {Hm}."


# ------------------------------------------------------------------ text per chapter
def l5_letters(T: dict) -> list:
    out = []
    for r in reversed(T.get("recent") or []):
        out.append("W" if r["gf"] > r["ga"] else "O" if r.get("x") in ("OT", "SO") else "L")
    return out[-5:] or list(T.get("last5") or "")


def streak_txt(T: dict) -> str:
    n, k = T.get("streak") or 0, T.get("streak_kind")
    if not n or not k:
        return ""
    word = {"W": "Won", "L": "Lost", "O": "Lost"}.get(k, "")
    return f"{word} {n} straight" if n >= 2 else ("Won last game" if k == "W" else "Lost last game")


def closeness(p: float, fav: str) -> str:
    d = abs(p - .5)
    if d < .03:
        return "A true coin flip: the model can barely split them."
    if d < .07:
        return f"A slight lean to **{fav}**, but this one is close."
    if d < .12:
        return f"**{fav}** are favourites, but not by much."
    return f"**{fav}** are clear favourites tonight."


def row(lab, va, vh, fa, fh, better, take):
    return {"lab": lab, "va": va, "vh": vh, "fa": max(.03, min(1, fa)), "fh": max(.03, min(1, fh)), "better": better, "take": take}


def scale(v, lo, hi):
    return (v - lo) / (hi - lo) if v is not None else 0


def form_beats(g: dict) -> list:
    A, Hm = g["away"], g["home"]
    TA, TH = g["teams"][A], g["teams"][Hm]
    nA, nH = nick(TA), nick(TH)
    beats = []
    # 1. who drives play: 5v5 share of expected goals, shots, pace
    r = []
    xa, xh = TA.get("xgf_pct5"), TH.get("xgf_pct5")
    if xa and xh:
        d = xh - xa
        take = (f"The **{nH if d > 0 else nA}** create more of the even-strength chances." if abs(d) >= .03
                else "Even at 5-on-5: neither side owns the play.")
        r.append(row("5-on-5 share of chances (xGF%)", pc(xa), pc(xh), scale(xa, .38, .62), scale(xh, .38, .62),
                     "h" if d > 0 else "a", take))
    sa, sh = TA.get("sf"), TH.get("sf")
    if sa and sh:
        d = sh - sa
        take = (f"**{Hm if d > 0 else A}** put {abs(d):.1f} more shots on net per game." if abs(d) >= 2
                else "About the same shot volume.")
        r.append(row("Shots on goal per game", f"{sa:.1f}", f"{sh:.1f}", scale(sa, 22, 36), scale(sh, 22, 36), "h" if d > 0 else "a", take))
    pa_, ph_ = (TA.get("gf_pg") or 0) + (TA.get("ga_pg") or 0), (TH.get("gf_pg") or 0) + (TH.get("ga_pg") or 0)
    if pa_ and ph_:
        hi = max(pa_, ph_)
        take = ("Both teams play high-event hockey." if min(pa_, ph_) >= 6.4 else
                "Both play tight, low-scoring games." if hi <= 5.8 else
                f"**{A if pa_ > ph_ else Hm}** games are the wilder ones.")
        r.append(row("Pace: goals per game, both teams", f"{pa_:.1f}", f"{ph_:.1f}", scale(pa_, 4.5, 7.5), scale(ph_, 4.5, 7.5), None, take))
    if r:
        beats.append({"sec": "Form & style", "title": "Who drives the play", "rows": r, "_type": "drive"})
    # 2. scoring, defence, luck
    r = []
    ga, gh = TA.get("gf_pg"), TH.get("gf_pg")
    if ga and gh:
        d = gh - ga
        take = f"**{Hm if d > 0 else A}** score {abs(d):.1f} more per game." if abs(d) >= .25 else "Similar scoring punch."
        r.append(row("Goals scored per game", f"{ga:.2f}", f"{gh:.2f}", scale(ga, 2, 4.2), scale(gh, 2, 4.2), "h" if d > 0 else "a", take))
    ca, ch = TA.get("ga_pg"), TH.get("ga_pg")
    if ca and ch:
        d = ch - ca
        take = f"**{A if d > 0 else Hm}** allow {abs(d):.1f} fewer per game." if abs(d) >= .25 else "Both defend about equally well."
        r.append(row("Goals allowed per game (lower is better)", f"{ca:.2f}", f"{ch:.2f}", scale(ca, 2, 4.2), scale(ch, 2, 4.2),
                     "a" if d > 0 else "h", take))
    pa2, ph2 = TA.get("pdo"), TH.get("pdo")
    if pa2 and ph2:
        hot = max(((A, pa2), (Hm, ph2)), key=lambda x: x[1])
        cold = min(((A, pa2), (Hm, ph2)), key=lambda x: x[1])
        if hot[1] >= 1.02:
            take = f"**{hot[0]}** are riding the bounces. Runs like that tend to cool off."
        elif cold[1] <= .98:
            take = f"**{cold[0]}** have been unlucky. Better bounces should come."
        else:
            take = "No big luck story: both near normal."
        r.append(row("Luck meter (PDO: shooting % + save %)", f"{pa2:.3f}", f"{ph2:.3f}", scale(pa2, .95, 1.05), scale(ph2, .95, 1.05), None, take))
    if r:
        beats.append({"sec": "Form & style", "title": "Scoring, defence & luck", "rows": r, "_type": "scoring"})
    # 3. special teams (league ranks), cross-matched: each power play against the other side's penalty kill
    RA, RH = TA.get("rank") or {}, TH.get("rank") or {}
    r = []
    n = TA.get("n_teams") or 32
    if RA.get("pp") and RH.get("pk"):
        pp, pk = RA["pp"]["rk"], RH["pk"]["rk"]
        take = (f"Edge **{A}**: a top power play against a weak kill." if pp + 8 <= pk else
                f"Edge **{Hm}**: their kill should handle it." if pk + 8 <= pp else "An even battle on the {A} power play.".replace("{A}", A))
        r.append(row(f"{A} power play vs {Hm} penalty kill · rank of {n}", ordinal(pp), ordinal(pk), (n + 1 - pp) / n, (n + 1 - pk) / n,
                     "a" if pp < pk else "h", take))
    if RH.get("pp") and RA.get("pk"):
        pp, pk = RH["pp"]["rk"], RA["pk"]["rk"]
        take = (f"Edge **{Hm}**: a top power play against a weak kill." if pp + 8 <= pk else
                f"Edge **{A}**: their kill should handle it." if pk + 8 <= pp else f"An even battle on the {Hm} power play.")
        r.append(row(f"{A} penalty kill vs {Hm} power play · rank of {n}", ordinal(pk), ordinal(pp), (n + 1 - pk) / n, (n + 1 - pp) / n,
                     "h" if pp < pk else "a", take))
    if r:
        beats.append({"sec": "Special teams", "title": "Power play vs penalty kill", "rows": r, "_type": "special"})
    return beats


def goalie_beat(g: dict) -> dict | None:
    A, Hm = g["away"], g["home"]

    def one(x, team, opp):
        if not x:
            return None
        st = (x.get("status") or "Projected")
        conf = "confirm" in st.lower()
        tiles = [["SAVE %", f"{x['sv']:.3f}".lstrip("0") if x.get("sv") else "–", None],
                 ["GSAX", f"{x['gsax']:+.1f}" if x.get("gsax") is not None else "–", "var(--pink)" if (x.get("gsax") or 0) > 0 else "var(--orange)"],
                 ["GAMES", str(x.get("gp", 0)), None]]
        lines = []
        if x.get("rk") and x.get("gp", 0) >= 5:
            lines.append(f"**{ordinal(x['rk'])}** of {x['of']} goalies in GSAx")
        if x.get("vs") and x["vs"]["gp"] >= 3:
            lines.append(f"Career vs {opp}: **{x['vs']['sv']:.3f}**".replace("0.", ".") + f" in {x['vs']['gp']} games")
        elif x.get("l3") and x["l3"].get("gp"):
            lines.append(f"Last 3 seasons: **{x['l3']['sv']:.3f}**".replace("0.", ".") + f" in {x['l3']['gp']} games")
        return {"name": x["name"], "num": x.get("num") or "G", "status": st.upper(), "confirmed": conf, "tiles": tiles, "lines": lines[:2]}
    ga, gh = g["goalies"].get(A), g["goalies"].get(Hm)
    if not ga and not gh:
        return None
    take = "Goalie numbers are still thin this early."
    if ga and gh and ga.get("gsax") is not None and gh.get("gsax") is not None:
        d = gh["gsax"] - ga["gsax"]
        best, worst = (gh, ga) if d > 0 else (ga, gh)
        if abs(d) >= 4:
            take = f"Edge in net: **{best['name'].split()[-1]}**, {abs(d):.0f} goals better than {worst['name'].split()[-1]} vs expected."
        else:
            take = "Close in net: both goalies within a few goals of each other."
    return {"kind": "goalies", "sec": "Goalie matchup", "title": "Who's in net", "away": A, "home": Hm,
            "ga": one(ga, A, Hm), "gh": one(gh, Hm, A), "take": take}


def pick_players(g: dict, k: int) -> list:
    out, seen = [], set()
    pl = [(t, p) for t in g["players"] for p in g["players"][t] if p.get("posg") != "G" and (p.get("toi") or 0) >= 12]
    have_pt = any(p.get("pt") for _, p in pl)

    def add(t, p, tag, tagc):
        if p and p["id"] not in seen:
            seen.add(p["id"]); out.append((t, p, tag, tagc))
    by_pt = sorted(pl, key=lambda x: -(x[1].get("pt") or 0) if have_pt else -(x[1]["p"] / max(1, x[1]["gp"])))
    heat = [x for x in pl if x[1].get("heat") and x[1]["heat"]["n"] >= 8]
    hot = max(heat, key=lambda x: x[1]["heat"]["v"], default=None)
    cold = [x for x in heat if x[1]["heat"]["v"] <= -2.5 and (x[1].get("toi") or 0) >= 16]
    cold = min(cold, key=lambda x: x[1]["heat"]["v"], default=None)
    if by_pt:
        add(*by_pt[0], "Top point chance" if have_pt else "Top scorer", "var(--pink)")
    if hot and hot[1]["heat"]["v"] >= 1.5:
        add(*hot, "Hottest on the ice", "var(--orange)")
    if cold and len(out) < k:
        add(*cold, "Ice cold", "var(--purple)")
    # make sure both teams show up
    teams = {t for t, *_ in out}
    for t, p in by_pt:
        if len(out) >= k:
            break
        if t not in teams or len(teams) == 2:
            add(t, p, "Top point chance" if have_pt else "Top scorer", "var(--pink)")
            teams.add(t)
    return out[:k]


def player_beat(g: dict, t: str, p: dict, tag: str, tagc: str) -> dict:
    opp = g["home"] if t == g["away"] else g["away"]
    h = p.get("heat")
    heat = None
    if h and h.get("n", 0) >= 8:
        heat = {"v": h["v"], "n": h["n"], "level": heat_level(h["v"]),
                "line": f"**{h['x']}** points in {h['n']} games · {h['e']:.1f} expected from his ice time"}
    tiles = [["GOALS", str(p["g"])], ["ASSISTS", str(p["a"])], ["POINTS", str(p["p"])], ["GAMES", str(p["gp"])]]
    line = ""
    L = p.get("line") or {}
    names = [n.split()[-1] for n in (L.get("names") or [])]
    if len(names) >= 2:
        line = "Line: " + " – ".join(names)
        if L.get("l5"):
            l5 = L["l5"]
            line += f" · **{l5['gf']}** goals on {l5['xgf']:.1f} expected at 5-on-5"
        if L.get("pp") == "pp1":
            line += " · PP1"
    elif L.get("pp") == "pp1":
        line = "On the first power-play unit"
    v = h["v"] if heat else 0
    if tag == "Ice cold":
        take = "Cold runs don't last: the model still expects his usual output."
    elif heat and v >= 4:
        take = "Red hot. Some of a run this big is real, most of it is luck."
    elif heat and v >= 1.5:
        take = "Scoring above what his ice time predicts. Streaks like this usually cool."
    elif p.get("pt"):
        take = f"The most likely player in this game to record a point."
    else:
        take = f"The {t} attack runs through him."
    pos = {"L": "LW", "R": "RW"}.get(p.get("pos") or "", p.get("pos") or "")
    meta = f"{t} · {pos} · vs {opp}"
    return {"kind": "player", "name": p["name"], "num": str(p.get("num") or "".join(w[0] for w in p["name"].split()[:2])), "color": color(t),
            "meta": meta.replace(" ·  ·", " ·"), "tag": tag, "tagc": tagc, "heat": heat, "last": list(reversed(p.get("last") or [])),
            "seasonLab": "This season", "tiles": tiles, "pt": p.get("pt"),
            "altLab": "Projected ice time", "alt": f"{p['toi']:.0f}" if p.get("toi") else "–", "altSub": "minutes tonight",
            "line": line, "take": take}


def h2h_beat(g: dict, day: dt.date) -> dict:
    A, Hm = g["away"], g["home"]
    season0 = dt.date(day.year if day.month >= 8 else day.year - 1, 9, 1).isoformat()
    rows = []
    for x in (g.get("h2h") or [])[:5]:
        d = dt.date.fromisoformat(x["date"])
        if x["hHome"]:
            a, h, as_, hs = A, Hm, x["ag"], x["hg"]
        else:
            a, h, as_, hs = Hm, A, x["hg"], x["ag"]
        rows.append({"d": d.strftime("%b %d, %Y").upper(), "a": a, "h": h, "as": as_, "hs": hs, "aw": as_ > hs, "hw": hs > as_})
    allg = (g.get("h2h") or [])[:5]
    this = [x for x in (g.get("h2h") or []) if x["date"] >= season0]
    wins_h = sum(x["hg"] > x["ag"] for x in allg)
    if allg:
        avg = sum(x["hg"] + x["ag"] for x in allg) / len(allg)
        lead = Hm if wins_h > len(allg) - wins_h else A if wins_h < len(allg) - wins_h else None
        wn = max(wins_h, len(allg) - wins_h)
        s = (f"**{lead}** won {wn} of {len(allg)}" if lead else f"Split {wins_h}–{wins_h}") + f" · {avg:.1f} goals a game"
        s += f" · {len(this)} this season" if this else " · first meeting this season"
    else:
        s = ""
    stakes = None
    sk_sum = ""
    if g.get("stakes"):
        stakes = []
        for t in (A, Hm):
            v = g["stakes"].get(t)
            if v and v.get("win") is not None and v.get("lose") is not None:
                stakes.append({"team": t, "c": color(t), "win": v["win"], "lose": v["lose"], "now": v.get("now") or (v["win"] + v["lose"]) / 2,
                               "swing": v.get("swing") or (v["win"] - v["lose"])})
        if stakes:
            big = max(stakes, key=lambda x: x["swing"])
            sk_sum = f"Win or lose: a **{round(big['swing'] * 100)}-point** swing in {big['team']}'s playoff chance."
        else:
            stakes = None
    return {"kind": "h2h", "sec": "Head-to-head & stakes" if stakes else "Head-to-head",
            "title": "History and what's at stake" if stakes else "History between them", "h2h": rows,
            "h2hLab": "Last meetings" if rows else "Head-to-head", "noH2h": "" if rows else "No recent meetings.", "h2hSum": s,
            "stakes": stakes, "skLab": "Playoff chance · win vs lose tonight", "skSum": sk_sum}


def keys_beat(g: dict, variant: int = 0) -> dict:
    V = lambda *xs: xs[variant % len(xs)]               # wording varies from game to game
    A, Hm = g["away"], g["home"]
    TA, TH = g["teams"][A], g["teams"][Hm]
    cand = []                                    # (magnitude, kind, head, sentence)
    xa, xh = TA.get("xgf_pct5"), TH.get("xgf_pct5")
    if xa and xh:
        lead, lv, oth = (Hm, xh, A) if xh > xa else (A, xa, Hm)
        cand.append((abs(xh - xa) / .04, "xgf", V("Control at 5-on-5", f"{lead} at even strength", "Who owns 5-on-5?"),
                     V(f"**{lead}** own {pc(lv)} of even-strength chances. Can {oth} keep up?",
                       f"**{lead}** take {pc(lv)} of the 5-on-5 expected goals. {oth} need to flip that.",
                       f"At 5-on-5 the chances lean **{lead}** ({pc(lv)}). {oth} must survive those minutes.")))
    ga, gh = g["goalies"].get(A), g["goalies"].get(Hm)
    if ga and gh and ga.get("gsax") is not None and gh.get("gsax") is not None:
        d = gh["gsax"] - ga["gsax"]
        b, w = (gh, ga) if d > 0 else (ga, gh)
        cand.append((abs(d) / 7, "goalie", V("The goalie gap", "Edge in net", f"{b['name'].split()[-1]} vs {w['name'].split()[-1]}"),
                     V(f"**{b['name']}** is {abs(d):.0f} goals better than {w['name']} vs expected.",
                       f"**{b['name']}** has saved {abs(d):.0f} more goals than {w['name']} relative to the shots faced.",
                       f"In net, **{b['name'].split()[-1]}** is {abs(d):.0f} goals ahead on goals saved above expected.")))
    RA, RH = TA.get("rank") or {}, TH.get("rank") or {}
    for (t, R, o, Ro) in ((A, RA, Hm, RH), (Hm, RH, A, RA)):
        if R.get("pp") and Ro.get("pk"):
            d = Ro["pk"]["rk"] - R["pp"]["rk"]
            if d > 0:
                cand.append((d / 14, "pp" + t, V(f"The {t} power play", f"{t} with the extra man", f"{t} PP vs {o} PK"), f"Ranks **{ordinal(R['pp']['rk'])}** against a penalty kill ranked {ordinal(Ro['pk']['rk'])}. {o} must stay out of the box."))
    for t, T in ((A, TA), (Hm, TH)):
        if T.get("pdo") and abs(T["pdo"] - 1) >= .015:
            hi = T["pdo"] > 1
            cand.append((abs(T["pdo"] - 1) / .025, "pdo" + t, f"{t}'s luck" + (" running out?" if hi else " turning?"),
                         f"PDO of **{T['pdo']:.3f}**: " + ("hot shooting and saves. Those usually come back to normal." if hi else "cold shooting and saves. Those usually come back to normal.")))
        if T.get("b2b"):
            cand.append((1.1, "b2b" + t, f"{t} on a back-to-back", "Second game in two nights: tired legs late in games."))
        for m in T.get("out") or []:
            cand.append((m["share"] / .08, "out" + t, f"{t} without {m['name'].split()[-1]}", f"**{m['name']}** is out. He had {pc(m['share'])} of their points."))
    pl = [(t, p) for t in g["players"] for p in g["players"][t] if p.get("heat") and p["heat"]["n"] >= 8]
    if pl:
        t, p = max(pl, key=lambda x: x[1]["heat"]["v"])
        if p["heat"]["v"] >= 3:
            cand.append((p["heat"]["v"] / 5, "hot", f"Can {g['home'] if t == g['away'] else g['away']} cool {p['name'].split()[-1]}?",
                         f"**{p['heat']['x']}** points in {p['heat']['n']} games, Heat Index {p['heat']['v']:+.1f}."))
    cand.sort(key=lambda c: -c[0])
    keys, seen = [], set()
    for m, k, h_, s in cand:
        base = re.sub(r"[A-Z]{3}$", "", k)
        if base in seen and base not in ("out",):
            continue
        seen.add(base)
        keys.append({"h": h_, "s": s})
        if len(keys) == 3:
            break
    if not keys:
        keys = [{"h": "Small margins", "s": "The model sees two very even teams: one bounce could decide it."}]
    sec, title = KEY_TITLES[variant % len(KEY_TITLES)]
    return {"kind": "keys", "sec": sec, "title": title, "keys": keys}


KEY_TITLES = [("What decides it", "Keys to the game"), ("How it's won", "Three things to watch"), ("Bottom line", "Where this one tips")]


def gamelog(pid: str) -> list:
    """origin/site:data/gl/<pid>.json: [date, opp, home, g, a, s, toi, ppp, pim, hits, blocks, xg] per game, newest first."""
    if pid not in _GL:
        try:
            _GL[pid] = json.loads(subprocess.run(["git", "show", f"origin/site:data/gl/{pid}.json"], cwd=ROOT, capture_output=True, check=True).stdout)
        except Exception:  # noqa: BLE001
            _GL[pid] = []
    return _GL[pid]


_GL: dict = {}


def per_game(x, gp, d=2):
    return f"{x / gp:.{d}f}" if gp else "–"


def pos_txt(p):
    return {"L": "LW", "R": "RW"}.get(p.get("pos") or "", p.get("pos") or "")


def profile_beat(g: dict, t: str, p: dict, tag: str, variant: int = 0) -> dict:
    """One player, full screen: season line, rates, finishing, last 10 games, Heat Index, role, tonight, career vs opponent."""
    opp = g["home"] if t == g["away"] else g["away"]
    gp = p["gp"] or 0
    chips = []
    L = p.get("line") or {}
    names = [n.split()[-1] for n in (L.get("names") or [])]
    if L.get("k", "").startswith("f") and names:
        chips.append(f"Line {L['k'][1]}: {' – '.join(names)}")
    elif L.get("k", "").startswith("d") and names:
        chips.append(f"Pair {L['k'][1]}: {' – '.join(names)}")
    if L.get("pp"):
        chips.append(L["pp"].upper().replace("PP", "PP unit "))
    rates = [["Points / game", per_game(p["p"], gp)], ["Goals / game", per_game(p["g"], gp)], ["Shots / game", per_game(p["s"], gp, 1)],
             ["Ice time", f"{p['toi_season']:.1f}" if p.get("toi_season") else "–"], ["PP time", f"{p['pp_toi']:.1f}" if p.get("pp_toi") is not None else "–"],
             ["Faceoffs", pc(p["fo"]) if p.get("fo") else (f"{p['ppp']}" if p.get("ppp") is not None else "–")]]
    if not p.get("fo"):
        rates[5][0] = "PP points"
    fin = []
    if p.get("sh") is not None and gp >= 5:
        fin.append(["Shooting %", f"{p['sh'] * 100:.1f}%", f"career {p['sh_career'] * 100:.1f}%" if p.get("sh_career") else ""])
    reg = p.get("reg") or {}
    xg = (p.get("xg") or {}).get("xg") if p.get("xg") else reg.get("xg")
    if xg is not None and gp >= 5:
        fin.append(["Goals vs xG", f"{p['g']} / {xg:.1f}", "above his chances" if p["g"] - xg >= 2 else "below his chances" if xg - p["g"] >= 2 else "right on his chances"])
    h = p.get("heat")
    if h and h.get("n", 0) >= 8:
        fin.append(["Heat Index", f"{h['v']:+.1f}", heat_level(h["v"]).lower()])
    bars = [{"o": x["o"], "p": x["p"], "s": x["s"]} for x in reversed(p.get("g10") or [])]
    # career vs tonight's opponent (game logs)
    vs = ""
    gl = [r for r in gamelog(p["id"]) if len(r) >= 6 and r[1] == opp]
    if len(gl) >= 3:
        G, A = sum(r[3] for r in gl), sum(r[4] for r in gl)
        vs = f"Career vs {opp}: **{G + A}** points in {len(gl)} games ({G} G, {A} A)"
    # the one-line read
    if h and h.get("n", 0) >= 8 and h["v"] >= 4:
        take = [f"{h['x']} points in {h['n']} games against {h['e']:.1f} expected: a heater, partly luck.",
                f"Running {h['v']:+.1f} over what his ice time predicts. Some of it sticks, most of it fades.",
                f"{h['x']} points in his last {h['n']}: the hottest stretch of his season."][variant % 3]
    elif h and h.get("n", 0) >= 8 and h["v"] <= -2.5:
        take = f"Only {h['x']} points in {h['n']} games against {h['e']:.1f} expected. The chances say it turns."
    elif xg is not None and gp >= 8 and xg - p["g"] >= 2:
        take = f"{xg:.1f} expected goals, only {p['g']} scored: the finish should come."
    elif p.get("pt"):
        take = f"The model gives him **{pc(p['pt'])}** to record a point tonight."
    else:
        take = f"{p['p']} points in {gp} games: the {t} attack runs through him."
    return {"kind": "profile", "sec": "Player profile", "name": p["name"], "num": str(p.get("num") or ""), "color": color(t),
            "meta": f"{t} · {pos_txt(p)} · vs {opp}" + (f" · age {p['age']}" if p.get("age") else ""), "tag": tag, "chips": chips[:2],
            "season": [["G", str(p["g"])], ["A", str(p["a"])], ["P", str(p["p"])], ["GP", str(gp)]], "rates": rates,
            "fin": fin[:3], "bars": bars, "pt": p.get("pt"), "vs": vs, "take": take}


def duel_beat(g: dict, pa_: dict, ph_: dict) -> dict:
    """Best player of each team, side by side."""
    A, Hm = g["away"], g["home"]

    def m(lab, fa, fh, fmt, lo=0.0, hi=None):
        va, vh = fa(pa_), fh(ph_)
        if va is None or vh is None:
            return None
        top = hi if hi is not None else max(va, vh, 1e-6) * 1.15
        sc = lambda v: max(.03, min(1, (v - lo) / (top - lo)))
        return {"lab": lab, "va": fmt(va), "vh": fmt(vh), "fa": sc(va), "fh": sc(vh), "better": "a" if va > vh else "h" if vh > va else None, "take": ""}
    gp = lambda p: max(1, p["gp"])
    rows = [m("Points per game", lambda p: p["p"] / gp(p), lambda p: p["p"] / gp(p), lambda v: f"{v:.2f}"),
            m("Goals this season", lambda p: p["g"], lambda p: p["g"], lambda v: f"{v:.0f}"),
            m("Shots on goal per game", lambda p: p["s"] / gp(p), lambda p: p["s"] / gp(p), lambda v: f"{v:.1f}"),
            m("Ice time per game (min)", lambda p: p.get("toi_season"), lambda p: p.get("toi_season"), lambda v: f"{v:.1f}", lo=8),
            m("Heat Index (last 10)", lambda p: (p.get("heat") or {}).get("v"), lambda p: (p.get("heat") or {}).get("v"), lambda v: f"{v:+.1f}", lo=-6, hi=8),
            m("Point chance tonight (model)", lambda p: p.get("pt"), lambda p: p.get("pt"), pc, hi=1.0)]
    rows = [r for r in rows if r][:6]
    wins = sum(r["better"] == "a" for r in rows), sum(r["better"] == "h" for r in rows)
    lead = pa_ if wins[0] > wins[1] else ph_
    take = (f"**{lead['name'].split()[-1]}** wins {max(wins)} of {len(rows)} categories." if wins[0] != wins[1]
            else "Dead even: they split the categories.")
    return {"kind": "duel", "sec": "Player duel", "title": "Head to head: the top guns",
            "pa": {"name": pa_["name"], "num": str(pa_.get("num") or ""), "color": color(A), "meta": f"{A} · {pos_txt(pa_)} · {pa_['p']} pts"},
            "ph": {"name": ph_["name"], "num": str(ph_.get("num") or ""), "color": color(Hm), "meta": f"{Hm} · {pos_txt(ph_)} · {ph_['p']} pts"},
            "rows": rows, "take": take, "away": A, "home": Hm}


def teamboard_beat(g: dict) -> dict:
    """Full team profile table with league ranks."""
    A, Hm = g["away"], g["home"]
    TA, TH = g["teams"][A], g["teams"][Hm]
    n = TA.get("n_teams") or 32

    def r(lab, k, fmt, rk_key=None):
        va, vh = TA.get(k), TH.get(k)
        if va is None or vh is None:
            return None
        rk = rk_key or k
        return {"lab": lab, "va": fmt(va), "vh": fmt(vh), "ra": (TA.get("lr") or {}).get(rk), "rh": (TH.get("lr") or {}).get(rk)}
    rows = [{"lab": "Record", "va": f"{TA.get('w', 0)}–{TA.get('l', 0)}–{TA.get('otl', 0)}", "vh": f"{TH.get('w', 0)}–{TH.get('l', 0)}–{TH.get('otl', 0)}",
             "ra": None, "rh": None},
            r("Points %", "pts_pct", lambda v: f"{v * 100:.0f}%"),
            r("Goals for / game", "gf_pg", lambda v: f"{v:.2f}"),
            r("Goals against / game", "ga_pg", lambda v: f"{v:.2f}"),
            r("5-on-5 xGF%", "xgf_pct5", lambda v: f"{v * 100:.1f}%"),
            r("High-danger shots for / 60", "hd_for", lambda v: f"{v:.2f}", "hdsf60"),
            r("High-danger shots against / 60", "hd_against", lambda v: f"{v:.2f}", "hdsa60"),
            r("Power play xG / 60", "pp_xgf60", lambda v: f"{v:.2f}", "pp_xgf60_cur"),
            r("Penalty kill xGA / 60", "pk_xga60", lambda v: f"{v:.2f}", "pk_xga60_cur"),
            r("PDO (luck)", "pdo", lambda v: f"{v:.3f}"),
            r("Faceoff %", "fo_pct", lambda v: f"{v * 100:.1f}%"),
            r("Penalties / game", "pen_pg", lambda v: f"{v:.1f}"),
            {"lab": "Last 5", "va": "".join(l5_letters(TA)), "vh": "".join(l5_letters(TH)), "ra": None, "rh": None}]
    rows = [x for x in rows if x]
    better = [(x["ra"] or 99) < (x["rh"] or 99) for x in rows if x["ra"] and x["rh"]]
    ka, kh = sum(better), len(better) - sum(better)
    lead = A if ka > kh else Hm
    take = (f"**{lead}** rank higher in {max(ka, kh)} of {ka + kh} categories." if ka != kh else "They split the categories evenly.")
    return {"kind": "teamboard", "sec": "Team stats", "title": "Side by side", "away": A, "home": Hm, "rows": rows, "n": n, "take": take}


def lines_beat(g: dict) -> dict | None:
    """Top forward lines, the top D pair and PP1 for both teams: 5-on-5 results together vs their chances."""
    A, Hm = g["away"], g["home"]
    sides, best = {}, None
    for t in (A, Hm):
        T = g["teams"][t]
        Ls = T.get("lines") or {}
        rows = []
        for k in ("f1", "f2", "f3", "d1", "d2"):
            L = Ls.get(k)
            if not L or (L.get("toi") or 0) < 15:
                continue
            names = [re.sub(r"^Mc(\w)", lambda m_: "Mc" + m_.group(1).upper(), x.strip().title()) for x in (L.get("name") or "").split("-") if x.strip()] or [n.split()[-1] for n in (T.get("groups") or {}).get(k, [])]
            xf, xa = L.get("xgf") or 0, L.get("xga") or 0
            row = {"k": ("Line " if k[0] == "f" else "Pair ") + k[1], "names": " – ".join(names), "toi": f"{L['toi']:.0f} min",
                   "gf": L["gf"], "xgf": f"{xf:.1f}", "ga": L["ga"], "xga": f"{xa:.1f}", "xp": xf / (xf + xa) if xf + xa > 0 else .5,
                   "sh": f"{L['sf']}–{L['sa']}", "flag": ""}
            d = L["gf"] - xf
            if (L.get("toi") or 0) >= 40 and abs(d) >= 1.5:
                row["flag"] = "Finishing above chances" if d > 0 else "Chances not going in"
                if not best or abs(d) > abs(best[0]):
                    best = (d, t, row)
            rows.append(row)
            if len(rows) == 4:
                break
        pp1 = (T.get("groups") or {}).get("pp1") or []
        sides[t] = {"rows": rows, "pp1": " – ".join(n.split()[-1] for n in pp1), "ppx": T.get("pp_xgf60")}
    if not any(s_["rows"] for s_ in sides.values()):
        return None
    if best:
        d, t, row = best
        take = (f"**{t}**'s {row['k'].lower()} has {row['gf']} goals on {row['xgf']} expected: " +
                ("hot finishing that tends to cool." if d > 0 else "the goals should start to come."))
    else:
        take = "No line is far off its chances: the results match the play."
    return {"kind": "lines", "sec": "Line stats", "title": "Who plays with whom", "away": A, "home": Hm,
            "sa": sides[A], "sh": sides[Hm], "take": take}


# section pool: every game picks its own mix and order (what stands out in THAT game), no type in more than 2 games
POOL = ("model", "teamboard", "drive", "scoring", "special", "goalies", "lines", "profile", "duel", "h2h")
MAX_GAMES_PER_SECTION = 2


def section_scores(g: dict) -> dict:
    A, Hm = g["away"], g["home"]
    TA, TH = g["teams"][A], g["teams"][Hm]
    sc = {"model": .7 + max(0, 1 - abs(g["p_home"] - .5) / .12) * .5,
          "teamboard": .75 + min(.6, abs((TA.get("pts_pct") or .5) - (TH.get("pts_pct") or .5)) * 3)}
    if TA.get("xgf_pct5") and TH.get("xgf_pct5"):
        sc["drive"] = .4 + abs(TA["xgf_pct5"] - TH["xgf_pct5"]) / .04
    sc["scoring"] = .4 + max(abs((TA.get("pdo") or 1) - 1), abs((TH.get("pdo") or 1) - 1)) / .025 * .6 + abs((TA.get("gf_pg") or 0) - (TH.get("gf_pg") or 0)) / 1.0
    RA, RH = TA.get("rank") or {}, TH.get("rank") or {}
    if RA.get("pp") and RH.get("pk") and RH.get("pp") and RA.get("pk"):
        sc["special"] = .3 + max(RH["pk"]["rk"] - RA["pp"]["rk"], RA["pk"]["rk"] - RH["pp"]["rk"], 0) / 14
    gk = [x for x in g["goalies"].values() if x]
    if gk:
        sc["goalies"] = .5 + (abs((gk[0].get("gsax") or 0) - (gk[1].get("gsax") or 0)) / 8 if len(gk) == 2 else 0)
    lb = lines_beat(g)
    if lb:
        devs = [abs(r_["gf"] - float(r_["xgf"])) for s_ in (lb["sa"], lb["sh"]) for r_ in s_["rows"]]
        sc["lines"] = .55 + max(devs, default=0) / 3
    pl = [p for t in g["players"] for p in g["players"][t] if p.get("posg") != "G" and (p.get("toi") or 0) >= 12]
    if pl:
        hv = max(((p.get("heat") or {}).get("v") or 0 for p in pl), default=0)
        sc["profile"] = .8 + max(hv, 0) / 5 + max((p.get("pt") or 0 for p in pl), default=0) * .5
        sc["duel"] = .75 + max((p.get("pt") or 0 for p in pl), default=0) * .4
    if g.get("h2h") or g.get("stakes"):
        sc["h2h"] = .35 + max((v.get("swing") or 0 for v in (g.get("stakes") or {}).values()), default=0) / .06 + .05 * len(g.get("h2h") or [])
    return sc


def plan_sections(chosen: list, seed: str, per_game: int = 5) -> list:
    """[[section, ...] per game]: highest-scoring sections first, each type in at most 2 games, a different opener each
    game, every game gets a player beat (profile or duel), the model's view in at most 2 games."""
    import random
    rnd = random.Random(seed)
    used = {k: 0 for k in POOL}
    openers, plans = set(), [None] * len(chosen)
    order = sorted(range(len(chosen)), key=lambda i: -max(section_scores(chosen[i]).values()))
    for i in order:
        # a little seeded variety, and a penalty for what earlier games already showed (so the games differ)
        sc = {k: v + rnd.random() * .25 - .8 * used[k] for k, v in section_scores(chosen[i]).items()}
        avail = [k for k in sorted(sc, key=lambda k: -sc[k]) if used[k] < MAX_GAMES_PER_SECTION]
        pick = avail[:per_game]
        if not any(k in pick for k in ("profile", "duel")):
            pl = next((k for k in avail if k in ("profile", "duel")), None)
            if pl:
                pick = pick[:-1] + [pl]
        for k in pick:
            used[k] += 1
        pick.sort(key=lambda k: -sc[k])
        if pick and pick[0] in openers:                  # a different first beat after each matchup card
            j = next((j for j, k in enumerate(pick) if k not in openers), 0)
            pick.insert(0, pick.pop(j))
        if pick:
            openers.add(pick[0])
        plans[i] = pick
    return plans


def game_beats(g: dict, i: int, n: int, day: dt.date, plan: list) -> list:
    A, Hm = g["away"], g["home"]
    TA, TH = g["teams"][A], g["teams"][Hm]
    ac, hc = color(A), color(Hm)

    def side(T):
        rec = f"{T.get('w', 0)}–{T.get('l', 0)}–{T.get('otl', 0)}"
        if T.get("playoffs") is not None:
            rec += f" · {pc(T['playoffs'])} playoff chance"
        return {"abbr": T["abbr"], "name": T.get("name") or T["abbr"], "rec": rec, "l5": l5_letters(T), "strk": streak_txt(T)}
    et, cet = times(g["start"])
    info = [["Puck drop", f"{et} · {cet}"]]
    if g.get("venue"):
        info.append(["", g["venue"]])
    for T in (TA, TH):
        if T.get("b2b"):
            info.append(["Back-to-back:", f"{T['abbr']} played last night", True])
    why, _ = reason(g)
    beats = [{"kind": "title", "kicker": f"Game {i + 1} of {n} · {why}", "A": side(TA), "H": side(TH), "ac": ac, "hc": hc, "info": info,
              "sec2": "Matchup"}]
    forms = {b["_type"]: b for b in form_beats(g)}
    # players: the profile gets the most interesting one, the duel the best of each team (not the profiled player)
    pl = {t: [p for p in g["players"][t] if p.get("posg") != "G" and (p.get("toi") or 0) >= 12] for t in (A, Hm)}
    have_pt = any(p.get("pt") for t in pl for p in pl[t])
    star = (lambda p: p.get("pt") or 0) if have_pt else (lambda p: p["p"] / max(1, p["gp"]))
    allp = [(t, p) for t in pl for p in pl[t]]
    prof = None
    if allp:
        hot = max(allp, key=lambda x: ((x[1].get("heat") or {}).get("v") or -9))
        top = max(allp, key=lambda x: star(x[1]))
        prof = hot + ("Hottest on the ice",) if ((hot[1].get("heat") or {}).get("v") or 0) >= 3 else top + ("Top point chance" if have_pt else "Top scorer",)
    for k in plan:
        if k == "model":
            pa = 1 - g["p_home"]
            fav = Hm if g["p_home"] >= .5 else A
            tiles = [["Expected score", f"{g['mu_a']:.1f} – {g['mu_h']:.1f}", f"{A} – {Hm}"],
                     ["Expected goals", f"{g['exp_total']:.1f}", "both teams combined"]]
            if g.get("p_tie_reg"):
                tiles.append(["Tied after 60 min", pc(g["p_tie_reg"]), "chance of overtime"])
            beats.append({"kind": "model", "sec": "The model's view", "title": "Win chance tonight", "away": A, "home": Hm, "pa": pa,
                          "tiles": tiles, "take": closeness(g["p_home"], fav)})
        elif k in forms:
            beats.append({"kind": "form", "away": A, "home": Hm, **{x: y for x, y in forms[k].items() if x != "_type"}})
        elif k == "goalies":
            gb = goalie_beat(g)
            if gb:
                beats.append(gb)
        elif k == "teamboard":
            beats.append(teamboard_beat(g))
        elif k == "lines":
            lb = lines_beat(g)
            if lb:
                beats.append(lb)
        elif k == "profile" and prof:
            beats.append(profile_beat(g, *prof, variant=i))
        elif k == "duel":
            sa_ = [p for p in pl[A] if not prof or p["id"] != prof[1]["id"]]
            sh_ = [p for p in pl[Hm] if not prof or p["id"] != prof[1]["id"]]
            if sa_ and sh_:
                beats.append(duel_beat(g, max(sa_, key=star), max(sh_, key=star)))
        elif k == "h2h":
            beats.append(h2h_beat(g, day))
    beats.append(keys_beat(g, i))
    for b in beats:
        b.setdefault("ac", ac); b.setdefault("hc", hc)
        sec = b.get("sec") or b.get("sec2") or ""
        b["chap"] = f"Game {i + 1} of {n} · **{A} @ {Hm}** · {sec.split(' · ')[0]}"
        b.pop("sec2", None)
    return beats


LEAD = {"cold": 3.4, "slate": 2.8, "picks": 2.2, "title": 2.4, "model": 3.4, "form": 3.0, "goalies": 2.5, "player": 2.5, "h2h": 2.4,
        "keys": 2.8, "recap": 2.0, "cta": 2.2, "profile": 3.4, "duel": 3.2, "teamboard": 3.0, "lines": 3.0}
MAXD = {"slate": 16, "cold": 10}
MIND = {"cold": 11, "slate": 14, "title": 9, "model": 10, "recap": 9, "cta": 7, "player": 12, "goalies": 12, "profile": 17, "duel": 13,
        "teamboard": 14, "lines": 14}


def build_spec(ext: dict, day: dt.date, force: list | None, max_seconds: float | None) -> dict | None:
    games = ext["games"]
    if not games:
        return None
    n = 3 if len(games) >= 6 else min(2, len(games))
    chosen = choose(games, n, force)
    n = len(chosen)
    n_players = 2 if n >= 3 else 3
    ids = {g["id"] for g in chosen}
    cg = lambda g: {"away": g["away"], "home": g["home"], "ac": color(g["away"]), "hc": color(g["home"])}
    dlab = f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}"
    beats = [{"kind": "cold", "kicker": "Tonight's NHL slate", "t1": f"{len(games)} games tonight" if len(games) > 1 else "One game tonight",
              "t2": f"{n} to watch" if n > 1 else "The breakdown", "games": [cg(g) for g in chosen],
              "sub": f"{dlab} · the model's numbers, game by game", "noChrome": True, "ci": 0}]
    beats.append({"kind": "slate", "sec": "Tonight's slate", "title": f"All {len(games)} games", "ci": 1,
                  "games": [{**cg(g), "time": tline(g["start"]), "pa": 1 - g["p_home"], "xt": g["exp_total"], "pick": g["id"] in ids} for g in games],
                  "chap": "**The slate** · start times & win chances"})
    beats.append({"kind": "picks", "sec": "Our 3 games" if n == 3 else f"Our {n} games", "title": "Why these games", "ci": 1,
                  "games": [{**cg(g), "why": reason(g)[0], "wsub": reason(g)[1], "time": tline(g["start"])} for g in chosen],
                  "chap": "**The slate** · why these games"})
    plans = plan_sections(chosen, f"{SERIES}|{day.isoformat()}", per_game=5 if n >= 3 else 6)
    for i, g in enumerate(chosen):
        g["_plan"] = plans[i]
        for b in game_beats(g, i, n, day, plans[i]):
            b["ci"] = 2 + i
            b["_game"] = i
            beats.append(b)
    beats.append({"kind": "recap", "sec": "Recap", "title": "The model's call tonight", "ci": 2 + n, "chap": "**Recap**",
                  "games": [{**cg(g), "pa": 1 - g["p_home"], "score": f"{g['mu_a']:.1f} – {g['mu_h']:.1f}"} for g in chosen]})
    beats.append({"kind": "cta", "t1": "Every player. Every game.", "t3": "Free to start · link in the description",
                  "disc": "Stats from NHL game data · win chances, xG and playoff chances are model estimates", "noChrome": True, "ci": 2 + n})
    # timing: animations first, then >= 0.35 s per readable word, never under 4 s
    t = 0.0
    for b in beats:
        d = max(MIN_BEAT, LEAD.get(b["kind"], 2.5) + SEC_PER_WORD * words(b))
        d = max(MIND.get(b["kind"], 0), min(d, MAXD.get(b["kind"], 99)))
        d = round(d * 2) / 2
        b["a"], b["b"] = round(t, 3), round(t + d, 3)
        t += d
    total = t
    if max_seconds:
        beats = [b for b in beats if b["a"] < max_seconds]
        total = min(total, max_seconds)
        beats[-1]["b"] = total
    chapters = [{"t": 0.0, "label": "Intro"}]
    for b in beats:
        if b["kind"] == "slate":
            chapters.append({"t": b["a"], "label": f"Tonight's {len(games)} games & why these {n}"})
        if b["kind"] == "title":
            g = chosen[b["_game"]]
            chapters.append({"t": b["a"], "label": f"{g['away']} @ {g['home']}: {short_name(g['teams'][g['away']].get('name', ''))} vs {short_name(g['teams'][g['home']].get('name', ''))}"})
        if b["kind"] == "recap":
            chapters.append({"t": b["a"], "label": "Recap: the model's call"})
    # thumbnail: the closest game's chance, or the hottest player
    close = min(chosen, key=lambda g: abs(g["p_home"] - .5))
    fav_p = max(close["p_home"], 1 - close["p_home"])
    thumb = {"k": "NHL tonight", "big": [f"{n} games", "to watch"] if n > 1 else ["Game", "breakdown"], "date": day.strftime("%b %d").upper().replace(" 0", " "),
             "games": [cg(g) for g in chosen], "num": pc(fav_p),
             "numLab": [f"{close['away']} @ {close['home']}", "a coin flip" if fav_p < .55 else f"model: {close['home'] if close['p_home'] >= .5 else close['away']} win"]}
    hot = max((g["_int"]["_hottest"] for g in chosen if g["_int"]["_hottest"]), key=lambda p: p["heat"]["v"], default=None)
    if hot and hot["heat"]["v"] >= 5 and fav_p >= .55:
        thumb.update({"num": f"{hot['heat']['v']:+.1f}", "numLab": [hot["name"], "Heat Index"]})
    return {"series": SERIES, "day": day.strftime("%a · %b ").upper() + str(day.day), "beats": [{k: v for k, v in b.items() if not k.startswith("_")} for b in beats],
            "chapters": chapters, "total": total, "thumbSpec": thumb, "_chosen": chosen, "_games": games}


# ------------------------------------------------------------------ captions
def ts(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}" if t < 3600 else f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def captions(spec: dict, day: dt.date) -> dict:
    ch = spec["_chosen"]
    md = f"{day.strftime('%b')} {day.day}"
    names = lambda g: f"{short_name(g['teams'][g['away']].get('name', ''))} vs {short_name(g['teams'][g['home']].get('name', ''))}"
    mus = [f"{g['away']} vs {g['home']}" for g in ch]
    title = f"NHL tonight: {(', '.join(mus[:-1]) + ' & ') if len(mus) > 1 else ''}{mus[-1]} — breakdown ({md})"
    if len(title) > 100:
        title = f"NHL tonight: {len(ch)} games to watch — breakdown ({md})"
    lines = []
    for g in ch:
        pa = 1 - g["p_home"]
        lines.append(f"• {names(g)} ({tline(g['start'])}): model win chance {g['away']} {pc(pa)}, {g['home']} {pc(g['p_home'])}; "
                     f"expected score {g['mu_a']:.1f}–{g['mu_h']:.1f}. {reason(g)[0]}.")
    chap = "\n".join(f"{ts(c['t'])} {c['label']}" for c in spec["chapters"])

    def body(lines):
        return (f"Tonight's NHL slate ({day.strftime('%A')}, {md}): {len(spec['_games'])} games, and the {len(ch)} we'd watch, broken down with the "
                f"model's numbers: win chance, expected score, 5-on-5 xG share, PDO, special teams, goalies (save %, goals saved above expected), "
                f"the hottest players by Heat Index, head-to-head and playoff stakes.\n\n" + "\n".join(lines) +
                f"\n\nChapters\n{chap}\n\nEvery player, every game, free to start: https://{SITE}/?ref=yt\n\n"
                "Stats from NHL game data; win chances, xG and playoff chances are model estimates. Analysis only.\n\n#NHL #hockey #NHLstats")
    desc = body(lines)
    while len(desc.encode("utf-8")) > 4900 and lines:    # YouTube: 5000 bytes; drop game lines, never the chapters
        lines = lines[:-1]
        desc = body(lines)
    tags = ["NHL", "hockey", "NHL tonight", "NHL preview", "NHLstats", "hockey analytics", "expected goals"]
    for g in ch:
        for t in (g["away"], g["home"]):
            nm = g["teams"][t].get("name")
            if nm and nm not in tags:
                tags.append(nm)
    return {"youtube": {"title": title[:100], "description": desc, "tags": tags}}


# ------------------------------------------------------------------ music
def file_track(path: str, total: float, xfade: float = 2.0, fade_in: float = 1.0, fade_out: float = 3.0):
    """A licensed music file (any format ffmpeg reads) looped for the whole video: equal-power crossfade over each loop
    seam, 1 s fade-in, 3 s fade-out. Loudness is set in the final encode (loudnorm -18 LUFS, background level)."""
    import numpy as np
    sr = music.SR
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    y = np.frombuffer(raw, np.float32).reshape(-1, 2).copy()
    loud = np.where(np.abs(y).max(axis=1) > 10 ** (-50 / 20))[0]        # trim silence at both ends (clean seam)
    if len(loud):
        y = y[loud[0]:loud[-1] + 1]
    n, k = int(round(total * sr)), int(xfade * sr)
    if len(y) <= 2 * k:
        raise SystemExit(f"music file too short to loop: {path}")
    up = np.sin(np.linspace(0, np.pi / 2, k))[:, None] ** 1
    down = np.cos(np.linspace(0, np.pi / 2, k))[:, None]
    out = y.copy()
    while len(out) < n + k:
        head = out[-k:] * down + y[:k] * up
        out = np.concatenate([out[:-k], head, y[k:]])
    out = out[:n]
    fi, fo = int(fade_in * sr), int(fade_out * sr)
    out[:fi] *= np.linspace(0, 1, fi)[:, None]
    out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 2
    pk = np.max(np.abs(out)) + 1e-9
    return (out / pk * .89).astype(np.float32)


def make_music(spec: dict, seed: str) -> "music.np.ndarray":
    import numpy as np
    total = spec["total"]
    beats = spec["beats"]
    starts = [0.0] + [b["a"] for b in beats if b["kind"] == "title"] + [total]
    starts = sorted(set(round(s, 3) for s in starts if s <= total))
    styles = music.SERIES_STYLES[SERIES]
    first = styles.index(music.pick_style(SERIES, seed))
    sr, X = music.SR, 1.5                                   # crossfade seconds between game sections
    out = np.zeros((int(round(total * sr)) + 1, 2), np.float32)
    for i in range(len(starts) - 1):
        a, b = starts[i], starts[i + 1]
        last = i == len(starts) - 2
        dur = (b - a) + (0 if last else X + 1.5)           # run past the cut so the next section crossfades in, never into an ending
        cues = [x["a"] - a for x in beats if a < x["a"] < b]
        y = music.style_track(styles[(first + i) % len(styles)], dur, cues, seed=f"{SERIES}|{seed}|{i}")
        n = min(len(y), int(round((b - a + (0 if last else X)) * sr)))
        y = y[:n].copy()
        if i > 0:                                          # fade in over the crossfade
            k = int(X * sr)
            y[:k] *= np.linspace(0, 1, k)[:, None] ** .5
        if not last:                                       # fade out after the cut
            k = int(X * sr)
            y[-k:] *= np.linspace(1, 0, k)[:, None] ** .5
        s0 = int(round(a * sr))
        m = min(len(y), len(out) - s0)
        out[s0:s0 + m] += y[:m]
    pk = np.max(np.abs(out)) + 1e-9
    return (out / pk * .89).astype(np.float32)


# ------------------------------------------------------------------ render
def setup_page(spec: dict, work: Path):
    (work / "img").mkdir(parents=True, exist_ok=True)
    shutil.copytree(HERE / "fonts", work / "fonts", dirs_exist_ok=True)
    shutil.copy(ROOT / "site-assets" / "brand" / "evenout-logo-dark.svg", work / "img" / "logo.svg")
    clean = {k: v for k, v in spec.items() if not k.startswith("_")}
    (work / "index.html").write_text((HERE / "longform.html").read_text(encoding="utf-8").replace("/*SPEC*/null", json.dumps(clean)), encoding="utf-8")


def render_video(spec: dict, work: Path, base: Path, seed: str, preview=None, stills: Path | None = None, music_file: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    setup_page(spec, work)
    total = spec["total"]
    stats = {}
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--allow-file-access-from-files"])
        page = br.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.on("pageerror", lambda e: print("  page error:", e))
        page.goto((work / "index.html").as_uri())
        page.wait_for_function("window.READY === true", timeout=60000)
        if preview:
            stills.mkdir(parents=True, exist_ok=True)
            for t in preview:
                page.evaluate(f"render({t})")
                page.screenshot(path=str(stills / f"slate-t{t:06.2f}.jpg"), type="jpeg", quality=88)
            page.set_viewport_size({"width": 1280, "height": 720})
            page.evaluate("thumb()")
            page.screenshot(path=str(stills / "slate-thumb.jpg"), type="jpeg", quality=92)
            br.close()
            return stats
        t0 = time.time()
        wav = work / "music.wav"
        music.write_wav(str(wav), file_track(music_file, total) if music_file else make_music(spec, seed))
        stats["music"] = Path(music_file).name if music_file else "generated"
        stats["music_s"] = round(time.time() - t0, 1)
        print(f"  music: {stats['music_s']}s", flush=True)
        n = int(round(total * FPS))
        t1 = time.time()
        keys = page.evaluate(f"plan({FPS},{n})")
        stats["plan_s"] = round(time.time() - t1, 1)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
               "-i", str(wav), "-map", "0:v", "-map", "1:a", "-af", "loudnorm=I=-18:TP=-2:LRA=11",
               "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "20", "-g", str(FPS * 2), "-pix_fmt", "yuv420p",
               "-profile:v", "high", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", f"{base}.mp4"]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        cdp = page.context.new_cdp_session(page)
        prev_key, prev_jpg, shots = "__none__", None, 0
        t2 = time.time()
        for f in range(n):
            k = keys[f]
            if k is None or k != prev_key or prev_jpg is None:
                page.evaluate(f"render({f / FPS})")
                r = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "optimizeForSpeed": True})
                prev_jpg = base64.b64decode(r["data"]); shots += 1
            prev_key = k
            ff.stdin.write(prev_jpg)
            if f % 900 == 0:
                el = time.time() - t2
                print(f"  frame {f}/{n} ({shots} captured) {el:.0f}s", flush=True)
        ff.stdin.close(); ff.wait()
        stats.update({"frames": n, "captured": shots, "frames_s": round(time.time() - t2, 1)})
        page.set_viewport_size({"width": 1280, "height": 720})
        page.evaluate("thumb()")
        page.screenshot(path=f"{base}-thumb.jpg", type="jpeg", quality=92)
        br.close()
    if ff.returncode:
        raise SystemExit("ffmpeg failed")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(ET).date().isoformat(), help="slate date (New York)")
    ap.add_argument("--site", help="built app page (default origin/site:app/index.html)")
    ap.add_argument("--pro", default=str(ROOT / "output" / "pro" / "latest.json"), help="Pro data patch (optional)")
    ap.add_argument("--out", default=str(ROOT / "video" / "out"))
    ap.add_argument("--games", help="comma list of game ids to feature (default: chosen by storyline)")
    ap.add_argument("--preview", help="'beats' (one still per beat) or comma-separated seconds: stills only")
    ap.add_argument("--max-seconds", type=float, help="cut the video after this many seconds (quick test)")
    ap.add_argument("--music", help="licensed music file to loop under the whole video instead of the generated track "
                                    "(never commit one: the repo is public)")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date)
    out = Path(a.out) / a.date
    out.mkdir(parents=True, exist_ok=True)
    T0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        pro = Path(a.pro) if a.pro else None
        ext = load_page_data(a.date, site_html(a.site), pro, work, script="longform_extract.js", args={"date": a.date})
        n_pt = sum(1 for g in ext["games"] for t in g["players"] for p in g["players"][t] if p.get("pt"))
        print(f"page data: {ext['n_games']} games on {a.date}, point chances for {n_pt} players ({'Pro data' if n_pt > 40 else 'public page only'})")
        spec = build_spec(ext, day, a.games.split(",") if a.games else None, a.max_seconds)
        if not spec:
            print(f"No games on {a.date}: no slate video.")
            return
        for g in spec["_chosen"]:
            print(f"  {g['away']} @ {g['home']} sections: {' > '.join(['matchup'] + g.get('_plan', []) + ['keys'])}")
        for g in spec["_chosen"]:
            I = g["_int"]
            print(f"  chosen {g['away']} @ {g['home']}: {reason(g)[0]} (score {g['_score']:.2f}; " +
                  ", ".join(f"{k} {I[k]:.2f}" for k in WEIGHT) + ")")
        for c in spec["chapters"]:
            print(f"  {ts(c['t'])} {c['label']}")
        print(f"  {len(spec['beats'])} beats, {spec['total']:.1f}s ({ts(spec['total'])})")
        (out / f"slate-{a.date}-spec.json").write_text(json.dumps({k: v for k, v in spec.items() if not k.startswith('_')}, indent=1), encoding="utf-8")
        base = out / f"slate-{a.date}"
        if a.preview:
            if a.preview == "beats":
                pts = [round(b["b"] - 1.0, 2) for b in spec["beats"]]
            else:
                pts = [float(x) for x in a.preview.split(",")]
            render_video(spec, work / "r", base, seed=a.date, preview=pts, stills=out / "stills")
            print("stills:", out / "stills")
            return
        mf = a.music if a.music and Path(a.music).is_file() else None
        if a.music and not mf:
            print(f"music file not found ({a.music}): using the generated track")
        stats = render_video(spec, work / "r", base, seed=a.date, music_file=mf)
    stats["total_s"] = round(time.time() - T0, 1)
    print(f"render: {stats}  -> {stats['frames'] / max(1, stats['frames_s']):.1f} video frames/s, "
          f"{stats['captured'] / max(1, stats['frames_s']):.1f} captures/s")
    cap = captions(spec, day)
    rel = lambda p: str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/") if str(Path(p).resolve()).startswith(str(ROOT)) else str(p)
    mpath = out / "manifest.json"
    manifest = {"date": a.date, "items": []}
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    manifest["items"] = [i for i in manifest.get("items", []) if i.get("series") != SERIES]
    manifest["items"].append({"id": f"{SERIES}-{a.date}", "series": SERIES, "platform": "youtube", "long": True,
                              "file": rel(f"{base}.mp4"), "cover": rel(f"{base}-thumb.jpg"), **cap["youtube"]})
    mpath.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print("video:", f"{base}.mp4")
    print("manifest:", mpath)


if __name__ == "__main__":
    main()
