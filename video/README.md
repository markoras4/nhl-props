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
- Music is synthesised in `music.py` (no samples, no copyright claims); loudness -14 LUFS.
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

# Series music (varied songs)

    import music
    x = music.series_track("hot", duration, cues=[4.0, 12.0], seed="2026-11-16")   # float32 stereo (n, 2) at music.SR
    music.write_wav("music.wav", x)
    music.style_track("lofi", duration, cues, seed=7)    # force one style
    music.SERIES_STYLES                                  # {"board": ["synthdrive", "house", "electro"], ...}

- Series: `board` (energetic), `hot` (dark, half-time / trap), `report` (calm, warm), `preview` (cinematic build),
  `lab` (light, curious); unknown -> `board`. Three styles each, 15 in total (`music.STYLES[name]["desc"]`).
- `seed`: int or string. An ISO date rotates through the series' styles day by day (consecutive days never repeat a
  style); tempo, key, mode, chord progression, drum variant, bass/arp patterns and melody also come from the seed.
  Same seed -> identical audio; `None` -> random. `music.pick_style(series, seed)` tells which style will be used.
- `cues`: section starts; each gets a one-beat drum fill with the kick and bass dropping out, a high-pass "lift" over
  the bar before, a whoosh, then an impact + crash on the cue, and the next section starts on a downbeat. Energy changes per section
  (steady styles alternate, preview styles build up). Starts with a hit and the groove at 0 s, ends on a final chord/hit
  and fades to silence in the last ~1.5 s.
- All synthesised from scratch (no samples, no known melodies). Peak 0.89; a 60 s track renders in about 2-4 s.
