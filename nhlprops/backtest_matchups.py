"""Line matchups: which opposing defence pairs do forwards face at 5-on-5, and does it change their shots or points
beyond what the model already uses? (does not touch the website)

From the NHL shift charts (who was on the ice, second by second) for every 5-on-5 stretch of every game:
  1. Exposure: each forward's share of his 5-on-5 time against the opponent's pair 1 / 2 / 3 (ranked by 5-on-5 ice time
     in that game), compared with what pure chance would give (each pair's share of the opponent's defence minutes).
     Split by home/away (the home coach has the last change) and by the forward's line.
  2. Quality of the defence he faced: the opposing defencemen's shots-against rate at 5-on-5 last season (MoneyPuck),
     weighted by the seconds he faced each of them, relative to the opponent's average defenceman that night.
     How much does facing tougher or softer defence than average move his shots, goals and points vs the model?
     (an upper bound: this uses who he actually faced, which isn't known before the game)
  3. Can it be known before the game? His own matchup pattern so far this season (shrunk toward zero) as a pre-game
     prediction: is it persistent, and does adding it improve the model on later seasons?
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores, prior_frames, predict
from .grade import summarize
from .model import _hours, _num, _sit

SHIFTS = "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={gid}"


def _sec(t) -> int | None:
    try:
        m, s = str(t).split(":")
        return int(m) * 60 + int(s)
    except Exception:
        return None


def game_matrix(rows: list[dict], pos: dict[int, str], goalies: set[int], team_of: dict[int, str]) -> dict | None:
    """-> {'fd': {f: {d: sec}}, 't5': {pid: sec}} over 5-on-5 time (5 skaters + a goalie on each side)."""
    by_per: dict[int, list] = {}
    for r in rows:
        if r.get("typeCode") not in (None, 517):
            continue
        a, b, pid = _sec(r.get("startTime")), _sec(r.get("endTime")), r.get("playerId")
        if a is None or b is None or b <= a or not pid:
            continue
        by_per.setdefault(int(r.get("period") or 0), []).append((a, b, int(pid)))
    if not by_per:
        return None
    fd: dict[int, dict[int, float]] = {}
    t5: dict[int, float] = {}
    for per, sh in by_per.items():
        if per > 3:
            continue
        ev = sorted([(a, 1, p) for a, b, p in sh] + [(b, 0, p) for a, b, p in sh])
        on: dict[int, int] = {}
        last = None
        for t, kind, p in ev:
            if last is not None and t > last and on:
                dt = t - last
                teams: dict[str, dict] = {}
                for q in on:
                    tm = team_of.get(q)
                    if tm is None:
                        continue
                    x = teams.setdefault(tm, {"g": 0, "sk": [], })
                    if q in goalies:
                        x["g"] += 1
                    else:
                        x["sk"].append(q)
                if len(teams) == 2 and all(x["g"] == 1 and len(x["sk"]) == 5 for x in teams.values()):
                    (ta, A), (tb, B) = teams.items()
                    for mine, theirs in ((A, B), (B, A)):
                        ds = [q for q in theirs["sk"] if pos.get(q) == "D"]
                        for q in mine["sk"]:
                            t5[q] = t5.get(q, 0) + dt
                            if pos.get(q) == "F":
                                row = fd.setdefault(q, {})
                                for d in ds:
                                    row[d] = row.get(d, 0) + dt
            if kind == 1:
                on[p] = on.get(p, 0) + 1
            else:
                on[p] = on.get(p, 1) - 1
                if on[p] <= 0:
                    on.pop(p, None)
            last = t
    return {"fd": {str(k): {str(d): v for d, v in r.items()} for k, r in fd.items()}, "t5": {str(k): v for k, v in t5.items()}}


def collect(src, season: int, sk: pd.DataFrame, go: pd.DataFrame, store: Path, workers: int, log=print) -> dict:
    store.mkdir(parents=True, exist_ok=True)
    f = store / f"shift5_{season}.json"
    have = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    gids = sorted(sk.gid.unique().tolist())
    todo = [g for g in gids if str(g) not in have]
    log(f"  {season}: shift charts for {len(todo)} new games ({len(have)} stored)")
    pos_by = {g: dict(zip(d.pid, d.posg)) for g, d in sk.groupby("gid")}
    team_by = {g: dict(zip(d.pid, d.team)) for g, d in pd.concat([sk[["gid", "pid", "team"]], go[["gid", "pid", "team"]]]).groupby("gid")}
    gk_by = {g: set(d.pid) for g, d in go.groupby("gid")}

    def one(gid):
        raw = src._download(SHIFTS.format(gid=gid))
        try:
            rows = (json.loads(raw) or {}).get("data") or [] if raw else []
            return gid, game_matrix(rows, pos_by.get(gid, {}), gk_by.get(gid, set()), team_by.get(gid, {}))
        except Exception:
            return gid, None
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for gid, rec in ex.map(one, todo):
            have[str(gid)] = rec or {}
            done += 1
            if done % 400 == 0:
                f.write_text(json.dumps(have, separators=(",", ":")), encoding="utf-8")
    f.write_text(json.dumps(have, separators=(",", ":")), encoding="utf-8")
    ok = sum(1 for v in have.values() if v)
    log(f"  {season}: {ok} games with 5-on-5 shift data")
    return have


def d_quality(src, season: int) -> tuple[dict, float]:
    """Last season's 5-on-5 shots against per 60 while each defenceman was on the ice, shrunk toward the league average."""
    d = src.moneypuck("skaters", season - 1, False)
    if d is None or "OnIce_A_shotsOnGoal" not in d.columns:
        return {}, 1.0
    d = d[(_sit(d) == "5on5") & (d.get("position", pd.Series("", index=d.index)).astype(str) == "D")]
    h, sa = _hours(d, "icetime"), _num(d, "OnIce_A_shotsOnGoal")
    lg = float(sa.sum() / max(h.sum(), 1e-9))
    k = 5.0   # hours of prior
    rate = (sa + k * lg) / (h + k)
    return dict(zip(pd.to_numeric(d.playerId, errors="coerce").astype("Int64").astype(str), (rate / lg).astype(float))), lg


def features(sh: dict, sk: pd.DataFrame, dq: dict) -> pd.DataFrame:
    rows = []
    for gid, g in sk.groupby("gid"):
        m = sh.get(str(gid)) or {}
        if not m.get("fd"):
            continue
        t5 = {int(k): v for k, v in m["t5"].items()}
        for team, tg in g.groupby("team"):
            opp_d = g[(g.team != team) & (g.posg == "D")].pid.tolist()
            od = sorted([(t5.get(p, 0), p) for p in opp_d], reverse=True)
            tot_d = sum(x for x, _ in od)
            if tot_d <= 0:
                continue
            rank = {p: (1 if i < 2 else 2 if i < 4 else 3) for i, (_, p) in enumerate(od)}
            exp = {k: sum(x for x, p in od if rank[p] == k) / tot_d for k in (1, 2, 3)}
            q_base = sum(x * dq.get(str(p), 1.0) for x, p in od) / tot_d
            fw = tg[tg.posg == "F"]
            f5 = sorted([(t5.get(p, 0), p) for p in fw.pid], reverse=True)
            line = {p: min(4, i // 3 + 1) for i, (_, p) in enumerate(f5)}
            for p in fw.pid:
                row = m["fd"].get(str(p)) or {}
                tot = sum(row.values())
                if tot < 120:   # under 2 minutes of 5-on-5 against defencemen: skip
                    continue
                share = {k: sum(v for d, v in row.items() if rank.get(int(d)) == k) / tot for k in (1, 2, 3)}
                q = sum(v * dq.get(d, 1.0) for d, v in row.items()) / tot
                rows.append({"gid": gid, "pid": p, "team": team, "line": line.get(p, 4), "t5": t5.get(p, 0) / 60,
                             "s1": share[1], "s2": share[2], "s3": share[3], "e1": exp[1], "e2": exp[2], "e3": exp[3],
                             "xq": math.log(max(q, 1e-6) / max(q_base, 1e-6))})
    return pd.DataFrame(rows)


def fit_beta(y, lam, x, w=None, iters=30):
    """One-parameter Poisson fit: y ~ Poisson(lam * exp(b x)). Returns b and its standard error."""
    y, lam, x = np.asarray(y, float), np.asarray(lam, float), np.asarray(x, float)
    b = 0.0
    for _ in range(iters):
        mu = lam * np.exp(b * x)
        g, h = float(((y - mu) * x).sum()), float((mu * x * x).sum())
        if h <= 0:
            break
        step = g / h
        b += step
        if abs(step) < 1e-8:
            break
    mu = lam * np.exp(b * x)
    return b, 1 / math.sqrt(max(float((mu * x * x).sum()), 1e-12))


def p_over(lam, line):
    lam = np.asarray(lam, float)
    k_max = int(math.floor(line))
    term, cdf = np.exp(-lam), np.zeros_like(lam)
    for i in range(k_max + 1):
        if i:
            term = term * lam / i
        cdf = cdf + term
    return np.clip(1 - cdf, 1e-4, 1 - 1e-4)


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    store = Path(src.cache) / "derived"
    frames = []
    for season in sorted(seasons):
        games, sk, go = season_boxscores(src, season, cfg["workers"], log)
        if sk.empty:
            continue
        sh = collect(src, season, sk, go, store, cfg["workers"], log)
        dq, _ = d_quality(src, season)
        ft = features(sh, sk, dq)
        pri = prior_frames(src, season)
        pr = predict(sk, pri, games, go, season, cfg, prior_beta=cfg.get("prior_toi_beta", 1.0))
        df = pr.merge(ft, on=["gid", "pid", "team"], how="inner")
        df["season"] = season
        # pre-game: his own pattern in earlier games this season, shrunk toward zero (k = 5 games)
        df = df.sort_values(["pid", "date"])
        for c in ("xq", "s1"):
            x = df[c] - (df["e1"] if c == "s1" else 0)
            df[c + "_ex"] = x
            cs = df.groupby("pid")[c + "_ex"].cumsum() - df[c + "_ex"]
            n = df.groupby("pid").cumcount()
            df[c + "_pre"] = cs / (n + 5)
        frames.append(df)
        log(f"  {season}: {len(df):,} forward-games with matchup data")
    d = pd.concat(frames, ignore_index=True)
    d = d[d.lam_s.notna() & d.lam_g.notna()]
    d["home"] = d["home"].astype(bool)

    # 1. exposure vs pure chance
    expo = []
    for (ln, hm), g in d.groupby(["line", "home"]):
        expo.append({"line": int(ln), "home": bool(hm), "n": int(len(g)),
                     "p1": float(g.s1.mean()), "p1_exp": float(g.e1.mean()), "p2": float(g.s2.mean()), "p2_exp": float(g.e2.mean()),
                     "p3": float(g.s3.mean()), "p3_exp": float(g.e3.mean())})
    spread = {"sd_s1_excess": float((d.s1 - d.e1).std()), "sd_xq": float(d.xq.std())}

    # 2. effect of the defence actually faced (only the 5-on-5 part of his game is exposed to it)
    frac = (d.t5 / d.toi.clip(lower=1)).clip(0, 1)
    pts = d.g + d.a
    eff = {}
    for name, y, lam in (("shots", d.s, d.lam_s), ("goals", d.g, d.lam_g), ("points", pts, d.lam_g + d.lam_a)):
        b, se = fit_beta(y, lam, d.xq * frac)
        b1, se1 = fit_beta(y, lam, (d.s1 - d.e1) * frac)
        eff[name] = {"beta_quality": b, "se_quality": se, "beta_top_pair": b1, "se_top_pair": se1,
                     # what a typical (1 sd) tougher/softer night does to his expected count
                     "one_sd_quality_pct": (math.exp(b * float((d.xq * frac).std())) - 1) * 100,
                     "one_sd_top_pair_pct": (math.exp(b1 * float(((d.s1 - d.e1) * frac).std())) - 1) * 100}

    # 3. predictable before the game?
    pers = {"corr_quality": float(np.corrcoef(d.xq_pre, d.xq)[0, 1]), "corr_top_pair": float(np.corrcoef(d.s1_pre, d.s1 - d.e1)[0, 1])}
    half = sorted(seasons)[: max(1, len(seasons) - 1)]
    fit, test = d[d.season.isin(half)], d[~d.season.isin(half)]
    if test.empty:
        fit = test = d
    ffr = 0.8   # typical 5-on-5 share of a forward's ice time (his real share tonight isn't known before the game)
    b_s, _ = fit_beta(fit.s, fit.lam_s, fit.xq_pre * ffr)
    b_g, _ = fit_beta(fit.g, fit.lam_g, fit.xq_pre * ffr)
    b_p, _ = fit_beta(fit.g + fit.a, fit.lam_g + fit.lam_a, fit.xq_pre * ffr)
    tfr = 0.8
    adj = lambda b: np.exp(b * test.xq_pre * tfr)
    sc_now, sc_adj = {}, {}
    for m, p0, p1, y in (("Goal", 1 - np.exp(-test.lam_g), 1 - np.exp(-test.lam_g * adj(b_g)), test.g >= 1),
                         ("Point", 1 - np.exp(-(test.lam_g + test.lam_a)), 1 - np.exp(-(test.lam_g + test.lam_a) * adj(b_p)), (test.g + test.a) >= 1),
                         ("SOG 2+", p_over(test.lam_s, 1.5), p_over(test.lam_s * adj(b_s), 1.5), test.s >= 2),
                         ("SOG 3+", p_over(test.lam_s, 2.5), p_over(test.lam_s * adj(b_s), 2.5), test.s >= 3)):
        yy = np.asarray(y, float)
        sc_now[m] = summarize(list(zip(np.clip(np.asarray(p0, float), 1e-4, 1 - 1e-4), yy)))
        sc_adj[m] = summarize(list(zip(np.clip(np.asarray(p1, float), 1e-4, 1 - 1e-4), yy)))

    rep = {"seasons": seasons, "n": int(len(d)), "exposure": expo, "spread": spread, "effect": eff, "persistence": pers,
           "pre_betas": {"shots": b_s, "goals": b_g, "points": b_p}, "test_now": sc_now, "test_adj": sc_adj}
    (out_dir / "matchups.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    L = [f"# Line matchups: {seasons} ({rep['n']:,} forward-games with 5-on-5 shift data)", "",
         "## 1. Share of 5-on-5 time against the opponent's pair 1 / 2 / 3 (actual vs pure chance)", "",
         "| Line | Venue | n | vs pair 1 | chance | vs pair 2 | chance | vs pair 3 | chance |", "|---|---|---|---|---|---|---|---|---|"]
    for x in sorted(expo, key=lambda r: (r["line"], not r["home"])):
        L.append(f"| L{x['line']} | {'home' if x['home'] else 'away'} | {x['n']:,} | {x['p1']:.1%} | {x['p1_exp']:.1%} | {x['p2']:.1%} | {x['p2_exp']:.1%} | {x['p3']:.1%} | {x['p3_exp']:.1%} |")
    L += ["", f"Night-to-night spread: top-pair share vs chance sd {spread['sd_s1_excess']:.3f}; defence quality faced sd {spread['sd_xq']:.3f} (log ratio)", "",
          "## 2. Effect of the defence actually faced (upper bound: known only after the game)", "",
          "| Outcome | Quality beta (se) | 1 sd tougher/softer night | Top-pair beta (se) | 1 sd more top pair |", "|---|---|---|---|---|"]
    for k, v in eff.items():
        L.append(f"| {k} | {v['beta_quality']:+.2f} ({v['se_quality']:.2f}) | {v['one_sd_quality_pct']:+.1f}% | {v['beta_top_pair']:+.2f} ({v['se_top_pair']:.2f}) | {v['one_sd_top_pair_pct']:+.1f}% |")
    L += ["", "(quality = opposing defencemen's shots-against rate last season, relative to that night's average opponent defenceman; positive beta = softer defence -> more)", "",
          "## 3. Known before the game?", "",
          f"Persistence (his earlier games this season -> tonight): defence quality r = {pers['corr_quality']:.3f}, top-pair share r = {pers['corr_top_pair']:.3f}", "",
          f"Pre-game betas fitted on {half}: shots {b_s:+.2f}, goals {b_g:+.2f}, points {b_p:+.2f}", "",
          "| Test season(s) | Brier now | Skill now | Brier with matchups | Skill with matchups |", "|---|---|---|---|---|"]
    for m in sc_now:
        a, b = sc_now[m], sc_adj[m]
        L.append(f"| {m} | {a['brier']:.5f} | {a['skill'] * 100:+.2f}% | {b['brier']:.5f} | {b['skill'] * 100:+.2f}% |")
    md = "\n".join(L) + "\n"
    (out_dir / "matchups.md").write_text(md, encoding="utf-8")
    log(md)
    return rep
