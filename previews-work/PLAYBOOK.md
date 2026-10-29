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
- Put searches per game in `previews-work/queries.json` (`{"<slug>": ["Player A Team", "Player B", ...]}`, 3–6 searches: key players of both teams; slug = `<DATE>-<away>-<home>`
  lowercase), commit, push, run `gh api -X POST repos/markoras4/nhl-props/actions/workflows/photos.yml/dispatches -f ref=main -f 'inputs[mode]=candidates'`,
  wait for it, then `git fetch origin photo-candidates` and look at the images (`git archive origin/photo-candidates | tar -x`)
  and `candidates.json`.
- Quality first: only use originals at least 1600px wide and landscape (the candidates list prints the size; the script already drops smaller ones). Never pick portrait shots or small "(cropped)" files: cropping them to the wide hero makes them blurry. Prefer clear action shots (professional Flickr sets such as Jenn G / Jennthulhu are excellent).
- Choose a sharp, relevant, landscape photo per game (player of that team, or the arena/teams). Avoid photos that
  show a player in a different team's uniform unless the story is about that (e.g. facing his old team).
- Write `previews-work/picks.json` (`{"<slug>": "File:..."}`), commit, push, run the same workflow with
  `inputs[mode]=fetch`, then pull: photos land in `site-assets/previews/<slug>.jpg` with `credits.json`.
- Convert each to WebP 2000px wide (Pillow, quality 82, method=6), delete the .jpg, and fill `photo` in the preview:
  `src: assets/previews/<slug>.webp`, `alt`, `author`, `license`, `license_url`, `page` from credits.json
  (the hero is very wide, so a 4:3 photo shows only its middle band: add `pos: "center 20%"`–`"center 25%"` whenever faces are in the upper part of the photo, which is most action shots, so heads are not cut).

## 5. Publish
- Validate the JSON (`python3 -c "import json;json.load(open(...))"`), commit `data/previews` and `site-assets/previews`,
  push to main, then run the build: `gh api -X POST repos/markoras4/nhl-props/actions/workflows/daily.yml/dispatches -f ref=main`.
- If there are no games on DATE, do nothing.

## 6. Video (after the build is started and has finished)
- Wait until the daily build has pushed the new site (origin/site contains DATE's games and odds), then `git fetch origin site`.
- Run `python video/render.py --date <DATE>` (needs ffmpeg and Playwright; ~10 min, run it in the background and poll).
  It picks 3-4 games with very high quality photos and real best picks from the site, see video/README.md.
- Check a few stills if anything looks off: `--preview 2,5,9` writes frames to `video/out/`.
- Send `video/out/<DATE>-nhl-best-bets.mp4`, `<DATE>-cover.jpg` and `<DATE>-captions.txt` to the user (SendUserFile).
  Do not commit the video files.
