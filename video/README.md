# Daily preview video

Renders a 45-60 s vertical video (1080x1920, 30 fps, H.264 + AAC) from the day's previews. One file works for
TikTok, Instagram Reels and YouTube Shorts.

    python video/render.py --date 2026-10-28          # reads origin/site for the model and odds
    python video/render.py --date 2026-10-28 --preview 1.5,6,30   # still frames only, for checking

Output in `video/out/` (git-ignored): `<DATE>-nhl-previews.mp4`, `<DATE>-cover.jpg`, `<DATE>-captions.txt`
(title, caption and hashtags to paste when posting).

- Layout: intro hook, one 9 s segment per game (photo card, matchup, headline, then model vs market win chance,
  projected goals and the top model player chance), outro with the site and an 18+ / not-advice line.
  With more than 5 games, the 5 with the biggest model-market gap get segments and the rest go on an "Also tonight" board.
- Important text stays between y=150 and y=1480 so the TikTok/Reels/Shorts buttons and captions don't cover it.
- Photos are the previews' Wikimedia Commons photos; the credit line is shown on every game segment.
- Music is synthesised in `music.py` (no samples), so there are no copyright claims. Loudness is normalised to -14 LUFS.
- Fonts (Anton, Exo 2, Oxanium) are under the SIL Open Font License, see `fonts/`.
- Needs: Python with Playwright (Chromium), Pillow, numpy, scipy, and ffmpeg. Rendering takes about 8 minutes on 2 CPUs.
