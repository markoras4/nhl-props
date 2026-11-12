"""Heat Index: points above (or below) the model over a player's last 10 games. How much of it carries over?
(does not touch the website)

HI = points in his last 10 games - points the model expected in them (model without the recent-form term).
Questions:
  (1) Is beating the model a repeatable skill? Split-half reliability of a player-season's (points - expected).
  (2) Carry-over: slope of the next 10 games' (points - expected) on HI, for the raw model and for the live model
      (with its recent-form term). Slope 0.25 = a quarter of the heat is real.
  (3) Next-game calibration by HI bucket for the live model: if it is right in every bucket, the site can show HI
      with "the model already counts the part that is real".
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest_metrics import frames, rel

BINS = [-99, -4, -2.5, -1, 1, 2.5, 4, 99]
LABELS = ["-4 or colder", "-4 to -2.5", "-2.5 to -1", "about even (-1 to +1)", "+1 to +2.5", "+2.5 to +4", "+4 or hotter"]


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, log)
    K = cfg["form_k"]
    # the model before the recent-form term
    d["raw_g"] = d.lam_g * (d.E10_g + K["g"]) / (d.X10_g + K["g"])
    d["raw_a"] = d.lam_a * (d.E10_a + K["a"]) / (d.X10_a + K["a"])
    d["raw_p"] = d.raw_g + d.raw_a
    d["hi"] = (d.X10_g + d.X10_a) - (d.E10_g + d.E10_a)
    d = d.sort_values(["pid", "season", "date", "gid"])
    g = d.groupby(["pid", "season"], sort=False)
    d["n_before"] = g.cumcount()
    for col, lam in (("f_raw", "raw_p"), ("f_live", "lam_p")):
        res = d.pt - d[lam]
        d[col] = res.groupby([d.pid, d.season]).transform(lambda v: v[::-1].rolling(10, min_periods=10).sum()[::-1].shift(-1) if len(v) else v)
    rep = {"seasons": [int(s) for s in sorted(d.season.unique())]}

    # (1) skill: season (points - raw expected), odd vs even games
    d["res"] = d.pt - d.raw_p
    h = d.groupby(["pid", "season", "half"]).agg(r=("res", "sum"), n=("res", "size")).reset_index()
    h["r"] = h.r / h.n
    w = h.pivot_table(index=["pid", "season"], columns="half", values="r").dropna()
    n = d.groupby(["pid", "season"]).size()
    w = w[n.reindex(w.index) >= 40]
    r = rel(w[0], w[1])
    rep["skill"] = {"split_half_r": r, "full_season": 2 * r / (1 + r), "n": int(len(w))}

    # (2) carry-over slopes (players 10+ games into the season, with 10 games still to come)
    q = d[(d.n_before >= 10)].dropna(subset=["f_raw", "f_live"])
    rep["carry"] = {}
    for col in ("f_raw", "f_live"):
        b = float(np.polyfit(q.hi, q[col], 1)[0])
        rep["carry"][col] = {"slope": b, "n": int(len(q))}

    # (3) next-game calibration by HI, live model and raw model
    e = d[d.n_before >= 10].copy()
    e["b"] = pd.cut(e.hi, BINS, labels=LABELS)
    rows = []
    for k, t in e.groupby("b", observed=True):
        y = (t.pt >= 1).astype(float)
        p_live, p_raw = 1 - np.exp(-t.lam_p), 1 - np.exp(-t.raw_p)
        se = float(np.sqrt(p_live.mean() * (1 - p_live.mean()) / len(t)))
        rows.append({"bucket": str(k), "n": int(len(t)), "raw": float(p_raw.mean()), "live": float(p_live.mean()), "actual": float(y.mean()),
                     "pts_next10_raw": float(t.f_raw.mean()), "pts_next10_live": float(t.f_live.mean()), "hi": float(t.hi.mean()), "se": se})
    rep["buckets"] = rows
    rep["share_hot"] = float((e.hi >= 2.5).mean())

    (out_dir / "heat.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "heat.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    c = r["carry"]
    L = [f"# Heat Index ({r['seasons']})", "",
         f"- beating the model as a skill (points - expected per game, odd vs even games, {r['skill']['n']} player-seasons with 40+ GP): "
         f"r = {r['skill']['split_half_r']:.3f} -> full season {r['skill']['full_season']:.3f}",
         f"- carry-over into the next 10 games (n {c['f_raw']['n']:,}): raw model {c['f_raw']['slope']:+.3f} points per point of heat · "
         f"live model (with recent form) {c['f_live']['slope']:+.3f}",
         f"- player-games with HI +2.5 or hotter: {r['share_hot']:.1%}", "",
         "| Heat Index (last 10) | n | mean HI | Raw model point chance | Live model | Happened | Live diff | Next 10 vs raw | Next 10 vs live |",
         "|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {x['bucket']} | {x['n']:,} | {x['hi']:+.2f} | {x['raw']:.1%} | {x['live']:.1%} | {x['actual']:.1%} | {(x['actual']-x['live'])*100:+.1f} ± {x['se']*200:.1f} | "
          f"{x['pts_next10_raw']:+.2f} | {x['pts_next10_live']:+.2f} |" for x in r["buckets"]]
    return "\n".join(L) + "\n"
