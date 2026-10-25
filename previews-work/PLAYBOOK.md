# Daily game previews: playbook

Goal: write `data/previews/<DATE>.json` for every NHL game on DATE (the NHL / North American date of
today's games, i.e. today in New York), in English and Swedish, then publish.

## 1. Games and model numbers
- Fetch the built site data: `git fetch origin site && git show origin/site:app/index.html`, extract the JSON after
  `const DATA=` (it is a single JSON object). Use `games` with `date == DATE`.
- Per game, take: `id, home, away, start, venue`, `default` (proj_h, proj_a, p_home, p_away, p_tie_reg, exp_total,
  over_5.5, over_6.5), `hist_over`, `h2h` (n, avg_total), `starters` + `starter_status` (goalie names from `goalies`),
  team context from `teams[abbr]` (w, l, otl, streak, streak_kind, last5, l5_gf, l5_ga, gf_pg, ga_pg, xgf_pct5, pdo,
  over_5.5, injuries), player flags from `players` (`regress.flag` hot/due, `drought.games`), and each team's top
  players by `props[].p.pt` / `p.g` (active players only).
- Market odds: `odds[<game id>]` (`winner` home/away decimal, `totals` per line over/under, `players` per player).
  No-vig probability for a two-way price: (1/a) / ((1/a)+(1/b)).

## 2. Research (real sources only)
For each game, search the web for the latest previews, news and reports (AP game previews, NHL.com, team sites,
The Hockey News, Daily Faceoff, TSN/Sportsnet, ESPN, theScore, beat writers). Collect only verifiable facts:
injuries and returns, goalie news, coach situation (hot seat, firing rumours, losing streaks), milestones
(a player close to a round number of goals/points/games), team and player records, streaks, notable quotes.
Never invent facts, numbers or quotes. If something cannot be verified, leave it out. Keep 2-3 source links per game in `sources` (stored for checking; the page does not show them). Write everything in your own words; never copy sentences from the sources.

## 3. Write each preview (schema = data/previews/2026-10-26.json)
- `en` and `sv` objects with: `kicker` ("Kings at Rangers"), `title` (one line, specific), `deck` (2-3 sentences: the
  angle of the game), `news` (3-4 bullets), `records` (3-4 bullets: streaks, records, milestones, positive or
  negative), `betting` (3 bullets: moneyline model vs market, total model vs market + history, model chances for
  2-3 key players; add prop edges if player odds exist).
- Betting bullets describe where model and market agree or disagree. They are not tips: no "bet this", no stakes.
- Plain, direct sentences. Swedish must be natural Swedish (not word-for-word), decimal comma, "%" with a space.
- Order previews by start time.

## 4. Photos (free licences only)
- Put one search per game in `previews-work/queries.json` (`{"<slug>": "Player Name"}`; slug = `<DATE>-<away>-<home>`
  lowercase), commit, push, run `gh api -X POST repos/markoras4/nhl-props/actions/workflows/photos.yml/dispatches -f ref=main -f 'inputs[mode]=candidates'`,
  wait for it, then `git fetch origin photo-candidates` and look at the images (`git archive origin/photo-candidates | tar -x`)
  and `candidates.json`.
- Choose a sharp, relevant, landscape photo per game (player of that team, or the arena/teams). Avoid photos that
  show a player in a different team's uniform unless the story is about that (e.g. facing his old team).
- Write `previews-work/picks.json` (`{"<slug>": "File:..."}`), commit, push, run the same workflow with
  `inputs[mode]=fetch`, then pull: photos land in `site-assets/previews/<slug>.jpg` with `credits.json`.
- Convert each to WebP 1400px wide (Pillow, quality 80), delete the .jpg, and fill `photo` in the preview:
  `src: assets/previews/<slug>.webp`, `alt`, `author`, `license`, `license_url`, `page` from credits.json
  (add `pos: "center 25%"` for portrait shots so faces are not cut).

## 5. Publish
- Validate the JSON (`python3 -c "import json;json.load(open(...))"`), commit `data/previews` and `site-assets/previews`,
  push to main, then run the build: `gh api -X POST repos/markoras4/nhl-props/actions/workflows/daily.yml/dispatches -f ref=main`.
- If there are no games on DATE, do nothing.
