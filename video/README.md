# Daily best-bets video

Renders a ~76 s vertical video (1080x1920, 30 fps, H.264 + AAC) for TikTok, Instagram Reels and YouTube Shorts.

    python video/render.py --date 2026-10-28                 # numbers from origin/site:app/index.html
    python video/render.py --date 2026-10-28 --preview 2,5,9 # still frames only, for checking

On Windows use `py` and UTF-8 mode: `set PYTHONUTF8=1` (cmd) or `$env:PYTHONUTF8=1` (PowerShell) first.

Look: the site's Neon colours (pink #ff4fa8, purple #b794ff, amber #ffb547, orange #ff6a3d) and the evenout logo
(site-assets/brand/evenout-logo-dark.svg) in the top bar and on the end card.

Output in `video/out/` (git-ignored): `<DATE>-nhl-best-bets.mp4`, `<DATE>-cover.jpg`, `<DATE>-captions.txt`.

How it works
- `extract.js` runs inside the built site page in headless Chromium and calls the site's own functions, so the
  video always matches the site: Best picks (`bestPicks`, `pickReasons`), model probabilities (`prob`), book odds and
  no-vig prices, public betting splits, playoff stakes per game and futures.
- Game choice: only games with a preview photo and a best pick; photos whose original is at least 3000 px wide and
  landscape first ("very high quality"), then the strongest best bet (biggest edge). 3-4 games, in start order.
- Each game (16 s): story (photo, matchup, preview headline) -> every player of the best pick's team (model goal,
  point and SOG 3+ chances next to the book's goal odds, rows and bars animate in), a scan bar finds the best pick
  and the table zooms into his row -> his real player profile from the site (captured in dark theme at phone width
  by `capture_profiles` in render.py) in a phone frame, scrolling down at an even pace to the game-by-game chart ->
  the bet (model vs market, edge, reasons, his Heat Index) and who wins (model, market, public bets), projected goals, playoff stakes.
- Outro: the site's other features (shots, hits & blocks, lines & PP units, goalies, public %, playoff & Cup odds,
  streaks, slip) with the model's Stanley Cup favourite, then the brand, URL, follow and an 18+ / not-advice line.
- Text stays between y=150 and y=1480 so platform buttons and captions don't cover it. Photo credits are on screen.
- Music: every video uses the licensed song (Snap Crackle) via `--music` or env `VIDEO_MUSIC_PATH`; the workflow fetches it from private storage (never commit it: public repo). Fallback when it is missing: the synthesised music in `music.py`. Loudness -14 LUFS.
- Fonts (Anton, Exo 2, Oxanium) are SIL Open Font License, see `fonts/`.
- Needs Python with Playwright (Chromium), Pillow, numpy, scipy, and ffmpeg. About 10 minutes on 2 CPUs.

# Review video (last night's results)

    python video/review.py                      # yesterday (New York); --date 2026-10-28 for another night
    python video/review.py --preview 4,8,30,36  # still frames only

Output in `video/out/`: `<DATE>-nhl-review.mp4`, `<DATE>-review-cover.jpg`, `<DATE>-review-captions.txt`.
- Data: every pick of that exact date from the site's Track record (origin/site:data/bestpicks.json: the picks as
  they stood at the lock, graded after the game), results and box-score lines from origin/site:data/archive/<DATE>.json.
- Intro with the night's record and units -> every main pick as a card (photo, score, model vs odds, what he did,
  WON/LOST stamp, running profit chart) -> multi-point long shots (only when one won at 5.00+) -> the night in numbers
  -> profit spotlights (up to 4 winners, 7.5 s each): his real site profile (the site's archive view of that date,
  served from the origin/site tree, dark theme, phone width) in a phone. Every spotlight opens on Prices & value
  (his pick's row highlighted), then flicks to one more section, a different one per spotlight, chosen for the market:
  points -> Averages chart (season by season, 82-game pace) / Heat Index / Game by game (P, with the line) / How the number is
  built / Skill or luck; sections the public site locks for Pro are skipped; goals -> Skill or luck first; shots -> Shot profile / Game by game (S). The callout uses his
  own numbers from that section. Then the result. Varied entrances (flip, rise, zoom), camera push-ins, spotlight
  masks and gold frames on the highlighted parts -> outro (every pick tracked).
- Music: `music.review_track`, a different original song, no rising build-up at the start (half-time groove, FM electric piano, 808, bell sparkles on
  each winning pick). Template: `review.html`.

# Series videos (video/series.py)

    py video/series.py --date 2026-11-17 --all --slot morning      # night (+ report on Mondays)
    py video/series.py --date 2026-11-17 --series night --preview 8,14,21

- `board` Tonight's Board (daily, YouTube only), `hot` Hot or lucky? (Tue/Thu/Sat, YouTube + TikTok), `report` Report
  Card (Mondays, YouTube only), `night` Last night (every morning at 10:00 Sweden, YouTube + TikTok).
- `night` is stats only (TikTok): never odds, picks, grades, prop lines or prices. Yesterday = `--date` minus one day;
  data from `origin/site:data/archive/<yesterday>.json` (scores, box scores, pre-game lines), `data/shots.json` (goal
  locations), `data/frozen/<yesterday>.json` (playoff chances at lock) and the built page via `night_extract.js`
  (season lines, Heat Index, goalie GSAx and playoff chances after last night). Scenes: final scores, star of the night,
  then the strongest 3 of wall of the night, line of the night, finishing vs xG, where the goals came from, playoff
  chance movers (a date-seeded nudge varies the pick). Scenes without a real fact are dropped; no archive = no video.



    import music
    x = music.series_track("hot", duration, cues=[4.0, 12.0], seed="2026-11-16")   # float32 stereo (n, 2) at music.SR
    music.write_wav("music.wav", x)
    music.style_track("basshouse", duration, cues, seed=7)  # force one style
    music.SERIES_STYLES                                     # {"board": ["basshouse", "deserthouse", "deephouse"], ...}

- 15 styles, 3 per series, modelled on genre / tempo / groove / sound / mix balance of 9 reference tracks (HiLau,
  Moavii, Nebulite, Zambolino, bensound; their licences exclude brand videos, so nothing from them is used: no
  samples, melodies, hooks or chord sequences). `music.STYLES[name]["ref"]` / `["desc"]` say which and how.
  board (house): `basshouse`, `deserthouse`, `deephouse` | hot (darker): `trespass`, `machine`, `darkdesert` |
  report (hip-hop): `boombap`, `dreamhop`, `moonlight` | preview (bouncy): `bounce`, `funkmachine`, `futurepop` |
  lab (light): `brightfeel`, `nightcruise`, `dreamloop`. night reuses `bounce`, `brightfeel`, `dreamloop`. Unknown series -> `board`.
- Bass-heavy (sub + bass carry most of the energy), dark top end, dense master, loop-based, one short hook per style,
  full groove from the first beat.
- `cues`: no risers/sweeps/uplifts. Each cue gets a one-beat drum fill (kick/backbeat drop out, bass keeps going) and
  the next segment alternates A/B (extra percussion or a second layer joins in B parts); the groove stays on one bar
  grid. Ends on a final chord + hit and fades to silence in the last ~1.5 s.
- `seed`: int or string. An ISO date rotates through the series' styles day by day (consecutive days never repeat a
  style); tempo, key, mode, chord loop and patterns also come from the seed. Same seed -> identical audio.
- Preview all styles: `video/out/music-sampler-v3.mp3` (12 s each, in `STYLES` order). 60 s renders in ~1.5 s.

# Game previews (video/previews.py)

- One 9:16 Short per featured game tonight (3 by default: games with a written preview, star power, close matchups).
  The matchup, the game's written preview (headline, deck, preview photo), then 6 moments on the real app, each with
  a one-line explanation under the phone. YouTube (workflow, 15:30 slot) and TikTok (stats only: odds hidden).
- The moments vary by game and day (seeded): game pages (final-score chances, team ranks, lines, goalies, each defence
  by position, playoff chances) and two featured players' cards (how the number is built, skill or luck, linemates
  with/without, season projection, shot chart season / last 10 / last game, game by game, the averages chart in any
  stat and per game / 82-game pace / per season / season by season, last 10 + Heat Index).
- `py -X utf8 video/previews.py --date 2026-11-27 --n 3 --music "<song.mp3>"`; `--games id1,id2` to pick games;
  `--preview 3,9,16` for stills. New player-card sections: `AppClips.card(pid, "shots", focus="svg", taps=[...])`.

# Tonight's slate breakdown (long-form YouTube, video/longform.py)

    py video/longform.py --date 2026-11-17                       # full render (~8 min video)
    py video/longform.py --date 2026-11-17 --preview beats       # one still per beat in video/out/<DATE>/stills/ (+ thumbnail)
    py video/longform.py --date 2026-11-17 --preview 12,48.5     # stills at these seconds
    py video/longform.py --date 2026-11-17 --games 2026020339,2026020340 --max-seconds 60   # pick the games, short test

- 16:9 1920x1080, 30 fps, music and on-screen text only (no voice), YouTube only. Stats and model numbers only: never
  odds, sportsbooks, picks or grades. Template `longform.html`, data `longform_extract.js` (the built app page, plus the
  Pro patch when there is one: point chances need it, everything else works on the public numbers).
- Story: cold open (games to watch) -> all games (ET / CET, model win chance, expected goals) -> why these games ->
  per game: matchup card, then 4-5 sections picked FOR THAT GAME from a pool (model's view, team stats board with league
  ranks, who drives play, scoring/defence/luck, special teams, goalies, line stats, player profile deep-dive, player
  duel, head-to-head & stakes) by what stands out (biggest gaps / storylines), then "keys" (wording varies) ->
  recap -> evenout.app. Rules (`plan_sections`): no section type in more than 2 games, a different first beat after
  every matchup card, a player beat (profile or duel) in every game; a penalty for sections earlier games showed.
  3 games when the slate has 6+, chosen by storyline (closest game, hottest player, star power, playoff stakes,
  goalie gap, division rivals). Career vs tonight's opponent comes from origin/site:data/gl/<player>.json.
- Timing: each beat lasts its animation plus 0.35 s per word to read (headings and row labels count half, numbers and
  tiles a tenth), never under 4 s. YouTube chapters (description) start at every game.
- Rendering: every animation in the template goes through `seg()`, which flags the frame as moving; `plan()` returns a
  key per frame and still frames are captured once and repeated (about a quarter of the frames are captured). No CSS
  `filter: blur` in the template: it makes captures several times slower.
- Music: `--music <file>` loops a licensed track under the whole video (2 s crossfade at each loop seam, 1 s fade-in,
  3 s fade-out, -18 LUFS in the final encode). Never commit music files (public repo): the workflow downloads it from
  private storage when the repository variable `LONGFORM_MUSIC` names it (TODO step in the slate job), or uses
  `vars.LONGFORM_MUSIC_PATH`. Without one: `music.style_track` per section (intro, each game) from
  `SERIES_STYLES["slate"]` (nightcruise, moonlight, dreamloop, deephouse; the date picks the first), 1.5 s crossfades.
- Output: `slate-<DATE>.mp4`, `slate-<DATE>-thumb.jpg` (1280x720 custom thumbnail, the manifest's `cover`),
  `slate-<DATE>-spec.json` (what was shown) and the `slate` item in `manifest.json` (`"long": true`, YouTube only).
- Workflow: job `slate` in `.github/workflows/videos.yml`, after the `videos` job. Scheduled (15:30 Sweden) only when
  the repository variable `SLATE_DAILY` is `true`; on demand with "Run workflow" -> slate ticked, or slot = `slate`
  (only the slate video).

# Real images: app clips and preview photos (video/appclips.py, video/preview_photos.py)

- App clips ("On evenout: ..."): short recordings of the real app, the signed-out free view of the built page on
  origin/site, served to headless Chromium straight from git objects (no network: fonts from `video/fonts`, auth / CDN /
  live scores blocked). Every frame is recorded deterministically (scroll, finger / cursor overlay and the page's own
  animations are set to the clip time, then a screenshot), saved at display size and shown frame by frame by the
  templates (`render(t)` sets the `<img>`, the render loop waits for its decode: `RENDER` in series.py / longform.py).
  - Pages (all free in the signed-out view), each 4-5 s with a scripted scroll and a tap / cursor on what the video talks about:
    - `player`: a player's game page -> "Last 10 games" (Heat Index + game log) and the "Game by game" chart, a tap on the
      Heat Index, then a tap switching the chart to his stat.
    - `explorer`: Players explorer (goals vs expected goals) with a name typed into the search box.
    - `game`: a game page, lean (hero with projected goals, the section tabs, one section; everything about prices hidden):
      a tap on Matchup / Lines, a scroll to the section, a highlight: `teams` (Team ranks, one row lit, e.g. xGF% or PDO),
      `goalies` (both goalie cards, the better one lit), `lines` (tonight's lines, the line the video talks about lit).
    - `futures`: Season & playoffs (simulation): a tap on the teams' division / conference filter (or the Playoffs column),
      then both teams' rows lit. Fair prices, the Pro note and the legend are hidden.
    - `slate`: tonight's games list (projected goals, win chances; the over/under and price columns hidden): a tap on
      the day, a scroll, the featured games lit.
    - Not used: Track record (profit / units) and Props (prices).
  - Odds-free: the odds-format picker, the hit-rate strip (ends in a Pro market chip), prop-line buttons, Pro lock
    boxes, the games list's over/under + price columns, futures fair prices, the locked line projections and the footer
    are hidden; a guard removes any sentence about odds / markets / books / bets still in the page (logged as
    `hid [...]`). Same rules for every series, so clips are safe for TikTok.
  - Shorts (`series.clip_plan`): 2-3 clips in a phone (390x844 CSS px at 2x, shown 560x1212), about a quarter of the
    runtime. Right after the headline card the same player on the app, as a split beat (the card small on the left,
    the phone on the right, after 2 s the phone takes the middle): board -> first pick, hot -> hottest player, night ->
    star of the night. Then a page that fits the story: night -> the explorer after the "luck" card and Futures after a
    playoff race (else the explorer / his team's playoff chances); hot -> the explorer with the other player; board ->
    alternating by day the pick's game page (team ranks) or both teams' playoff chances, before the outro. Report: none.
  - Slate video: about 15-20 % of the runtime, each clip tied to its section: tonight's games list after the slate grid
    (`full`: the browser large, caption below), per game the player page after the profile / duel (browser or, every
    other game, a phone) and 1-2 section clips (2 in every other game; page types earlier games didn't show first):
    team stats -> team ranks and line stats -> tonight's lines (`side`: our board shrinks to the left, the app on the
    right), goalies -> goalie cards, head-to-head & stakes -> Futures (phone), plus the Players explorer before the
    recap. Desktop layout 1024x659 CSS px, shown 1296x834 in a browser window.
  - A clip that can't be recorded (page / section / selector missing) is dropped and the timeline rebuilt.
  - Shorts background blobs are radial gradients (look of the old 120 px blur, about half the capture time per frame),
    which pays for the extra clip frames.
- Preview photos: the game previews' Wikimedia Commons photos (site-assets/previews, credits in data/previews/<date>.json
  and credits.json), cropped to fit (focal point from the preview), never stretched, darkened, soft blurred edges,
  brand gradient on top. Slate video: full-bleed behind each matchup card + a band on the "why these games" cards.
  Shorts: a band at the top of the card and a soft backdrop on scenes whose game has a photo (board / hot: tonight's
  previews, night: last night's). No photo -> the cards look as before.
- Photo credits are never on screen: author, licence and source link go in the YouTube description and a short
  "Photos: Name (CC BY-SA 4.0) via Wikimedia Commons, edited" line ends the TikTok caption (`preview_photos.credit_lines`).
