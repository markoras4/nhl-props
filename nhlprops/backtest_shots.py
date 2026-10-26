"""Team shots on goal and goalie saves: point-in-time backtest (does not touch the website).

For every game, using only information available before it:
  * each team's shots for / against per game this season, shrunk toward last season (itself pulled toward the
    league average), and the league level so far
  * expected shots for a team = its shot rate x the opponent's shots-allowed rate / league rate x home/away
  * optional score-effects term: the team expected to trail shoots a little more
  * starting goalie's expected saves = opponent's expected shots x the share of the game a starter usually plays
    x (1 - expected goals per shot for that goalie)
Probabilities for the usual lines from Poisson / negative binomial, dispersion chosen by log loss.
Compared with what happened and with the simple averages most bettors use (team's season average; goalie's last 10).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import season_boxscores
from .grade import summarize
from .model import _num, _sit

TEAM_LINES = [25.5, 27.5, 29.5, 31.5, 33.5]
TOTAL_LINES = [55.5, 57.5, 59.5, 61.5, 63.5]
SAVE_LINES = [22.5, 24.5, 26.5, 28.5, 30.5]
DISP = [None, 200, 100, 60, 40, 25, 15]


def shrink(x, n, prior, k):
    return (x + k * prior) / (n + k)


def p_over(lam, line, r):
    if lam <= 0:
        return 0.0
    k_max = int(math.floor(line))
    if r is None:
        # Poisson via log terms (large means)
        lt = -lam; cdf = math.exp(lt)
        for k in range(1, k_max + 1):
            lt += math.log(lam / k); cdf += math.exp(lt)
        return max(0.0, 1 - cdf)
    p = r / (r + lam)
    lt = r * math.log(p); cdf = math.exp(lt)
    for k in range(1, k_max + 1):
        lt += math.log((k + r - 1) / k * (1 - p)); cdf += math.exp(lt)
    return max(0.0, 1 - cdf)


def team_prior(src, season):
    d = src.moneypuck("teams", season - 1, False)
    if d is None:
        return {}
    a = d[_sit(d) == "all"]
    gp = _num(a, "games_played").clip(lower=1)
    return {t: (float(f / g), float(s / g)) for t, f, s, g in
            zip(a.team.astype(str), _num(a, "shotsOnGoalFor"), _num(a, "shotsOnGoalAgainst"), gp)}


def season_rows(src, season, cfg, log=print):
    games, sk, go = season_boxscores(src, season, cfg["workers"], log)
    tp = team_prior(src, season)
    pf = src.moneypuck("goalies", season - 1, False)
    gratio = {}
    if pf is not None:
        a = pf[_sit(pf) == "all"]
        ga, xga = _num(a, "goals"), _num(a, "xGoals")
        lr = float(ga.sum() / xga.sum()) if xga.sum() else 1.0
        for pid, g_, x_ in zip(pd.to_numeric(a.playerId, errors="coerce"), ga, xga):
            gratio[int(pid)] = shrink(g_, x_, lr, 40.0) / lr
    go = go.copy()
    go["sa"] = pd.to_numeric(go["sa"], errors="coerce")
    go["sv"] = pd.to_numeric(go["sv"], errors="coerce")
    starters = go[go.starter].drop_duplicates(["gid", "team"]).set_index(["gid", "team"])
    lg_prior = float(np.mean([v[0] for v in tp.values()])) if tp else 29.0
    hist: dict[str, list] = {}
    ghist: dict[int, list] = {}            # goalie -> list of (saves, shots faced, goals, full game)
    lg_s, lg_n, lg_g, lg_sh = 0.0, 0, 0.0, 0.0
    share_s, share_n = 0.0, 0              # starters' share of team shots against
    rows = []
    for g in games.sort_values(["date", "id"]).itertuples():
        if g.hsog is None or g.asog is None or pd.isna(g.hsog) or pd.isna(g.asog):
            continue
        H, A = g.home, g.away
        lg = shrink(lg_s, lg_n, lg_prior, 200)               # shots per team-game so far
        gps = shrink(lg_g, lg_sh, 0.095, 3000)                # goals per shot so far
        play = shrink(share_s, share_n, 0.965, 100)

        def rating(t, k):
            h = hist.get(t, [])
            pr = tp.get(t, (lg, lg))
            pf_, pa_ = 0.7 * pr[0] + 0.3 * lg_prior, 0.7 * pr[1] + 0.3 * lg_prior
            sc = lg / lg_prior
            return (shrink(sum(x[0] for x in h), len(h), pf_ * sc, k), shrink(sum(x[1] for x in h), len(h), pa_ * sc, k), h)
        out = {"season": season, "id": g.id, "date": g.date, "home": H, "away": A, "hsog": int(g.hsog), "asog": int(g.asog)}
        for k in (8, 15, 25):
            fh, ah_, hh = rating(H, k); fa, aa, ha = rating(A, k)
            out[f"eh_k{k}"] = fh * aa / lg * 1.02
            out[f"ea_k{k}"] = fa * ah_ / lg / 1.02
        hh, ha = hist.get(H, []), hist.get(A, [])
        out["gp_min"] = min(len(hh), len(ha))
        # naive: average of own shots-for and opponent shots-against this season
        out["nh"] = (np.mean([x[0] for x in hh]) + np.mean([x[1] for x in ha])) / 2 if hh and ha else np.nan
        out["na"] = (np.mean([x[0] for x in ha]) + np.mean([x[1] for x in hh])) / 2 if hh and ha else np.nan
        out["nh_own"] = np.mean([x[0] for x in hh]) if hh else np.nan
        # goals per game proxies for score effects (from shots and league finishing)
        for side, opp, eo in (("h", A, "ea_k15"), ("a", H, "eh_k15")):
            team = H if side == "h" else A
            if (g.id, team) in starters.index:
                st = starters.loc[(g.id, team)]
                pid = int(st.pid)
                gh = ghist.get(pid, [])
                out[f"g{side}_pid"] = pid
                out[f"g{side}_sv"] = st.sv if not pd.isna(st.sv) else np.nan
                out[f"g{side}_sa"] = st.sa if not pd.isna(st.sa) else np.nan
                gr = gratio.get(pid, 1.0)
                if gh:   # this season: his goals allowed vs league-average goals on the shots he faced, shrunk (300 shots)
                    gr = (sum(x[2] for x in gh) + 300 * gps * gr) / (sum(x[1] for x in gh) * gps + 300 * gps)
                out[f"g{side}_exp"] = out[eo] * play * (1 - gps * gr)
                l10 = [x[0] for x in gh[-10:]]
                out[f"g{side}_l10"] = float(np.mean(l10)) if len(l10) >= 5 else np.nan
                out[f"g{side}_l10hits"] = json.dumps(l10)
        rows.append(out)
        # update
        hist.setdefault(H, []).append((g.hsog, g.asog)); hist.setdefault(A, []).append((g.asog, g.hsog))
        lg_s += g.hsog + g.asog; lg_n += 2
        lg_g += g.hs + g.as_; lg_sh += g.hsog + g.asog
        for team, opp_sog in ((H, g.asog), (A, g.hsog)):
            if (g.id, team) in starters.index:
                st = starters.loc[(g.id, team)]
                if not pd.isna(st.sa) and opp_sog:
                    share_s += min(1.0, st.sa / opp_sog); share_n += 1
                    ghist.setdefault(int(st.pid), []).append((st.sv if not pd.isna(st.sv) else st.sa - st.ga, st.sa, st.ga, st.toi >= 55))
    return pd.DataFrame(rows)


def _best_r(lam, y, lines):
    best, res = None, []
    for r in DISP:
        ll = 0.0
        for L in lines:
            yy = (y > L).astype(int)
            p = np.clip([p_over(x, L, r) for x in lam], 1e-4, 1 - 1e-4)
            ll += float(-(yy * np.log(p) + (1 - yy) * np.log(1 - p)).mean())
        res.append({"r": r, "logloss": ll / len(lines)})
        if best is None or ll < best[1]:
            best = (r, ll)
    return best[0], res


def _lines(lam, y, lines, r, naive=None):
    out = []
    for L in lines:
        yy = (y > L).astype(int)
        pm = np.clip([p_over(x, L, r) for x in lam], 1e-4, 1 - 1e-4)
        row = {"line": L, "base": float(yy.mean()), "model": summarize(list(zip(pm, yy)))}
        if naive is not None:
            ok = ~np.isnan(naive)
            pn = np.clip([p_over(x, L, r) for x in naive[ok]], 1e-4, 1 - 1e-4)
            row["naive"] = summarize(list(zip(pn, yy[ok])))
            row["model_same"] = summarize(list(zip(pm[ok], yy[ok])))
        out.append(row)
    return out


def evaluate(d: pd.DataFrame) -> dict:
    d = d[d.gp_min >= 5].copy()
    rep = {"games": int(len(d))}
    # team shots: stack home and away rows
    ys = np.concatenate([d.hsog.values, d.asog.values]).astype(float)
    rep["team_mean_actual"] = float(ys.mean())
    ks = {}
    for k in (8, 15, 25):
        lam = np.concatenate([d[f"eh_k{k}"].values, d[f"ea_k{k}"].values])
        ks[k] = float(((lam - ys) ** 2).mean())
    k_best = min(ks, key=ks.get)
    rep["team_k_mse"] = ks; rep["team_k"] = k_best
    lam = np.concatenate([d[f"eh_k{k_best}"].values, d[f"ea_k{k_best}"].values])
    naive = np.concatenate([d.nh.values, d.na.values]).astype(float)
    own = np.concatenate([d.nh_own.values, d.na.values * np.nan]).astype(float)
    rep["team_mean_pred"] = float(lam.mean()); rep["team_corr"] = float(np.corrcoef(lam, ys)[0, 1])
    ok = ~np.isnan(naive)
    rep["team_corr_naive"] = float(np.corrcoef(naive[ok], ys[ok])[0, 1])
    rep["team_mae"] = float(np.abs(lam - ys).mean()); rep["team_mae_naive"] = float(np.abs(naive[ok] - ys[ok]).mean())
    r, res = _best_r(lam, ys, TEAM_LINES)
    rep["team_r"], rep["team_disp"] = r, res
    rep["team_lines"] = _lines(lam, ys, TEAM_LINES, r, naive)
    # home advantage check
    rep["home_ratio_actual"] = float(d.hsog.mean() / d.asog.mean())
    # totals
    yt = (d.hsog + d.asog).values.astype(float)
    lt = (d[f"eh_k{k_best}"] + d[f"ea_k{k_best}"]).values
    nt = (d.nh + d.na).values.astype(float)
    rep["total_mean_actual"] = float(yt.mean()); rep["total_mean_pred"] = float(lt.mean())
    rep["total_corr"] = float(np.corrcoef(lt, yt)[0, 1])
    rt, rest = _best_r(lt, yt, TOTAL_LINES)
    rep["total_r"], rep["total_disp"] = rt, rest
    rep["total_lines"] = _lines(lt, yt, TOTAL_LINES, rt, nt)
    # saves: stack both starters
    sv = pd.concat([d[["gh_sv", "gh_exp", "gh_l10"]].set_axis(["sv", "exp", "l10"], axis=1),
                    d[["ga_sv", "ga_exp", "ga_l10"]].set_axis(["sv", "exp", "l10"], axis=1)]).dropna(subset=["sv", "exp"])
    y = sv.sv.values.astype(float); lam = sv.exp.values; l10 = sv.l10.values.astype(float)
    rep["saves_n"] = int(len(y)); rep["saves_mean_actual"] = float(y.mean()); rep["saves_mean_pred"] = float(lam.mean())
    rep["saves_corr"] = float(np.corrcoef(lam, y)[0, 1])
    ok = ~np.isnan(l10)
    rep["saves_corr_l10"] = float(np.corrcoef(l10[ok], y[ok])[0, 1])
    rs, ress = _best_r(lam, y, SAVE_LINES)
    rep["saves_r"], rep["saves_disp"] = rs, ress
    rep["saves_lines"] = _lines(lam, y, SAVE_LINES, rs, l10)
    rep["saves_pulled_share"] = float((sv.sv < 15).mean())
    return rep


def run(src, seasons, cfg, out_dir: Path, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for y in sorted(seasons):
        frames.append(season_rows(src, y, cfg, log))
    rep = {"seasons": seasons, **evaluate(pd.concat(frames))}
    (out_dir / "shots_saves.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    md = to_md(rep)
    (out_dir / "shots_saves.md").write_text(md, encoding="utf-8")
    log(md)
    return rep


def to_md(r: dict) -> str:
    def disp(x):
        return " · ".join(f"{'P' if d['r'] is None else d['r']}: {d['logloss']:.4f}" for d in x)

    def table(rows, name):
        L = [f"| {name} | Base | Model Brier | Model skill | Simple avg skill | Model skill (same rows) |", "|---|---|---|---|---|---|"]
        for x in rows:
            nv = x.get("naive"); ms = x.get("model_same")
            L.append(f"| over {x['line']} | {x['base']:.1%} | {x['model']['brier']:.4f} | {x['model']['skill'] * 100:+.1f}% | "
                     f"{(nv['skill'] * 100 if nv else float('nan')):+.1f}% | {(ms['skill'] * 100 if ms else float('nan')):+.1f}% |")
        return L
    L = [f"# Team shots & goalie saves backtest: {', '.join(f'{s}-{s + 1}' for s in r['seasons'])}", f"Games: {r['games']:,}", "",
         "## Team shots on goal",
         f"Mean predicted {r['team_mean_pred']:.2f} · actual {r['team_mean_actual']:.2f} · corr {r['team_corr']:.3f} (simple avg {r['team_corr_naive']:.3f}) · "
         f"MAE {r['team_mae']:.2f} (simple {r['team_mae_naive']:.2f}) · best k={r['team_k']} {r['team_k_mse']} · home/away actual {r['home_ratio_actual']:.3f}",
         f"Spread: best {r['team_r']} ({disp(r['team_disp'])})", ""] + table(r["team_lines"], "Team") + ["",
         "## Total shots on goal",
         f"Mean predicted {r['total_mean_pred']:.2f} · actual {r['total_mean_actual']:.2f} · corr {r['total_corr']:.3f}",
         f"Spread: best {r['total_r']} ({disp(r['total_disp'])})", ""] + table(r["total_lines"], "Total") + ["",
         "## Starting goalie saves",
         f"Starts: {r['saves_n']:,} · mean predicted {r['saves_mean_pred']:.2f} · actual {r['saves_mean_actual']:.2f} · corr {r['saves_corr']:.3f} (last-10 avg {r['saves_corr_l10']:.3f}) · "
         f"starts under 15 saves {r['saves_pulled_share']:.1%}",
         f"Spread: best {r['saves_r']} ({disp(r['saves_disp'])})", ""] + table(r["saves_lines"], "Saves")
    return "\n".join(L) + "\n"
