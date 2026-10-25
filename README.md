# NHL Props Lab

A personal NHL player-props and game-totals model built on **regression to the mean**.
It downloads stats from the NHL API and MoneyPuck, computes probabilities for
goal / assist / point / shots-on-goal props and over/under totals, and opens a dashboard in your browser.

## Setup (once)

1. Install **Python 3.10 or newer** from https://www.python.org/downloads/
   (Windows: tick "Add Python to PATH" in the installer).
2. Unzip this folder anywhere, for example `Documents\nhl-props`.
3. Open a terminal in the folder and install the libraries:

   ```
   pip install -r requirements.txt
   ```

## Daily use

**Windows:** double-click `run.bat`. **Mac/Linux:** `./run.sh`

Or from a terminal:

```
python -m nhlprops build                  # today's slate (North American date)
python -m nhlprops build --date 2026-10-28
python -m nhlprops grade                  # after games finish: score the saved predictions
python -m nhlprops check                  # test that every data source works
python -m nhlprops build --demo           # simulated data, no internet needed
```

The first run downloads four seasons of team results and five seasons of MoneyPuck data,
which takes about 5 minutes. After that, past seasons are cached and a daily build takes 1–2 minutes.
The dashboard is saved to `output/index.html`. Open it any time, on any device.

**If something fails on the first run:** run `python -m nhlprops check` and send the output to Claude.
The NHL API is unofficial and field names occasionally change.

## The weekly loop that makes it accurate

1. `build` before games start. Each build saves its probabilities in `data/history/`.
2. `grade` the next day. It compares predictions with final boxscores.
3. `build` again. The **Track record** tab shows calibration and Brier skill per market, and the best
   history weight for totals.
4. Change a setting in `nhlprops/config.py` (3 vs 5 seasons, shrinkage, the 6-game rule) and compare.
   Give each change a few hundred graded props before trusting it.

## What's in the model

| Your idea | Where it lives |
|---|---|
| Regression to the mean (shots without goals, Sh%) | Two-level shrinkage: league → career → recent seasons. ▲ Due / ▼ Hot / "shots dry" badges |
| Current season, prev 3 / 5 seasons, career averages | Player detail: per game and per season, plus Sh% |
| Time on ice | 60% last 10 games + 40% season average |
| Last 5 games form | Player detail and last-10 hit bars. TOI trend feeds the projection |
| Individual xG (ixG) | 50% of the goal rate (ixG × finishing skill), plus G − xG luck check |
| League goals average 5 yrs, O/U % | League bar on Games tab. Scoring-level factor on props |
| Goalies SV%, GAA, xGA vs GA, GSAx (3 yrs), vs opponent | Goalie factor = GA ÷ xGA regressed. Switchable starter |
| HDCF / HDCA, PDO, xGF% | Games tab (MoneyPuck high-danger data, 5v5) |
| PP / PK | PP expected minutes × PP xG rate vs opponent PK, inside game totals |
| Team O/U %, home/away, H2H 4 seasons | "History" column, blendable with the slider |
| 6-game rule with strength-based thresholds | Strong 8W/4L, average 6/6, weak 4W/8L. Weight slider (default 0 = flag only) |
| Back-to-backs | Factor on props and totals, plus backup-goalie handling |

## Notes

* Odds: type the bookmaker's decimal odds into the dashboard to see EV. They are saved in your browser.
* Bookmaker odds and Natural Stat Trick aren't scraped. MoneyPuck already provides the high-danger
  numbers, and props odds aren't on BetExplorer. These can be added later.
* Data: NHL.com and **MoneyPuck.com** (free for non-commercial use, credit required).
  Personal use only. Gamble responsibly. Stödlinjen: 020-81 91 00.
