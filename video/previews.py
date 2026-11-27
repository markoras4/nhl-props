"""evenout "Game preview" Shorts: one 9:16 video per featured game tonight, built from the real app.

Each video: the matchup (intro), the game's written preview (headline + deck, the site's preview photo), then 5-6
moments on the real app, every one with a one-line explanation of what it shows. The moments are drawn from a wide pool
and vary by game and day:

  game:    chance of each final score, team ranks (one stat lit), tonight's lines (a line lit), both goalies,
           what each defence allows by position, playoff chances (Futures)
  players: how the model builds his number, skill or luck (shooting % vs true talent), linemates with / without,
           season projection, shot chart (season, last 10 or his last game), game by game (goals, assists, points,
           shots), averages chart (goals, assists, points, shots, shooting %, goals vs expected; per game, 82-game pace
           or per season; season by season), last 10 + Heat Index

Stats and model probabilities only: odds, markets and bets are hidden on every page (appclips.py), so these go to
TikTok as well as YouTube. Pro-only numbers stay locked (signed-out view).

    py -X utf8 video/previews.py --date 2026-11-27 --n 3 --music "<licensed song.mp3>"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import series as S  # noqa: E402

SERIES = "preview"
CLIP = 6.0

# one line under the phone: what the viewer is looking at
NOTE = {
    "built": "How the model builds his number: scoring rates per 60 and ice time, then tonight's opponent, goalie, rest and form.",
    "luck": "Skill or luck: his shooting % against his true talent. Far above it tends to cool off, far below tends to bounce back.",
    "mates": "Linemates tonight: his points per game when each linemate also played vs when he didn't.",
    "role": "Role & usage: his power-play unit, minutes and how the coach uses him.",
    "proj": "Season projection: where he ends up if he keeps scoring at the model's rate.",
    "shots": "Shot profile: how often he shoots, from where and how dangerous, ranked against the league. On the chart pink = goal, purple = saved.",
    "gbg": "Game by game: one bar per game. The line is his season average, the last bar tonight's model number.",
    "avg": "This season against his last 3 and 5 seasons and his career.",
    "l10": "Heat Index: points in his last 10 games against what his ice time should give.",
    "scores": "Every final score and its chance. Most NHL games end within a goal or two.",
    "teams": "Both teams, every stat with its league rank (#1 = best of 32).",
    "lines": "Tonight's lines and how each one has done at 5-on-5 this season.",
    "goalies": "Tonight's goalies: save %, goals saved above expected, record against this opponent.",
    "defence": "What each defence gives up per game to centres, wingers and defencemen, with league rank.",
    "futures": "10,000 simulated seasons: each team's chance to make the playoffs.",
}
STATS = {"g": "Goals", "a": "Assists", "p": "Points", "s": "Shots", "sh": "Shooting %", "gx": "Goals vs expected"}
UNITS = {"pg": "per game", "pace": "82-game pace", "ps": "per season"}
FORBID = re.compile(r"\bodds\b|sportsbook|bookmaker|\bmarkets?\b|\bbet(s|ting|tor)?\b|parlay|wager|\bprices?\b|favou?rite|underdog", re.I)


def clean(txt: str) -> str:
    """The preview text without sentences about odds / markets (stats only)."""
    ss = re.split(r"(?<=[.!?])\s+", (txt or "").strip())
    return " ".join(x for x in ss if x and not FORBID.search(x))


def stars(C, gid: str) -> dict | None:
    """Teams, start, the written preview and each team's top 2 players by projected season points."""
    return C.page("phone").evaluate("""(gid) => { const g = gameById[gid]; if (!g) return null;
      const pv = (DATA.previews || {})[gid]?.en || null;
      const team = t => Object.values(DATA.players).filter(p => p.team === t && p.active !== false && !p.injury && p.posg !== 'G')
          .map(p => ({id: String(p.id), name: p.name, pos: p.pos, pts: (DATA.proj?.[p.id]?.p || [])[1] || 0, sh: p.shp ? 1 : 0}))
          .sort((a, b) => b.pts - a.pts).slice(0, 2);
      return {id: String(g.id), away: g.away, home: g.home, start: g.start, away_name: g.away_name, home_name: g.home_name,
              mu: g.mu, pv: pv && {kicker: pv.kicker, title: pv.title, deck: pv.deck}, A: team(g.away), H: team(g.home)}; }""", gid)


def todays_games(C, date: str) -> list:
    return C.page("phone").evaluate("""(d) => DATA.games.filter(g => g.date === d && (!g.state || ['FUT','PRE'].includes(g.state)))
        .map(g => ({id: String(g.id), start: g.start, pv: !!(DATA.previews || {})[String(g.id)],
                    close: Math.abs((g.mu?.home?.g || 0) - (g.mu?.away?.g || 0)),
                    star: [g.home, g.away].map(t => Math.max(0, ...Object.values(DATA.players).filter(p => p.team === t).map(p => (DATA.proj?.[p.id]?.p || [])[1] || 0))).reduce((a, b) => a + b, 0)}))""", date)


def choose(games: list, n: int) -> list:
    """Games with a written preview first, then star power and a close matchup; spread over the evening."""
    key = lambda g: (g["pv"], g["star"] / 100 - g["close"])
    return sorted(sorted(games, key=key, reverse=True)[:n], key=lambda g: g["start"])


def plan(g: dict, rng: random.Random) -> list:
    """5-6 moments: 2 about the game, 2 per featured player (one per team), every page type at most once, in an
    order that alternates game and player moments."""
    pa, ph = (g["A"] or [None])[0], (g["H"] or [None])[0]
    players = [p for p in (pa, ph) if p]
    rng.shuffle(players)
    game_pool = ["scores", "teams", "lines", "goalies", "defence", "futures"]
    rng.shuffle(game_pool)
    card_pool = ["built", "luck", "mates", "role", "proj", "shots", "gbg", "avg", "l10"]
    rng.shuffle(card_pool)
    out = [{"type": "game", "v": v, "alts": game_pool[2:]} for v in game_pool[:2]]
    k = 0
    for p in players:
        for _ in range(2):
            out.append({"type": "card", "v": card_pool[k % len(card_pool)], "p": p, "alts": card_pool[4:] + card_pool[:4]})
            k += 1
    # game, player, player, game, player, player
    gm, pl = [x for x in out if x["type"] == "game"], [x for x in out if x["type"] == "card"]
    order = []
    while gm or pl:
        if gm:
            order.append(gm.pop(0))
        order += pl[:2]
        pl = pl[2:]
    return order


def record(C, m: dict, g: dict, rng: random.Random, name: str) -> dict | None:
    gid, v = g["id"], m["v"]
    A, Hm = g["away"], g["home"]
    if m["type"] == "game":
        if v == "futures":
            c = C.futures([A, Hm], kind="phone", name=name, dur=CLIP)
            return c and {**c, "caption": "Playoff chances, both teams", "note": NOTE[v]}
        focus = None
        if v == "teams":
            focus = rng.choice(["^xGF%", "^PDO", "^Goaltending", "^Power play", "^Penalty kill"])
        elif v == "lines":
            focus = (rng.choice([A, Hm]), rng.choice(["f1", "f2", "d1"]))
        elif v == "goalies":
            focus = rng.choice([A, Hm])
        c = C.game(gid, v, kind="phone", name=name, focus=focus, dur=CLIP)
        cap = {"scores": "Chance of each final score", "teams": "Team ranks, both teams", "lines": "Tonight's lines",
               "goalies": "Both goalies", "defence": "Each defence, by position"}[v]
        return c and {**c, "caption": cap, "note": NOTE[v]}
    p = m["p"]
    last = p["name"].split()[-1]
    if v == "l10":
        c = C.player(p["id"], stat=rng.choice(["p", "s", "g"]), kind="phone", name=name, prefer_game=gid, dur=CLIP)
        return c and {**c, "caption": f"{last}: last 10 & Heat Index", "note": NOTE[v]}
    kw, cap, note = {}, "", NOTE[v]
    if v == "built":
        cap = f"How {last}'s number is built"
    elif v == "luck":
        cap = f"{last}: skill or luck?"
    elif v == "mates":
        cap = f"{last}'s linemates tonight"
    elif v == "role":
        cap = f"{last}'s role & usage"
    elif v == "proj":
        cap = f"{last}'s season projection"
    elif v == "shots":
        span = rng.choice(["season", "l10", "game"])
        kw = {"pre": ['[data-sp="season"]'], "pan": True}
        if span == "l10":
            kw["taps"] = [(1.8, '[data-sp="l10"]')]
        elif span == "game":
            last_game = C.page("phone").evaluate("(pid) => (DATA.players[pid]?.form?.games || [])[0]?.d || null", p["id"])
            kw["taps"] = [(1.8, f"select.spgame|{last_game}")] if last_game else [(1.8, '[data-sp="l10"]')]
        cap = {"season": f"{last}'s shots this season", "l10": f"{last}'s shots, last 10 games", "game": f"{last}'s shots, last game"}[span]
    elif v == "gbg":
        seq = rng.choice([["g", "a", "p"], ["s", "g", "p"], ["p", "s", "a"]])
        first = {"g": "s", "s": "p", "p": "g"}[seq[0]]
        kw = {"pre": [f'[data-l10="{first}"]'], "taps": [(1.7 + i * 1.3, f'[data-l10="{x}"]') for i, x in enumerate(seq)]}
        cap = f"{last}, game by game: " + ", ".join(STATS[x].lower() for x in seq)
    elif v == "avg":
        st = rng.sample(["g", "a", "p", "s", "sh", "gx"], 2)
        unit = rng.choice(["pg", "pace", "ps"])
        kw = {"focus": "svg", "pre": ['[data-cv="chart"]', '[data-cm="windows"]', '[data-cu="pg"]', f'[data-cs="{st[0]}"]'],
              "taps": [(2.0, f'[data-cs="{st[1]}"]')] + ([(3.6, f'[data-cu="{unit}"]')] if unit != "pg" else [(3.6, '[data-cm="seasons"]')])}
        cap = f"{last}: {STATS[st[1]].lower()}, " + (UNITS[unit] if unit != "pg" else "season by season")
        note = NOTE["avg"] + (" Per game, at an 82-game pace or per season." if unit != "pg" else " Every season of his career.")
    c = C.card(p["id"], v, kind="phone", name=name, prefer_game=gid, dur=CLIP, **kw)
    return c and {**c, "caption": cap, "note": note}


def spec_for(g: dict, day: dt.date) -> dict:
    bpm = 112
    intro = S.seconds(6, bpm)
    t = intro
    parts = []
    pv = g.get("pv") or {}
    deck = clean(pv.get("deck", ""))
    if pv.get("title"):
        parts.append({"kind": "story", "a": t, "b": t + 7.0, "gid": g["id"], "kicker": pv.get("kicker") or f"{g['away_name']} at {g['home_name']}",
                      "title": pv["title"], "deck": deck[:260] + ("…" if len(deck) > 260 else ""),
                      "ac": S.team_color(g["away"]), "hc": S.team_color(g["home"]), "away": g["away"], "home": g["home"]})
        t += 7.0
    start = dt.datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(S.ET)
    return {"series": SERIES, "badge": "Game preview", "day": S.short_day(day), "title1": f"{g['away']} @ {g['home']}", "title2": "Preview",
            "sub": f"Tonight {start.strftime('%I:%M %p').lstrip('0')} ET · the numbers behind the game",
            "chips": [[g["away"], S.team_color(g["away"])], [g["home"], S.team_color(g["home"])]],
            "parts": parts, "timing": {"intro": intro, "outroAt": t, "total": t + S.seconds(8, bpm), "beat": 60 / bpm},
            "linkSub": "Every game, every player", "outro1": "Every number from this video:", "outro3": "Free to start · link in bio",
            "_g": g}


def add_media(spec: dict, C, sw: Path, day: dt.date, rng: random.Random) -> None:
    import preview_photos as PH
    g = spec["_g"]
    ph = PH.game_photos(day.isoformat()).get(g["id"])
    bg = None
    if ph:
        bg = "img/" + Path(PH.backdrop(ph, sw / "img" / f"bg-{ph['slug']}.jpg", (1080, 1920))).name
        for P in spec["parts"]:
            P["bg"] = bg
            if P["kind"] == "story":
                P["photo"] = "img/" + Path(PH.hero(ph, sw / "img" / f"ph-{ph['slug']}.jpg", (968, 420), dark=.5, edge=.7)).name
        spec["_photos"] = [ph]
    C.work = sw / "img"
    got = []
    used = set()                                        # (player or game, page type): never the same page twice
    for i, m in enumerate(plan(g, rng)):
        who = m["p"]["id"] if m["type"] == "card" else "game"
        c = None
        for v in [m["v"]] + [x for x in m.get("alts", []) if x != m["v"]]:   # a locked / missing page: the next one
            if (who, v) in used:
                continue
            used.add((who, v))
            try:
                c = record(C, {**m, "v": v}, g, rng, f"app{i}")
            except Exception as e:  # noqa: BLE001
                print(f"  app clip {i} ({v}) failed: {e}")
                c = None
            if c:
                break
        if c:
            got.append(c)
    for c in got:                                       # in order, each before the outro
        d = S.insert_app(spec, None, c)
        spec["parts"][-1]["note"] = c["note"]
        if bg:
            spec["parts"][-1]["bg"] = bg
    spec["_clips"] = got


def captions(spec: dict) -> dict:
    import preview_photos as PH
    g = spec["_g"]
    pv = g.get("pv") or {}
    title = f"{g['away']} @ {g['home']} preview: " + (pv.get("title") or f"{g['away_name']} at {g['home_name']}")
    names = [p["name"] for p in (g["A"][:1] + g["H"][:1])]
    body = (clean(pv.get("deck", "")) + "\n\n" if pv.get("deck") else "") + \
        "In this video: " + "; ".join(c["caption"] for c in spec["_clips"]) + "."
    tags = ["NHL", "hockey", "NHLstats", "hockeytok", g["away"], g["home"]] + [n.split()[-1] for n in names]
    yt = f"https://{S.SITE}/?ref=yt"
    cr = PH.credit_lines(spec.get("_photos") or [])
    return {"youtube": {"title": title[:100], "tags": tags[:12],
                        "description": f"{body}\n\nEvery game and every player, free to start: {yt}\n\nStats only.\n\n#" + " #".join(tags[:6])
                        + ("\n\n" + cr["long"] if cr["long"] else "")},
            "tiktok": {"caption": f"{title}\n\n{body}\n\nLink in bio: {S.SITE}\nStats only." + ("\n" + cr["short"] if cr["short"] else "")
                       + "\n#" + " #".join(t for t in tags[:7])}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(S.ET).date().isoformat())
    ap.add_argument("--n", type=int, default=3, help="how many games (default 3)")
    ap.add_argument("--games", help="comma list of game ids instead of the automatic choice")
    ap.add_argument("--out", default=str(S.ROOT / "video" / "out"))
    ap.add_argument("--music", default=os.environ.get("VIDEO_MUSIC_PATH"))
    ap.add_argument("--pro", default=os.environ.get("VIDEO_PRO_PATH") or str(S.ROOT / "output" / "pro" / "latest.json"),
                    help="the day's Pro data (unlocks every number on the app); used only when its date is --date")
    ap.add_argument("--preview", help="comma-separated times: still frames only")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date)
    mf = a.music if a.music and Path(a.music).is_file() else None
    print("music:", Path(mf).name if mf else "generated")
    out = Path(a.out) / a.date
    out.mkdir(parents=True, exist_ok=True)
    from appclips import AppClips
    jobs = []
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        pro = None
        if a.pro and Path(a.pro).is_file():
            pro = json.loads(Path(a.pro).read_text(encoding="utf-8"))
            if pro.get("date") != a.date:
                print(f"Pro data is for {pro.get('date')}, not {a.date}: free view")
                pro = None
        print("app view:", "Pro (every number unlocked)" if pro else "free (Pro numbers locked)")
        C = AppClips(work / "appsite", pro=pro)
        try:
            ids = a.games.split(",") if a.games else [g["id"] for g in choose(todays_games(C, a.date), a.n)]
            if not ids:
                print(f"No games on {a.date}.")
                return
            for gid in ids:
                g = stars(C, gid)
                if not g:
                    print(f"{gid}: not on the app")
                    continue
                rng = random.Random(f"{a.date}|{gid}")
                spec = spec_for(g, day)
                sw = work / f"p{gid}"
                (sw / "img").mkdir(parents=True)
                t0 = time.time()
                add_media(spec, C, sw, day, rng)
                print(f"{g['away']} @ {g['home']}: {len(spec['_clips'])} app clips, {spec['timing']['total']:.1f}s ({time.time() - t0:.0f}s)")
                for P in spec["parts"]:
                    print(f"    {P['a']:5.1f}-{P['b']:5.1f}s {P['kind']}: {P.get('cap') or P.get('title')}")
                jobs.append((g, spec, sw))
        finally:
            C.close()
        mpath = out / "manifest.json"
        for g, spec, sw in jobs:
            name = f"preview-{g['away']}-{g['home']}-{a.date}"
            t0 = time.time()
            S.render_video(spec, sw, out / name, seed=f"{a.date}|{g['id']}", music_file=mf,
                           preview=[float(x) for x in a.preview.split(",")] if a.preview else None)
            print(f"{name}: rendered in {time.time() - t0:.0f}s")
            if a.preview:
                continue
            man = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {"date": a.date, "items": []}
            man["items"] = [i for i in man["items"] if i.get("id") != name]
            rel = lambda p: str(Path(p).resolve().relative_to(S.ROOT)).replace("\\", "/") if str(Path(p).resolve()).startswith(str(S.ROOT)) else str(p)
            for plat, c in captions(spec).items():
                man["items"].append({"id": name, "series": SERIES, "platform": plat, "file": rel(out / f"{name}.mp4"),
                                     "cover": rel(out / f"{name}-cover.jpg"), **c})
            mpath.write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
        print("manifest:", mpath)


if __name__ == "__main__":
    main()
