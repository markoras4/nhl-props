"""Data access: NHL API (api-web.nhle.com) and MoneyPuck season CSVs.

Everything is cached under data/cache so repeat runs are fast and past seasons
are downloaded only once.
"""
from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import pandas as pd
import requests

NHL = "https://api-web.nhle.com/v1"
MP = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{year}/regular/{kind}.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal NHL props model; non-commercial)",
    "Accept": "application/json,text/csv,*/*",
}

# MoneyPuck uses dotted codes for a few teams.
MP_TO_NHL = {"T.B": "TBL", "N.J": "NJD", "L.A": "LAK", "S.J": "SJS", "ARI": "UTA", "PHX": "UTA"}

NHL_TEAMS = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET", "EDM",
    "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR", "OTT", "PHI", "PIT",
    "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG",
]


def season_id(start_year: int) -> str:
    return f"{start_year}{start_year + 1}"


def teams_for_season(start_year: int) -> list[str]:
    t = [x for x in NHL_TEAMS]
    if start_year <= 2023:
        t = [x if x != "UTA" else "ARI" for x in t]
    if start_year <= 2020:
        t = [x for x in t if x != "SEA"]
    return t


class SourceError(RuntimeError):
    pass


class Source:
    def __init__(self, cache_dir: Path, offline: bool = False, interval: float = 0.12, log=print):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.interval = interval
        self.log = log
        self._lock = threading.Lock()
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.errors: list[str] = []

    # ------------------------------------------------------------------ core
    def _fresh(self, p: Path, max_age_h: float | None) -> bool:
        if not p.exists():
            return False
        if max_age_h is None or self.offline:
            return True
        return (time.time() - p.stat().st_mtime) / 3600 < max_age_h

    def _throttle(self):
        with self._lock:
            wait = self._last + self.interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def _download(self, url: str) -> bytes | None:
        for attempt in range(4):
            self._throttle()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                err = str(e)
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r.content
            if r.status_code == 404:
                return None
            err = f"HTTP {r.status_code}"
            time.sleep(2.0 * (attempt + 1))
        self.errors.append(f"{url} -> {err}")
        return None

    def json(self, url: str, key: str, max_age_h: float | None):
        p = self.cache / key
        if self._fresh(p, max_age_h):
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        if self.offline:
            return None
        raw = self._download(url)
        if raw is None:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return json.loads(raw)

    def csv(self, url: str, key: str, max_age_h: float | None) -> pd.DataFrame | None:
        p = self.cache / key
        if not self._fresh(p, max_age_h) and not self.offline:
            raw = self._download(url)
            head = (raw or b"")[:300].decode("utf-8", "ignore").lstrip("\ufeff")
            if raw is not None and len(raw) > 100 and not head.lstrip().startswith("<") \
                    and ("playerId" in head.split("\n")[0] or "team" in head.split("\n")[0]):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
            elif raw is not None:
                self.errors.append(f"{url} -> response was not a CSV (blocked or changed?)")
        if not p.exists():
            return None
        return pd.read_csv(p)

    # ------------------------------------------------------------------ NHL
    def schedule(self, date: str):
        return self.json(f"{NHL}/schedule/{date}", f"nhl/schedule/{date}.json", 2)

    def roster(self, team: str):
        return self.json(f"{NHL}/roster/{team}/current", f"nhl/roster/{team}.json", 12)

    def club_schedule(self, team: str, start_year: int, current: bool):
        sid = season_id(start_year)
        return self.json(f"{NHL}/club-schedule-season/{team}/{sid}",
                         f"nhl/club/{team}_{sid}.json", 2 if current else None)

    def landing(self, pid: int):
        return self.json(f"{NHL}/player/{pid}/landing", f"nhl/landing/{pid}.json", 20)

    def game_log(self, pid: int, start_year: int, current: bool):
        sid = season_id(start_year)
        return self.json(f"{NHL}/player/{pid}/game-log/{sid}/2",
                         f"nhl/gamelog/{pid}_{sid}.json", 3 if current else None)

    def boxscore(self, game_id: int):
        key = f"nhl/box/{game_id}.json"
        data = self.json(f"{NHL}/gamecenter/{game_id}/boxscore", key, None)
        if data and data.get("gameState") not in ("OFF", "FINAL") and not self.offline:
            (self.cache / key).unlink(missing_ok=True)
            data = self.json(f"{NHL}/gamecenter/{game_id}/boxscore", key, None)
        return data

    # ------------------------------------------------------------- MoneyPuck
    def moneypuck(self, kind: str, start_year: int, current: bool) -> pd.DataFrame | None:
        df = self.csv(MP.format(year=start_year, kind=kind), f"mp/{kind}_{start_year}.csv",
                      12 if current else None)
        if df is None:
            return None
        df.columns = [c.strip() for c in df.columns]
        if "team" in df.columns:
            df["team"] = df["team"].astype(str).map(lambda t: MP_TO_NHL.get(t, t))
        df["season_start"] = start_year
        return df
