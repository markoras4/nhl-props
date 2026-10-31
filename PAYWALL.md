# Free / Pro / visitors: agreed plan (build together with Stripe)

Rule of thumb: anything about the game or the teams is free; anything that helps decide a bet on a specific player is Pro.
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
| Hits & blocks | no | yes |
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
| Hit rates | L5 | L10, L20, season, vs opponent, model vs market |
| Per-player matchup (what the opponent allows his position) | no | yes |
| Shot map | season map | last 10, single game, profile numbers |
| Streaks view & "Streaks to watch" | the list (who, what run, how rare) | chance tonight, fair odds, EV |
| Milestones | yes | yes |
| My picks (tracking) | yes (free account) | yes (later: export, ROI by market) |
| Closing odds / CLV per pick (auto, or typed in for markets the feed lacks) | yes (free account) | yes |
| Watchlist: follow players, next game, marker on game cards | up to 3 players (free account) | unlimited + game-day push alerts |
| Sharp-book second price (shown beside the main sportsbook) | no | yes |
| Alerts | no | yes |
| Track record, How it works | yes | yes |
| Futures tab (playoffs, rounds, Cup, division, points) | chances | plus fair odds, sportsbook odds and value |
| What's at stake (game importance) | level only | now / with a win / with a loss |
| Game recaps (last 6, past meetings) | yes | yes |
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
| Unlucky / hot tags, typical shots per goal, team news | yes | yes |
| Hand-copied sportsbook prices (game markets in More markets) | yes (game level) | yes |
| Hand-copied sportsbook player prices (shots, multi-point, PP points, blocks) | same rules as other player prices: open players; multi-point, PP and blocks are Pro | everyone |
