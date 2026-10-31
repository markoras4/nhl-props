"""Late-season stakes vs the player model: does the model overrate top forwards on safe teams? (does not touch the website)

The schedule test found that top-3 forwards on teams 10+ points clear of the playoff line (last 20 games) play less
and score fewer points than their own averages. The model already follows recent ice time, so this checks the
model itself: site-like player model (usage weights, recent-form factor, backtest_usage.frames), calibration of
1+ point, goal and 3+ shots by stakes group, for top-3 forwards (by ice time so far, 20+ GP) and for everyone.
Stakes: point-in-time standings (2 points a win, 1 an OT/SO loss), gap to the 8th-place team in the conference:
out (10+ behind), bubble (within 4), safe (10+ ahead), mid; 'early' before a team's last 20 games.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest_usage as bu
from .backtest import season_boxscores
from .backtest_schedule import conf

ORDER = ["early", "safe", "mid", "bubble", "out"]
LAB = {"early": "earlier in the season", "safe": "safe (10+ pts ahead)", "mid": "between", "bubble": "on the bubble (within 4 pts)",
       "out": "out of the race (10+ pts behind)"}


def stakes_map(games: pd.DataFrame) -> dict:
    games = games.sort_values(["date", "id"])
    n = pd.concat([games.home, games.away]).value_counts().to_dict()
    teams = list(n)
    pts, gp, out = {}, {}, {}
    for g in games.itertuples():
        for team in (g.home, g.away):
            st = "early"
            if n[team] - gp.get(team, 0) <= 20 and gp.get(team, 0) >= 40:
                table = sorted((pts.get(t, 0) for t in teams if conf(t) == conf(team) and t != team), reverse=True)
                eighth = table[7] if len(table) >= 8 else 0
                seventh = table[6] if len(table) >= 7 else 0
                me = pts.get(team, 0)
                gap = me - (eighth if me <= eighth else seventh)
                st = "out" if gap < -10 else ("safe" if gap > 10 else ("bubble" if abs(gap) <= 4 else "mid"))
            out[(int(g.id), team)] = st
        for t in (g.home, g.away):
            gp[t] = gp.get(t, 0) + 1
        win, lose = (g.home, g.away) if g.hs > g.as_ else (g.away, g.home)
        pts[win] = pts.get(win, 0) + 2
        if g.last != "REG":
            pts[lose] = pts.get(lose, 0) + 1
    return out


def cal(p, y, m):
    if m.sum() < 100:
        return None
    pm, ym = float(p[m].mean()), float(y[m].mean())
    se = math.sqrt(max(float((p[m] * (1 - p[m])).mean()), 1e-9) / m.sum())
    return {"n": int(m.sum()), "pred": pm, "act": ym, "ratio": ym / pm if pm else None, "t": (ym - pm) / se}


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = bu.frames(src, seasons, cfg, log)
    st = {}
    for y in sorted(seasons):
        games, _, _ = season_boxscores(src, y, cfg["workers"], log)
        st.update(stakes_map(games))
    d["st"] = [st.get((int(g), t), "early") for g, t in zip(d.gid, d.team)]
    d = d[(d.c_gp >= 5) & (d.lam_g > 0)].reset_index(drop=True)
    pt = bu.parts(d, cfg)
    um = cfg.get("usage_mult") or {}
    lg, ls = bu.lams(d, pt, cfg, um.get("s_sog", 2.0), um.get("xg_goal", 4.0))
    lg, ls = np.asarray(lg, float), np.asarray(ls, float)
    P = {"1+ point": 1 - np.exp(-(lg + d.lam_a.values)), "Goal": 1 - np.exp(-lg), "Assist": 1 - np.exp(-d.lam_a.values), "3+ shots": bu.p_over(ls, 2.5)}
    Y = {"1+ point": (d.g + d.a >= 1).values, "Goal": (d.g >= 1).values, "Assist": (d.a >= 1).values, "3+ shots": (d.s >= 3).values}
    P = {k: np.asarray(v, float) for k, v in P.items()}
    Y = {k: np.asarray(v, float) for k, v in Y.items()}
    # top-3 forwards per team-game by ice time per game so far (20+ GP)
    d["tpg"] = d.c_toi / d.c_gp.clip(lower=1)
    f = d[(d.posg == "F") & (d.c_gp >= 20)]
    top_idx = f.sort_values("tpg", ascending=False).groupby(["gid", "team"]).head(3).index
    top = np.zeros(len(d), bool); top[top_idx] = True
    rep = {"seasons": seasons, "rows": int(len(d)), "top_rows": int(top.sum())}
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    first = d.season.isin(half).values
    for who, wm in (("top3", top), ("all", np.ones(len(d), bool)), ("rest_F", (d.posg == "F").values & ~top), ("D", (d.posg == "D").values)):
        rep[who] = {}
        for mk in P:
            rows = []
            for k in ORDER:
                m = wm & (d.st == k).values
                c = cal(P[mk], Y[mk], m)
                if c:
                    c["group"] = LAB[k]
                    c["halves"] = [x and x["ratio"] for x in (cal(P[mk], Y[mk], m & first), cal(P[mk], Y[mk], m & ~first))]
                    rows.append(c)
            rep[who][mk] = rows
    (out_dir / "stakes.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep, half)
    (out_dir / "stakes.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r, half):
    L = [f"# Late-season stakes vs the player model: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}",
         f"Player-games: {r['rows']:,} · top-3 forward games: {r['top_rows']:,} · 'halves' = actual/model in {half} and in the later seasons", ""]
    names = {"top3": "Top-3 forwards (by ice time so far)", "all": "All skaters", "rest_F": "Other forwards", "D": "Defencemen"}
    for who in ("top3", "rest_F", "D", "all"):
        L += [f"## {names[who]}", ""]
        for mk, rows in r[who].items():
            L += [f"**{mk}**", "", "| Stakes | n | Model | Actual | Actual / model | t | Halves |", "|---|---|---|---|---|---|---|"]
            for x in rows:
                hv = " / ".join("–" if v is None else f"{v:.3f}" for v in x["halves"])
                L.append(f"| {x['group']} | {x['n']:,} | {x['pred']:.1%} | {x['act']:.1%} | {x['ratio']:.3f} | {x['t']:+.1f} | {hv} |")
            L.append("")
    return "\n".join(L) + "\n"
