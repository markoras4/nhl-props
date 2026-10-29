# Daily best-bets video

Renders a ~60 s vertical video (1080x1920, 30 fps, H.264 + AAC) for TikTok, Instagram Reels and YouTube Shorts.

    python video/render.py --date 2026-10-28                 # numbers from origin/site:app/index.html
    python video/render.py --date 2026-10-28 --preview 2,5,9 # still frames only, for checking

Output in `video/out/` (git-ignored): `<DATE>-nhl-best-bets.mp4`, `<DATE>-cover.jpg`, `<DATE>-captions.txt`.

How it works
- `extract.js` runs inside the built site page in headless Chromium and calls the site's own functions, so the
  video always matches the site: Best picks (`bestPicks`, `pickReasons`), model probabilities (`prob`), book odds and
  no-vig prices, public betting splits, playoff stakes per game and futures.
- Game choice: only games with a preview photo and a best pick; photos whose original is at least 3000 px wide and
  landscape first ("very high quality"), then the strongest best bet (biggest edge). 3-4 games, in start order.
- Each game (12 s): story (photo, matchup, preview headline) -> player chances table (goal, point, SOG 3+, best bet
  highlighted) -> best-bet player profile (number, role, season line, last 10 games chart, the bet, model vs market,
  edge, reasons from the site) -> who wins (model, market, public bets), projected goals, playoff stakes.
- Outro: the site's other features (shots, hits & blocks, lines & PP units, goalies, public %, playoff & Cup odds,
  streaks, slip) with the model's Stanley Cup favourite, then the brand, URL, follow and an 18+ / not-advice line.
- Text stays between y=150 and y=1480 so platform buttons and captions don't cover it. Photo credits are on screen.
- Music is synthesised in `music.py` (no samples, no copyright claims); loudness -14 LUFS.
- Fonts (Anton, Exo 2, Oxanium) are SIL Open Font License, see `fonts/`.
- Needs Python with Playwright (Chromium), Pillow, numpy, scipy, and ffmpeg. About 10 minutes on 2 CPUs.
