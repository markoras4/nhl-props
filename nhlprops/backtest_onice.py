"""On-ice chances (the useful version of plus/minus): does a player's on-ice expected goals for predict his assists
and points beyond the model? Mainly a question for defencemen. (does not touch the website)

From MoneyPuck 5-on-5, his two previous seasons (weights 1 and 0.6), before the season being tested:
  env = on-ice xGF/60 (shrunk toward his team without him) vs the league's 5-on-5 xGF/60: the offence around him
  rel = on-ice xGF/60 vs his team's xGF/60 with him on the bench: his own on-ice impact
Poisson regressions fitted on the older seasons, judged on the newer ones, separately for D and F:
  assists ~ exp(b0 + b1 log(model) + b2 log(env))     points likewise; and the same with rel instead of env.
b2 = 0: no information beyond the model.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest_usage import frames, parts, lams
from .backtest_shooters import pois_glm
from .grade import summarize
from .model import _hours, _num, _sit


def onice(src, season):
    acc = {}
    lg = [0.0, 0.0]
    for y, w in ((season - 1, 1.0), (season - 2, 0.6)):
        d = src.moneypuck("skaters", y, False)
        if d is None:
            continue
        d = d[_sit(d) == "5on5"]
        if "OnIce_F_xGoals" not in d.columns:
            continue
        onh, offh = _hours(d, "icetime"), _num(d, "timeOnBench") / 3600
        for pid, a, b, c, e in zip(pd.to_numeric(d["playerId"], errors="coerce"), onh, _num(d, "OnIce_F_xGoals"), offh, _num(d, "OffIce_F_xGoals")):
            if pd.isna(pid):
                continue
            r = acc.setdefault(int(pid), [0.0] * 4)
            for i, v in enumerate((a, b, c, e)):
                r[i] += w * float(v)
        lg[0] += w * float(_num(d, "OnIce_F_xGoals").sum()); lg[1] += w * float(onh.sum())
    lg_rate = lg[0] / lg[1] if lg[1] else 2.5
    out = {}
    for pid, (onh, onf, offh, offf) in acc.items():
        if onh <= 0 or offh <= 0:
            continue
        off_rate = offf / offh
        on_s = (onf + 8.0 * off_rate) / (onh + 8.0)
        out[pid] = (on_s / lg_rate, on_s / off_rate if off_rate > 0 else 1.0)
    return out


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = frames(src, seasons, cfg, log)
    d = d[(d.c_gp >= 5) & (d.lam_g > 0)].reset_index(drop=True)
    pt = parts(d, cfg)
    um = cfg.get("usage_mult") or {}
    lg, _ = lams(d, pt, cfg, um.get("s_sog", 2.0), um.get("xg_goal", 4.0))
    d["lg"] = np.asarray(lg, float)
    env, rel = np.ones(len(d)), np.ones(len(d))
    for s in sorted(d.season.unique()):
        oi = onice(src, int(s))
        m = (d.season == s).values
        e_ = [oi.get(int(p), (np.nan, np.nan)) for p in d.pid[m]]
        env[m] = [x[0] for x in e_]; rel[m] = [x[1] for x in e_]
    d["env"], d["rel"] = env, rel
    d = d[d.env.notna() & d.rel.notna()].reset_index(drop=True)
    half = sorted(seasons)[: max(1, len(seasons) // 2)]
    rep = {"seasons": seasons, "fit_seasons": half, "rows": int(len(d)), "groups": {}}
    for pos in ("D", "F"):
        q = d[d.posg == pos].reset_index(drop=True)
        fm = q.season.isin(half).values; tm = ~fm
        res = {"n_test": int(tm.sum())}
        for mk, lam, y in (("Assist", q.lam_a.values, q.a.values), ("Point", q.lg.values + q.lam_a.values, (q.g + q.a).values)):
            yy = y.astype(float)
            out = {}
            for feat in ("env", "rel"):
                X = np.column_stack([np.ones(len(q)), np.log(np.clip(lam, 0.01, None)), np.log(np.clip(q[feat].values, 0.4, 2.5))])
                w = pois_glm(X[fm], yy[fm])
                X0 = X[:, :2]; w0 = pois_glm(X0[fm], yy[fm])
                mu1, mu0 = np.exp(X @ w), np.exp(X0 @ w0)
                p1, p0 = 1 - np.exp(-mu1), 1 - np.exp(-mu0)
                yb = (yy >= 1).astype(float)
                s1 = summarize(list(zip(np.clip(p1[tm], 1e-4, 1 - 1e-4), yb[tm])))
                s0 = summarize(list(zip(np.clip(p0[tm], 1e-4, 1 - 1e-4), yb[tm])))
                # calibration of the plain model by feature tercile
                cut = np.quantile(q[feat].values[tm], [1 / 3, 2 / 3])
                ter = []
                for lab, mm in (("low", q[feat].values < cut[0]), ("mid", (q[feat].values >= cut[0]) & (q[feat].values < cut[1])), ("high", q[feat].values >= cut[1])):
                    mm = mm & tm
                    ter.append({"t": lab, "n": int(mm.sum()), "feat": float(q[feat].values[mm].mean()), "pred": float(p0[mm].mean()), "act": float(yb[mm].mean())})
                out[feat] = {"b2": float(w[2]), "b1": float(w[1]), "brier_model": s0["brier"], "brier_with": s1["brier"], "skill_model": s0["skill"], "skill_with": s1["skill"], "terciles": ter}
            res[mk] = out
        rep["groups"][pos] = res
    (out_dir / "onice.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "onice.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r):
    L = [f"# On-ice chances vs the model: fit on {r['fit_seasons']}, tested on the rest of {r['seasons']}", ""]
    for pos, g in r["groups"].items():
        L += [f"## {'Defencemen' if pos == 'D' else 'Forwards'} ({g['n_test']:,} test player-games)"]
        for mk in ("Assist", "Point"):
            for feat in ("env", "rel"):
                x = g[mk][feat]
                L.append(f"- {mk}, {feat}: b2 {x['b2']:+.3f} (b1 {x['b1']:.3f}) · skill {x['skill_model']*100:+.2f}% -> {x['skill_with']*100:+.2f}% · "
                         + " · ".join(f"{t['t']} {t['feat']:.2f}: pred {t['pred']:.1%} act {t['act']:.1%}" for t in x["terciles"]))
        L.append("")
    return "\n".join(L) + "\n"
