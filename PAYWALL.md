# Free / Pro / visitors: agreed plan (build together with Stripe)

Rule of thumb: anything about the game or the teams is free; anything that helps decide a bet on a specific player is Pro.
Consistency rules (2026-11-09):
* A chance and its fair odds are always shown together or locked together (fair odds = 1 / chance). What is always
  Pro: the sportsbook price, value and EV.
* Counts from public data (the game logs in data/gl/, shot data) are never locked: a lock that can be rebuilt from
  public files is not used.
* Season-long player chances are Pro (milestones, Art Ross, Rocket Richard, award "stand-in" chances, goalie
  saved-goals leader), and so are the projection ranges and the season-end chart: with them the chances could be
  rebuilt closely (Rocket Richard within ~1-4 points, points milestones within ~1.5). Projected totals stay free.
* Tooltips follow the same rules as the page: a tooltip never shows a number that is locked where it appears.
* Every player profile has the same sections. Open and locked players differ only in tonight's model numbers,
  prices and value (and the "How the number is built" box).
Locked items are shown blurred with a small lock and an "Unlock with Pro" panel (not hidden).
Pro data must only be sent to Pro users (served by a Supabase function that checks the subscription), never just hidden in the page.

| Feature | Visitors / Free | Pro |
|---|---|---|
| Game cards (projected score, win %, total goals, shots line) | yes | yes |
| Game previews (landing page, previews page, game pages) | yes, except the player-prop betting bullet (blurred) | everything |
| Lineups, goalies, injuries, Who's missing | yes | yes |
| Team ranks, defence by position table, last 6 games, head-to-head | yes | yes |
| Team shots & total shots | yes | yes |
| More markets (1X2, puck line, team totals, 1st period, BTTS) | yes (game level) | yes |
| Goalie saves | expected saves number | line probabilities & fair odds |
| Player probabilities, EV | 1 forward + 1 defenceman per team | everyone |
| Best picks in each game (live, until the lock) | no: blurred box ("2 picks · +edge") with an upgrade button | every tab (All, Points, Multi-point, …) |
| Best picks record (Track record: W–L, units, ROI, CLV, charts by market/team/player, every past pick) | yes, after each game locks | yes |
| Best picks record on the landing page | yes (public) | yes |
| First goalscorer | team "scores first" % | player list |
| Hits & blocks | past games (game-by-game chart, hit counts) | plus tonight's chances |
| Multi-point (2+/3+ points, 2+ goals, hat trick, 2+ assists), PP point, faceoffs won, penalties | no | yes |
| Shutout chance (goalie card, More markets) | yes | yes |
| Role & usage box | PP minutes and unit only | everything, incl. tonight's chances |
| Linemates tonight | yes | yes |
| Lines tab (lines side by side, change flags, record together) | lines, change flags, record together | plus per-line projections (scores %, xG, shots, penalty) |
| Compare players (2–4, any game) | only the open players (1 F + 1 D per team) | everyone, every market |
| Compare: Quick read (rule-based) | with the open players | everyone |
| Compare: Ask AI (planned with payments; API cost) | no | yes, daily limit |
| Shot profile tiers (elite / good / average / below) | season tiers | everything |
| Odds movement | no | yes |
| Player page: last 10 games + chart | yes | yes |
| Hit rates | L5, L10, L20, season (counts from the public game logs) | plus vs opponent and model vs market |
| Per-player matchup (what the opponent allows his position) | yes (same numbers as the free defence-by-position table and the Tonight card) | yes |
| Shot map | season, last 10 and single-game maps, with tiers | plus the shot-profile numbers |
| Streaks view & "Streaks to watch" | the list (who, what run, how rare) | chance tonight, fair odds, EV |
| Milestones | yes | yes |
| My picks (tracking) | yes (free account) | yes (later: export, ROI by market) |
| Closing odds / CLV per pick (auto, or typed in for markets the feed lacks) | yes (free account) | yes |
| Watchlist: follow players, next game, marker on game cards | up to 3 players (free account) | unlimited + game-day push alerts |
| Sharp-book second price (shown beside the main sportsbook) | no | yes |
| Alerts | no | yes |
| Track record, How it works | yes | yes |
| Futures tab (playoffs, rounds, Cup, division, points) | chances and fair odds | plus sportsbook odds and value |
| What's at stake (game importance) | level only | now / with a win / with a loss |
| Game recaps (last 6, past meetings) | yes | yes |
| Season projections (player profile, Futures → Players, Explorer, Awards) | projected points / goals / assists (the total only) | plus the range, the season-end chart, milestone chances and Art Ross / Rocket Richard chances |
| Season awards | projection leaders (sorted by projected goals / points / saved goals) | plus model chances, sportsbook prices and value |
| Archive: last 7 days as before puck drop, with ✓/✗ results | games list, scores and results only | everything (player breakdowns, picks, compare) |

Also planned for that release: 18+ check on the landing page again, Stripe checkout, prices (not decided yet).
| Lines & pairs list: every line/pair in the league, sortable by xG%, chances/60, over/under-scoring, goaltending luck | no | yes |
| Parlay check: combined chance with same-game links, fair odds, EV, staking plans, saved and graded parlays | 2-leg check (free account to save) | full |
| Public betting (tickets % and money % per side: moneyline, total, puck line) | no | yes |
| Triage grades (Solid / Lean / Fragile, reasons, season and last-10 checks) on prop cells, Best picks, Prices & value | only the open players (1 F + 1 D per team) | everyone |
| Slate triage list (Props → Triage: every value bet of the day, game filter, linked warnings) | "How Triage works" panel + blurred list with the counts ("38 Solid · 52 Lean tonight") and an upgrade button | full list |
| Track record: results by triage grade | yes (public) | yes |
| Tonight bar on the game-by-game chart (empty bar for the upcoming game + card) | the bar, and the card's "His side" and "Opponent" blocks (ice time, PP unit, luck, ranks) | plus the "Tonight" block: model chance, odds, EV, grade, season / last-10 views |
| Players explorer (scatter of any two stats, presets, top-10 list) | all stat presets (Luck, Volume vs finishing, Role, Power play, Shots trend) and free choice of stats | plus the "Tonight" preset and the model / market axes |
| Teams explorer (all 32 teams on two stats, six presets, top-10 list) | everything | – (free) |
| Unlucky / hot tags, typical shots per goal, team news | yes | yes |
| Hand-copied sportsbook prices (game markets in More markets) | yes (game level) | yes |
| Hand-copied sportsbook player prices (shots, multi-point, PP points, blocks) | same rules as other player prices: open players; multi-point, PP and blocks are Pro | everyone |

## Plans in the app (built)

| Plan | Who | Access |
|---|---|---|
| Visitor | not signed in | Free column; account features ask to sign up |
| Free | free account (`profiles.plan = 'free'`) | Free column + account features (picks, closing odds, watchlist ≤ 3, 2-leg parlays) |
| Pro | `profiles.plan = 'pro'` | everything |
| Creator | `profiles.plan = 'creator'` | everything in Pro + creator licence note in the account window |
| Media | `profiles.plan = 'media'` | everything in Pro + media licence note in the account window |

How it is enforced:
* `nhlprops/paywall.py` splits every build: the page carries Free data only (open players = biggest projected ice
  time F and D per team; Pro markets, odds movement, sharp prices, public betting, futures odds, stakes numbers,
  line rankings, hit rates beyond L5 and the preview prop bullet are removed). The rest goes to `output/pro/`.
* The daily workflow uploads `output/pro/` to the private `pro` storage bucket through `supabase/functions/pro-upload`,
  signed with GitHub's own OIDC token (no stored key; only this repo's main branch is accepted).
  `supabase/functions/pro-data` returns a 60-second signed link only to Pro / Creator / Media.
* `supabase/migrations/*_paywall.sql`: plan values, `has_pro()`, watchlist ≤ 3 and 2-leg parlays for free, alerts Pro only.
  Users can't change their own plan (only `display_name` is granted); a Stripe webhook should set it.
* `supabase/functions/send-alerts` reads the private alerts feed and only alerts Pro plans.
* Local testing: `py tools/paywall/localsite.py <built site> .local-test/site`, then
  `py tools/paywall/devserver.py .local-test/site 8787` and open `http://localhost:8787/app/?as=free` (visitor/free/pro/creator/media).

## Sharp-book prices (hourly)
* Pinnacle's guest API blocks Supabase's servers and many home connections, but not GitHub's runners.
* The first workflow run each hour (the pre-game ticks run every 10-15 min, 13:00-08:30 UTC on game days) runs
  `python -m nhlprops sharp` and posts the rows to `supabase/functions/pin-sync`, signed with GitHub's OIDC token.
  `GET pin-sync` returns how old the newest stored price is; the step skips when it is under 50 minutes.
* Table `pin_odds` (Pro-only reads). The app merges newer prices for Pro accounts on load and every 15 minutes, and
  the roster grid footer shows "Sharp-book prices updated HH:MM". Site builds still fetch Pinnacle themselves.
