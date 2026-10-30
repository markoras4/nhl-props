"""Triage: how solid is a value bet? (the website runs the same rules in JavaScript; keep the two in step)

For a price with positive EV, the edge is re-checked with two other views of the player in tonight's context:
his season average and his last 10 games (goals from his expected goals, so luck doesn't count twice). Cautions:
shooting clearly down (overs) or up (unders) this season vs his previous 3 seasons, a depth forward on scoring
markets, the opposing goalie not confirmed, the price drifting out since it opened, the sharp book disagreeing,
a short NHL history. Grade:
  S (solid)    both views also show value and no caution
  F (fragile)  neither view shows value, or 2+ cautions
  L (lean)     everything in between
"""
from __future__ import annotations

import math
import re

try:
    from .config import CONFIG as _C
    SOG_CAL = float(_C.get("sog_calibration", 1.0)) or 1.0
except Exception:  # noqa: BLE001
    SOG_CAL = 1.0
SCORING = {"g", "a", "pt", "pt2", "pt3", "g2", "g3", "a2"}


def _pois_le(lam: float, k: int) -> float:
    term, cdf = math.exp(-lam), 0.0
    for i in range(k + 1):
        if i:
            term *= lam / i
        cdf += term
    return cdf


def _p(m: str, lg: float, la: float, ls: float) -> float | None:
    if m == "g":
        return 1 - math.exp(-lg)
    if m == "a":
        return 1 - math.exp(-la)
    if m == "pt":
        return 1 - math.exp(-(lg + la))
    if m in ("pt2", "pt3", "g2", "g3", "a2"):
        lam = lg + la if m.startswith("pt") else (lg if m[0] == "g" else la)
        return 1 - _pois_le(lam, int(m[-1]) - 1)
    if m.startswith("s") and m[1:].replace(".", "").isdigit():
        return 1 - _pois_le(ls, int(math.floor(float(m[1:]))))
    return None


def ratios(pl: dict) -> dict:
    """his season and last-10 per-game rates vs the model's own rate (before tonight's context)"""
    r = pl.get("rates") or {}
    toi = pl.get("toi_proj") or 0
    out = {}
    if not toi:
        return out
    mod = {"g": r.get("g60", 0) * toi / 60, "a": r.get("a60", 0) * toi / 60, "s": r.get("s60", 0) * toi / 60}
    cur = (pl.get("windows") or {}).get("cur") or {}
    xc = (pl.get("xg_windows") or {}).get("cur") or {}
    if (cur.get("gp") or 0) >= 5:
        gp = cur["gp"]
        out["season"] = {"g": (xc.get("xg") or 0) / max(1, xc.get("gp") or gp), "a": cur.get("a", 0) / gp, "s": cur.get("s", 0) / gp}
    l10 = ((pl.get("form") or {}).get("games") or [])[:10]
    if len(l10) >= 5:
        n = len(l10)
        out["l10"] = {"g": sum(x.get("x") or 0 for x in l10) / n, "a": sum(x.get("a", 0) for x in l10) / n, "s": sum(x.get("s", 0) for x in l10) / n}
    return {k: {kk: (v[kk] / mod[kk] if mod[kk] > 0 else 1.0) for kk in v} for k, v in out.items()}


def usage_z(pl: dict) -> tuple[float, float]:
    w = pl.get("windows") or {}
    cur, base = w.get("cur") or {}, (w.get("prev3") or {}) if ((w.get("prev3") or {}).get("gp") or 0) >= 40 else (w.get("career") or {})
    gp = cur.get("gp") or 0
    if gp < 8 or not base.get("gp"):
        return 0.0, 0.0
    ps = base.get("s", 0) / base["gp"]
    zs = (cur.get("s", 0) - gp * ps) / math.sqrt(gp * ps) if ps > 0 else 0.0
    xw = pl.get("xg_windows") or {}
    xc, xb = xw.get("cur") or {}, xw.get("prev3") or {}
    zx = 0.0
    if (xb.get("gp") or 0) >= 40 and (xc.get("gp") or 0) >= 8:
        px = xb["xg"] / xb["gp"]
        zx = (xc["xg"] - xc["gp"] * px) / math.sqrt(xc["gp"] * px) if px > 0 else 0.0
    return zs, zx


def grade(pl: dict, pr: dict, m: str, od: float, side: str = "o", opp_goalie_status: str = "", book: dict | None = None,
          open_odds: float | None = None) -> dict:
    p = (pr.get("p") or {}).get(m)
    if not p or not od:
        return {}
    pm = p if side == "o" else 1 - p
    if pm * od - 1 <= 0:
        return {}
    pg, pa = (pr.get("p") or {}).get("g") or 0, (pr.get("p") or {}).get("a") or 0
    lg, la = -math.log(max(1e-9, 1 - pg)), -math.log(max(1e-9, 1 - pa))
    ls = (pr.get("base") or {}).get("s") or 0
    views = {}
    if m in SCORING or m.startswith("s"):
        for k, r in ratios(pl).items():
            # his raw shooting average already is what he does, so the model's own shots trim isn't applied twice
            q = _p(m, lg * r["g"], la * r["a"], ls * r["s"] / SOG_CAL)
            if q is not None:
                views[k] = round((q if side == "o" else 1 - q) * od - 1, 4)
    caut, info = [], []
    zs, zx = usage_z(pl)
    if m in SCORING or m.startswith("s"):
        if side == "o" and (zs <= -2 or zx <= -2):
            caut.append("down")
        if side == "n" and (zs >= 2 or zx >= 2):
            caut.append("up")
    if m in SCORING and side == "o" and pl.get("posg") == "F" and (pl.get("toi_proj") or 99) < 14:
        caut.append("depth")
    st = (opp_goalie_status or "").lower()
    pct = re.search(r"(\d+)%", st)
    likely = bool(pct) and int(pct.group(1)) >= 70
    if m in SCORING and ("confirmed" not in st or "unconfirmed" in st) and not likely:
        caut.append("goalie")
    if side == "o" and open_odds and od > open_odds * 1.05:
        caut.append("drift")
    b = book or {}
    if b.get("p") and b.get("pn"):
        sp = (1 / b["p"]) / (1 / b["p"] + 1 / b["pn"])
        if ((sp if side == "o" else 1 - sp) * od - 1) < 0:
            caut.append("sharp")
    if (((pl.get("windows") or {}).get("career") or {}).get("gp") or 0) < 50:
        caut.append("short")
    if od >= 6:
        info.append("long")
    z = (pl.get("regress") or {}).get("z")
    if m in SCORING and z is not None and z <= -1:
        info.append("unlucky")
    v = sum(1 for e in views.values() if e > 0)
    nv = len(views)
    if (nv and v == 0) or len(caut) >= 2:
        gr = "F"
    elif nv == 2 and v == 2 and not caut:
        gr = "S"
    else:
        gr = "L"
    return {"gr": gr, "views": views, "c": caut, "i": info}
