# Daily best-bets video

Renders a ~76 s vertical video (1080x1920, 30 fps, H.264 + AAC) for TikTok, Instagram Reels and YouTube Shorts.

    python video/render.py --date 2026-10-28                 # numbers from origin/site:app/index.html
    python video/render.py --date 2026-10-28 --preview 2,5,9 # still frames only, for checking

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
  the bet (model vs market, edge, reasons) and who wins (model, market, public bets), projected goals, playoff stakes.
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
  WON/LOST stamp, running profit chart) -> multi-point long shots -> the night in numbers -> profit spotlights: the
  biggest winners' real site profiles (archive of that date, dark theme, phone width) with the pick's market row,
  "how the number is built" and the game-by-game chart highlighted, plus callouts -> outro (every pick tracked).
- Music: `music.review_track`, a different original song (half-time groove, FM electric piano, 808, bell sparkles on
  each winning pick). Template: `review.html`.

