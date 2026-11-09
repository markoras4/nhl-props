"""Free / Pro data split (see PAYWALL.md).

The app computes every player probability in the browser from the numbers embedded in the page, so hiding
Pro features with CSS is not enough: anyone could read the page source. Instead the build writes two things:

* the PUBLIC payload, embedded in app/index.html: everything Free and visitors may see. Locked players keep
  their name, team and opponent but lose the model inputs (base rates, factors, precomputed chances), and Pro
  markets (hits, blocks, multi-point, PP points, penalties, faceoffs, first goalscorer) are removed from the
  open players too.
* the PRO patch, a separate JSON file that is never published to the public site. The workflow uploads it to a
  private Supabase Storage bucket, and the `pro-data` edge function hands it only to signed-in users whose
  profiles.plan is pro, creator or media. The app merges it into DATA after login.

Plans:  visitor (no account) < free (account) < pro = creator = media (creator/media are Pro plus a content licence).
"""
from __future__ import annotations

import copy
import datetime as dt
import re

PRO_PLANS = ("pro", "creator", "media")

# model inputs on a prop row that free users keep for the two open players per team
FREE_BASE = ("g", "a", "s")
# precomputed chances (pr.p) that stay for open players: goals, assists, points, shots
FREE_P = re.compile(r"^(g|a|pt|s\d.*)$")
# player sportsbook markets free users may see for open players (multi-point, PP, hits, blocks... are Pro)
FREE_ODDS = FREE_P


def _is_fwd(pos: str | None) -> bool:
    return (pos or "").upper() not in ("D", "G")


def open_players(slate: dict) -> set[str]:
    """1 forward + 1 defenceman per team and game: the one with the biggest projected ice time.
    Chosen at build time so free users, the server and the Pro patch all agree on who is open."""
    players = slate.get("players") or {}
    out: set[str] = set()
    for g in slate.get("games") or []:
        for team in (g.get("home"), g.get("away")):
            rows = [pr for pr in g.get("props") or [] if pr.get("team") == team]
            for want_fwd in (True, False):
                best, best_toi = None, -1.0
                for pr in rows:
                    p = players.get(str(pr.get("id"))) or {}
                    if not p.get("active", True) or _is_fwd(p.get("pos")) != want_fwd:
                        continue
                    toi = p.get("toi_proj") or 0
                    if toi > best_toi:
                        best, best_toi = str(pr["id"]), toi
                if best:
                    out.add(best)
    return out


def _lock_prop(pr: dict) -> dict:
    """a prop row with the model removed: the app shows the player, blurred, with an 'Unlock with Pro' lock"""
    return {k: pr[k] for k in ("id", "team", "opp", "home") if k in pr} | {"base": {}, "locked": 1}


def _free_prop(pr: dict) -> dict:
    q = copy.deepcopy(pr)
    q["base"] = {k: v for k, v in (pr.get("base") or {}).items() if k in FREE_BASE}
    q["p"] = {k: v for k, v in (pr.get("p") or {}).items() if FREE_P.match(k)}
    q["open"] = 1
    return q


def _is_prop_bullet(s: str) -> bool:
    return bool(re.match(r"^\s*(Player chances|Players?:|Spelarchanser|Spelare:)", s or "", re.I))


def strip_preview(pv: dict | None) -> dict | None:
    """the player-prop bullet in a written preview is Pro: replaced by a lock marker"""
    if not pv:
        return pv
    pv = copy.deepcopy(pv)
    for lang in ("en", "sv"):
        x = pv.get(lang)
        if isinstance(x, dict) and isinstance(x.get("betting"), list):
            x["betting"] = ["\u0000LOCK" if _is_prop_bullet(b) else b for b in x["betting"]]
    return pv


def split(payload: dict) -> tuple[dict, dict]:
    """payload (what render() used to embed) -> (public payload, pro patch)"""
    opened = open_players(payload)
    pub = dict(payload)
    pro: dict = {"date": payload.get("date"), "built": payload.get("built"), "props": {}, "previews": {}}

    games = []
    for g in payload.get("games") or []:
        pro["props"][str(g["id"])] = g.get("props") or []
        g2 = dict(g)
        g2["props"] = [(_free_prop(pr) if str(pr.get("id")) in opened else _lock_prop(pr)) for pr in g.get("props") or []]
        games.append(g2)
    pub["games"] = games
    pub["open_ids"] = sorted(opened)

    # sportsbook player prices: only the open players' free markets; no opening prices (odds movement) and no
    # sharp-book second price (p / pn); public betting splits are Pro
    odds_pub = {}
    for gid, o in (payload.get("odds") or {}).items():
        o2 = {k: v for k, v in o.items() if k not in ("players", "open", "public")}
        o2["players"] = {pid: {m: {k: v for k, v in x.items() if k in ("o", "n", "b")} for m, x in mk.items() if FREE_ODDS.match(m)}
                         for pid, mk in (o.get("players") or {}).items() if pid in opened}
        odds_pub[gid] = o2
    pub["odds"] = odds_pub
    pro["odds"] = payload.get("odds") or {}

    # futures: chances are free, fair odds / book odds / value are Pro
    if payload.get("futures"):
        fu = dict(payload["futures"])
        pro["futures_odds"] = fu.pop("odds", None)
        # what's at stake: free sees the level only (now / with a win / with a loss are Pro)
        if isinstance(fu.get("games"), dict):
            pro["futures_games"] = fu["games"]
            fu["games"] = {gid: {tm: ({"level": x.get("level")} if isinstance(x, dict) and not tm.startswith("_") else x)
                                 for tm, x in (gm or {}).items()} for gid, gm in fu["games"].items()}
        pub["futures"] = fu
    # awards: model chances free; book prices Pro (rows are [name, book_odds, pid, chance])
    if payload.get("awards"):
        aw = copy.deepcopy(payload["awards"])
        pro["awards"] = payload["awards"]
        for k, rows in (aw.get("m") or {}).items():
            # [name, book odds, pid, book chance (no margin), ...]: both book numbers are Pro
            aw["m"][k] = [[r[0], None, r[2] if len(r) > 2 else None, None, *r[4:]] if isinstance(r, list) and len(r) > 1 else r for r in rows]
        pub["awards"] = aw
    # line & pair rankings across the league: Pro
    if "league_lines" in payload:
        pro["league_lines"] = payload["league_lines"]
        pub["league_lines"] = {"min": (payload["league_lines"] or {}).get("min"), "rows": [], "locked": 1}
    # previews: the player-prop bullet
    if payload.get("previews"):
        pro["previews"] = payload["previews"]
        pub["previews"] = {gid: strip_preview(pv) for gid, pv in payload["previews"].items()}
    # hit rates and hits / blocks history stay public: they are counts from the public game logs (data/gl/)
    pro["players"] = {}
    # season-long player chances are Pro: milestone chances (m), Art Ross / Rocket Richard / D / rookie leaders
    # (ar, rr, dl, rl, am) and the goalie "most saved goals" chance (vz). Projected totals and ranges stay free.
    PJ_PRO = ("m", "ar", "rr", "dl", "rl", "am")
    if payload.get("proj"):
        pro["proj"] = {pid: {k: pj[k] for k in PJ_PRO if k in pj} for pid, pj in payload["proj"].items()}
        pub["proj"] = {pid: {k: v for k, v in pj.items() if k not in PJ_PRO} for pid, pj in payload["proj"].items()}
    if payload.get("gproj"):
        pro["gproj"] = {pid: {"vz": gp["vz"]} for pid, gp in payload["gproj"].items() if "vz" in gp}
        pub["gproj"] = {pid: {k: v for k, v in gp.items() if k != "vz"} for pid, gp in payload["gproj"].items()}
    pub["paywall"] = {"pro_plans": list(PRO_PLANS), "free_watch": 3, "free_legs": 2}
    return pub, pro


def strip_feed_rows(rows: list, now: dt.datetime | None = None) -> list:
    """public track-record files must not show picks for games that haven't locked yet (live picks are Pro)"""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    out = []
    for r in rows or []:
        if r.get("r") in (None, "", "v") and str(r.get("d", "")) >= today:
            continue
        out.append(r)
    return out


def strip_archive(arch: dict) -> tuple[dict, dict]:
    """archive day (as it looked before puck drop): free sees the games list, scores and results only"""
    pub = {k: v for k, v in arch.items() if k not in ("odds", "futures", "awards", "league_lines")}
    pub["odds"] = {}
    pub["games"] = [{**g, "props": [_lock_prop(pr) for pr in g.get("props") or []]} for g in arch.get("games") or []]
    pub["locked"] = 1
    return pub, arch
