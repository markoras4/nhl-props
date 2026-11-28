"""evenout Black Week promo Short (9:16): Pro is free for everyone until 31 December, sign up by 31 December, no card.

The intro slams "Black Week / Pro is free", then a fast run through Pro on the real app (season projection with
milestone chances, shot profile, the chance of each final score, playoff chances, the players explorer), then
"No card · nothing to cancel · free account", then the outro. Stats only: odds stay hidden (appclips.py).

    py -X utf8 video/promo.py --pro <today's Pro data json> --music "<licensed song.mp3>" [--theme december]

Themes: "blackweek" (the first cut, 28 Nov) and "december" (the same offer as "December: Pro is free", with other app
sections: linemates, skill or luck, tonight's lines, game by game, what each defence allows).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import series as S  # noqa: E402

CLIP = 4.2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(S.ET).date().isoformat())
    ap.add_argument("--out", default=str(S.ROOT / "video" / "out"))
    ap.add_argument("--music", default=os.environ.get("VIDEO_MUSIC_PATH"))
    ap.add_argument("--pro", help="Pro data json (the app shown as a Pro user sees it)")
    ap.add_argument("--preview", help="comma-separated times: still frames only")
    ap.add_argument("--theme", default="blackweek", choices=["blackweek", "december"])
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date)
    mf = a.music if a.music and Path(a.music).is_file() else None
    pro = json.loads(Path(a.pro).read_text(encoding="utf-8")) if a.pro and Path(a.pro).is_file() else None
    out = Path(a.out) / a.date
    out.mkdir(parents=True, exist_ok=True)
    bpm = 120
    from appclips import AppClips
    with tempfile.TemporaryDirectory() as td:
        sw = Path(td) / "promo"
        (sw / "img").mkdir(parents=True)
        C = AppClips(sw / "img", pro=pro)
        try:
            # tonight's biggest name (highest projected season points) and his game
            star = C.page("phone").evaluate("""() => { const up = g => !g.state || ['FUT','PRE'].includes(g.state);
                const gs = DATA.games.filter(up).sort((a, b) => (a.start || '').localeCompare(b.start || ''));
                const day = gs[0]?.date; const today = gs.filter(g => g.date === day); const teams = new Set(today.flatMap(g => [g.home, g.away]));
                const ps = Object.values(DATA.players).filter(p => teams.has(p.team) && p.posg !== 'G' && !p.injury)
                    .map(p => ({id: String(p.id), name: p.name, team: p.team, pts: (DATA.proj?.[p.id]?.p || [])[1] || 0})).sort((a, b) => b.pts - a.pts);
                const p = ps[0], g = today.find(g => g.home === p.team || g.away === p.team);
                return {p, g: g && {id: String(g.id), away: g.away, home: g.home}, second: ps.find(x => x.team !== p.team)}; }""")
            p, g, p2 = star["p"], star["g"], star["second"]
            last, last2 = p["name"].split()[-1], p2["name"].split()[-1]
            moments = [
                (lambda: C.card(p["id"], "mates", kind="phone", name="d0", prefer_game=g["id"], dur=CLIP + .3), f"Who {last} plays with tonight",
                 "Tonight's linemates, and how he scores with and without them."),
                (lambda: C.card(p2["id"], "luck", kind="phone", name="d1", dur=CLIP), f"{last2}: skill or luck?",
                 "Is the streak real? Shooting % against what his chances were worth."),
                (lambda: C.game(g["id"], "lines", kind="phone", name="d2", dur=CLIP), f"{g['away']} @ {g['home']}: tonight's lines",
                 "Every line and every pair, for every game."),
                (lambda: C.card(p["id"], "gbg", kind="phone", name="d3", prefer_game=g["id"], dur=CLIP), f"{last}, game by game",
                 "Goals, assists, points and shots in every game this season."),
                (lambda: C.game(g["id"], "defence", kind="phone", name="d4", dur=CLIP), "What each defence allows",
                 "Shots and points allowed, by position, for every team."),
            ] if a.theme == "december" else [
                (lambda: C.card(p["id"], "proj", kind="phone", name="c0", prefer_game=g["id"], dur=CLIP), f"{last}'s season projection",
                 "Where every season ends up, with milestone chances."),
                (lambda: C.card(p2["id"], "shots", kind="phone", name="c1", dur=CLIP + .4, pre=['[data-sp="season"]'], pan=True), f"{last2}'s shot profile",
                 "How often, from where, how dangerous: ranked against the league."),
                (lambda: C.game(g["id"], "scores", kind="phone", name="c2", dur=CLIP), f"{g['away']} @ {g['home']}: every final score",
                 "The chance of every final score, for every game."),
                (lambda: C.futures([g["away"], g["home"]], kind="phone", name="c3", dur=CLIP), "Playoff chances",
                 "10,000 simulated seasons, every team."),
                (lambda: C.card(p["id"], "built", kind="phone", name="c4", prefer_game=g["id"], dur=CLIP), f"How {last}'s number is built",
                 "Every model number, explained step by step."),
            ]
            clips = []
            for rec, cap, note in moments:
                try:
                    c = rec()
                except Exception as e:  # noqa: BLE001
                    print("clip failed:", e)
                    c = None
                if c:
                    clips.append({**c, "caption": cap, "note": note})
        finally:
            C.close()
        intro = S.seconds(6, bpm)
        t = intro
        parts = [{"kind": "promo", "a": t, "b": t + 4.0, "kick": "evenout Pro", "big": ["Every number.", "Unlocked."],
                  "sub": "Free for everyone until 31 December."}]
        t += 4.0
        dec = a.theme == "december"
        spec = {"series": "promo", "badge": "December" if dec else "Black Week", "day": "UNTIL 31 DEC",
                "title1": "December" if dec else "Black Week", "title2": "Pro is free",
                "sub": "Every Pro feature on evenout, free until 31 December",
                "chips": [["NO CARD", "#ff4fa8"], ["NOTHING TO CANCEL", "#b794ff"]],
                "parts": parts, "timing": {"intro": intro, "outroAt": t, "total": t + S.seconds(8, bpm), "beat": 60 / bpm},
                "linkSub": "Pro free until 31 December", "outro1": "Pro is free. Go get it.", "outro3": "evenout.app · link in bio",
                "disclaimer": "Stats from NHL game data · free until 31 Dec 2026, then back to Free"}
        for c in clips:
            S.insert_app(spec, None, c)
            spec["parts"][-1]["note"] = c["note"]
        T = spec["timing"]
        spec["parts"].append({"kind": "promo", "a": T["outroAt"], "b": T["outroAt"] + 5.5, "kick": "December" if dec else "Black Week",
                              "big": ["Sign up", "for free"], "checks": ["Pro free until 31 December", "No card needed", "Nothing to cancel"]})
        T["outroAt"] += 5.5
        T["total"] += 5.5
        print(f"promo: {len(clips)} app clips, {T['total']:.1f}s")
        name = f"promo-{a.theme}-{a.date}"
        S.render_video(spec, sw, out / name, seed=f"promo|{a.date}", music_file=mf,
                       preview=[float(x) for x in a.preview.split(",")] if a.preview else None)
    if a.preview:
        return
    title = "December: evenout Pro is free" if a.theme == "december" else "Black Week: evenout Pro is free until 31 December"
    dec = a.theme == "december"
    feats = ("tonight's lines and linemates, skill or luck, every player game by game, what each defence allows." if dec else
             "season projections, shot profiles, the chance of every final score, playoff chances, every player's numbers explained.")
    tags = "#December" if dec else "#BlackFriday #BlackWeek"
    body = (f"Every Pro feature on evenout, free for everyone until 31 December: {feats}\n"
            "Create a free account. No card, no payment, nothing to cancel: on 1 January your account simply goes back to Free.")
    man_p = out / "manifest.json"
    man = json.loads(man_p.read_text(encoding="utf-8")) if man_p.exists() else {"date": a.date, "items": []}
    man["items"] = [i for i in man["items"] if i.get("id") != name]
    rel = lambda q: str(Path(q).resolve().relative_to(S.ROOT)).replace("\\", "/")
    for plat, c in {"youtube": {"title": title, "tags": ["NHL", "hockey", "NHLstats", "evenout"] + (["December"] if dec else ["BlackWeek", "BlackFriday"]),
                                "description": f"{body}\n\nhttps://{S.SITE}/?ref=yt\n\nStats only.\n\n#NHL #hockey {tags}"},
                    "tiktok": {"caption": f"{title}\n\n{body}\n\nLink in bio: {S.SITE}\nStats only.\n#NHL #hockey #NHLstats #hockeytok {tags}"}}.items():
        man["items"].append({"id": name, "series": "promo", "platform": plat, "file": rel(out / f"{name}.mp4"), "cover": rel(out / f"{name}-cover.jpg"), **c})
    man_p.write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
    print("video:", out / f"{name}.mp4")


if __name__ == "__main__":
    main()
