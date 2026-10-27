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
| Player probabilities, EV, best picks | 1 forward + 1 defenceman per team | everyone |
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
| Alerts | no | yes |
| Track record, How it works | yes | yes |
| Futures tab (playoffs, rounds, Cup, division, points) | chances | plus fair odds, sportsbook odds and value |
| What's at stake (game importance) | level only | now / with a win / with a loss |
| Game recaps (last 6, past meetings) | yes | yes |
| Archive: last 7 days as before puck drop, with ✓/✗ results | games list, scores and results only | everything (player breakdowns, picks, compare) |

Also planned for that release: 18+ check on the landing page again, Stripe checkout, prices (not decided yet).
