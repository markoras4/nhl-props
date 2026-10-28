"""Grade saved predictions against final boxscores.

Answers the important question: when the model says 30%, does it happen ~30% of the time?
Also tests which history weight works best for over/under.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .sources import Source

MARKETS = [("g", "Goal"), ("a", "Assist"), ("pt", "Point"), ("fg", "First goal")]


def _bet_name(k: str) -> str:
    fixed = {"g": "Goal", "a": "Assist", "pt": "Point", "g2": "2+ goals", "a2": "2+ assists", "pt2": "2+ points"}
    if k in fixed:
        return fixed[k]
    return f"Over {k[1:]} " + {"s": "SOG", "h": "hits", "b": "blocks"}[k[0]]


def _bet_order(k: str):
    return ({"g": 0, "a": 1, "pt": 2, "g2": 3, "a2": 4, "pt2": 5}.get(k, 9), k)


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def summarize(pairs: list[tuple[float, int]]) -> dict | None:
    if not pairs:
        return None
    n = len(pairs)
    base = sum(y for _, y in pairs) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    brier_base = sum((base - y) ** 2 for _, y in pairs) / n
    bins = []
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        sub = [(p, y) for p, y in pairs if lo <= p < hi or (b == 9 and p == 1)]
        if sub:
            bins.append({"lo": lo, "hi": hi, "n": len(sub), "pred": sum(p for p, _ in sub) / len(sub),
                         "actual": sum(y for _, y in sub) / len(sub)})
    return {"n": n, "mean_pred": sum(p for p, _ in pairs) / n, "hit_rate": base, "brier": brier,
            "skill": 1 - brier / brier_base if brier_base else 0.0,
            "logloss": sum(_ll(p, y) for p, y in pairs) / n, "bins": bins}


def _box_players(box: dict) -> dict[int, dict]:
    out = {}
    for side in ("homeTeam", "awayTeam"):
        grp = (box.get("playerByGameStats") or {}).get(side) or {}
        for key in ("forwards", "defense", "defensemen"):
            for p in grp.get(key, []) or []:
                out[int(p["playerId"])] = {"g": p.get("goals", 0) or 0, "a": p.get("assists", 0) or 0,
                                           "s": p.get("sog", p.get("shots", 0)) or 0,
                                           "h": p.get("hits", 0) or 0, "b": p.get("blockedShots", 0) or 0,
                                           "pim": p.get("pim", 0) or 0}
    return out


def _p1_goals(src: Source, date: str) -> dict:
    """game id -> (home, away) 1st-period goals, from the NHL daily score feed"""
    js = src.json(f"https://api-web.nhle.com/v1/score/{date}", f"nhl/score_final/{date}.json", 6) or {}
    out = {}
    ab = lambda x: x.get("default") if isinstance(x, dict) else x
    for g in js.get("games") or []:
        if g.get("gameState") not in ("OFF", "FINAL"):
            continue
        home = ab((g.get("homeTeam") or {}).get("abbrev"))
        h = a = 0
        for x in g.get("goals") or []:
            pdsc = x.get("periodDescriptor") or {}
            if pdsc.get("number") == 1 and pdsc.get("periodType", "REG") == "REG":
                if ab(x.get("teamAbbrev")) == home:
                    h += 1
                else:
                    a += 1
        out[int(g["id"])] = (h, a)
    return out


def _first_scorers(src: Source, date: str) -> dict:
    """game id -> player id of the first goal (None if no goal before a shootout), from the NHL daily score feed"""
    js = src.json(f"https://api-web.nhle.com/v1/score/{date}", f"nhl/score_final/{date}.json", 6) or {}
    out = {}
    for g in js.get("games") or []:
        if g.get("gameState") not in ("OFF", "FINAL"):
            continue
        goals = [x for x in (g.get("goals") or []) if (x.get("periodDescriptor") or {}).get("periodType") != "SO"]
        out[int(g["id"])] = int(goals[0]["playerId"]) if goals and goals[0].get("playerId") else None
    return out


def grade(src: Source, history_dir: Path, log=print) -> dict:
    history_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in history_dir.glob("20*.json"))
    props = {k: [] for k, _ in MARKETS}
    sog: dict[str, list] = {}
    ou_model: dict[str, list] = {}
    ou_hist: dict[str, list] = {}
    days = []
    hb: dict[str, list] = {}
    shots: dict[str, list] = {}
    gm: dict[str, list] = {}
    more: dict[str, list] = {}
    pbp = {}
    for fp in sorted(Path("data/shots").glob("*.json")) if Path("data/shots").exists() else []:
        try:
            pbp.update(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            pass
    bets: dict[str, list] = {}  # (stake 1 at book odds when model EV >= 5%)
    for f in files:
        h = json.loads(f.read_text(encoding="utf-8"))
        finals, boxes = 0, {}
        for g in h["games"]:
            box = src.boxscore(g["id"])
            if not box or box.get("gameState") not in ("OFF", "FINAL"):
                continue
            boxes[g["id"]] = box
            finals += 1
            total = (box.get("homeTeam", {}).get("score") or 0) + (box.get("awayTeam", {}).get("score") or 0)
            mk = g.get("mk")
            if mk:
                hs_, as__ = (box.get("homeTeam") or {}).get("score"), (box.get("awayTeam") or {}).get("score")
                lp = ((box.get("gameOutcome") or {}).get("lastPeriodType")) or "REG"
                if hs_ is not None and as__ is not None:
                    rh, ra = hs_, as__
                    if lp != "REG":                       # the OT/shootout winner's extra goal isn't a regulation goal
                        rh, ra = (hs_ - 1, as__) if hs_ > as__ else (hs_, as__ - 1)
                    res_ = {"1": rh > ra, "X": rh == ra, "2": rh < ra, "hpl": hs_ - as__ >= 2, "apl": as__ - hs_ >= 2,
                            "sh_h": (as__ - (1 if lp == "SO" and as__ > hs_ else 0)) == 0, "sh_a": (hs_ - (1 if lp == "SO" and hs_ > as__ else 0)) == 0,
                            "btts": hs_ >= 1 and as__ >= 1, "b60": rh >= 1 and ra >= 1, "b2": rh >= 2 and ra >= 2, "htt2.5": hs_ > 2.5, "htt3.5": hs_ > 3.5, "att2.5": as__ > 2.5, "att3.5": as__ > 3.5}
                    p1 = _p1_goals(src, h["date"]).get(g["id"])
                    if p1:
                        res_.update({"p1o0.5": sum(p1) > 0.5, "p1o1.5": sum(p1) > 1.5, "p1o2.5": sum(p1) > 2.5, "p1x": p1[0] == p1[1]})
                    for k, yv in res_.items():
                        if k in mk:
                            gm.setdefault(k, []).append((mk[k], int(yv)))
            sh = g.get("shots")
            hs, as_ = (box.get("homeTeam") or {}).get("sog"), (box.get("awayTeam") or {}).get("sog")
            if sh and hs is not None and as_ is not None:
                gl = {}
                for side in ("homeTeam", "awayTeam"):
                    for p_ in ((box.get("playerByGameStats") or {}).get(side) or {}).get("goalies") or []:
                        if p_.get("starter"):
                            gl[side] = (int(p_["playerId"]), p_.get("saves"))
                st = g.get("starters") or {}
                for k, v in sh["p"].items():
                    if k.startswith("svh") or k.startswith("sva"):
                        side, team = ("homeTeam", g["home"]) if k[2] == "h" else ("awayTeam", g["away"])
                        got = gl.get(side)
                        if not got or got[1] is None or st.get(team) != got[0]:
                            continue      # graded only when the goalie we projected actually started
                        shots.setdefault("Goalie saves", []).append((v, int(got[1] > float(k[3:]))))
                    elif k[0] == "t":
                        shots.setdefault("Total shots", []).append((v, int(hs + as_ > float(k[1:]))))
                    else:
                        val = hs if k[0] == "h" else as_
                        shots.setdefault("Team shots", []).append((v, int(val > float(k[1:]))))
            for L, p in g["model"].items():
                y = int(total > float(L))
                ou_model.setdefault(L, []).append((p, y))
                ou_hist.setdefault(L, []).append((g["hist"][L], y))
        stats = {gid: _box_players(b) for gid, b in boxes.items()}
        first_scorer = _first_scorers(src, h["date"]) if boxes else {}
        n_props = 0
        for pr in h["props"]:
            st = stats.get(pr["game"], {}).get(pr["id"])
            if st is None:
                continue  # game not final, or player scratched
            n_props += 1
            props["g"].append((pr["p"]["g"], int(st["g"] >= 1)))
            # multi-point, penalties, power-play points and faceoffs won (play-by-play for the last two)
            P = pr["p"]
            for k, lab, yv in (("pt2", "2+ points", st["g"] + st["a"] >= 2), ("pt3", "3+ points", st["g"] + st["a"] >= 3),
                               ("g2", "2+ goals", st["g"] >= 2), ("g3", "Hat trick", st["g"] >= 3), ("a2", "2+ assists", st["a"] >= 2),
                               ("pim0.5", "PIM over 0.5", st.get("pim", 0) > 0.5), ("pim2.5", "PIM over 2.5", st.get("pim", 0) > 2.5)):
                if k in P:
                    more.setdefault(lab, []).append((P[k], int(yv)))
            gp_ = pbp.get(str(pr["game"]))
            if gp_ and "gl" in gp_:
                if "ppp" in P:
                    n = sum(1 for g_ in gp_["gl"] if g_[4] and pr["id"] in (g_[0], g_[1], g_[2]))
                    more.setdefault("Power-play point", []).append((P["ppp"], int(n >= 1)))
                wins = sum(1 for w, l in gp_.get("fo", []) if w == pr["id"])
                for k, v in P.items():
                    if k.startswith("fo"):
                        more.setdefault("Faceoffs won (all lines)", []).append((v, int(wins > float(k[2:]))))
            fgs = first_scorer.get(pr["game"], "?")
            if "fg" in pr["p"] and fgs != "?":
                props["fg"].append((pr["p"]["fg"], int(fgs == pr["id"])))
            props["a"].append((pr["p"]["a"], int(st["a"] >= 1)))
            props["pt"].append((pr["p"]["pt"], int(st["g"] + st["a"] >= 1)))
            res = {"g": st["g"] >= 1, "a": st["a"] >= 1, "pt": st["g"] + st["a"] >= 1,
                   "g2": st["g"] >= 2, "a2": st["a"] >= 2, "pt2": st["g"] + st["a"] >= 2}
            for m, od in (pr.get("o") or {}).items():
                if m not in pr["p"] or not od:
                    continue
                if m in res:
                    won = res[m]
                elif m[0] in "shb" and m[1:2].isdigit():
                    won = st.get({"s": "s", "h": "h", "b": "b"}[m[0]], 0) > float(m[1:])
                else:
                    continue
                if pr["p"][m] * od - 1 >= 0.05:
                    bets.setdefault(m, []).append(od - 1 if won else -1.0)
            for k, v in pr["p"].items():
                if k.startswith("s"):
                    sog.setdefault(k[1:], []).append((v, int(st["s"] > float(k[1:]))))
                elif k[0] in "hb" and k[1:2].isdigit():
                    hb.setdefault(k, []).append((v, int(st[k[0]] > float(k[1:]))))
        days.append({"date": h["date"], "games_final": finals, "games": len(h["games"]), "props": n_props})

    blends = {}
    for L in ou_model:
        res = []
        for w10 in range(0, 11):
            w = w10 / 10
            pairs = [((1 - w) * pm + w * ph, y) for (pm, y), (ph, _) in zip(ou_model[L], ou_hist[L])]
            res.append({"w": w, **{k: v for k, v in summarize(pairs).items() if k in ("brier", "logloss")}})
        blends[L] = res

    out = {
        "days": days,
        "props": {name: summarize(props[k]) for k, name in MARKETS},
        "sog": {f"Over {L} SOG": summarize(v) for L, v in sorted(sog.items())},
        "shots": {k: summarize(v) for k, v in shots.items()},
        "gm": {k: summarize(v) for k, v in gm.items()},
        "more": {k: summarize(v) for k, v in more.items()},   # all five lines around each projection pooled
        "hb": {f"Over {k[1:]} {'hits' if k[0] == 'h' else 'blocks'}": summarize(v) for k, v in sorted(hb.items())},
        "ou_model": {f"Over {L}": summarize(v) for L, v in ou_model.items()},
        "ou_hist": {f"Over {L}": summarize(v) for L, v in ou_hist.items()},
        "ou_blends": blends,
        "bets": {_bet_name(k): {"n": len(v), "profit": sum(v), "roi": sum(v) / len(v), "won": sum(1 for x in v if x > 0)}
                 for k, v in sorted(bets.items(), key=lambda kv: _bet_order(kv[0])) if v},
    }
    (history_dir / "graded.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    n = sum(d["props"] for d in days)
    log(f"Graded {len(days)} slate(s): {sum(d['games_final'] for d in days)} final games, {n} player-games.")
    for name, s in out["props"].items():
        if s:
            log(f"  {name:7s} n={s['n']:5d}  predicted {s['mean_pred']:.3f}  actual {s['hit_rate']:.3f}  "
                f"Brier {s['brier']:.4f}  skill {s['skill']:+.3f}")
    return out
