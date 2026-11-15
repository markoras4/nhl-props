# Landing-page tour video

`rec.js` drives the real app in headless Chromium (Playwright), draws the captions, cursor and title cards inside
the page, and captures every repaint with the DevTools screencast (JPEG q92, 1920x1080) plus timestamps.

1. Serve a built app with every player's numbers. Easiest: run a local build (`py -m nhlprops build --no-open`, then
   `git checkout -- data` to drop the data files it touched), copy `output/index.html` to `.local-test/site/app/index.html`
   and `output/pro/latest.json` to `.local-test/pro/latest.json`, and start `tools/paywall/devserver.py` (port 8787).
   Set `TOUR_URL=http://localhost:8787/app/?as=pro` (default: `http://localhost:8771/app/index.html`). The recorder
   waits for the Pro data before it starts. It records a 1920x1080 window with the page zoomed 4/3 (a 1440x810 layout),
   because the screencast ignores the device pixel ratio. Title cards use the evenout logo and the Neon colours.
2. `node rec.js take` → frames + `take/frames.json`.
3. Build a concat list with each frame's duration (timestamps / speed-up, about 1.2–1.3x to land near 60 s), then
   `ffmpeg -f concat -safe 0 -i list.txt -vf "fps=30,format=yuv420p" -c:v libx264 -preset veryfast -crf 21 -movflags +faststart -an tour-1080.mp4`
   and a 1280x720 copy at CRF 25.
4. Replace `site-assets/tour-720-neon.mp4`, `tour-1080-neon.mp4` and `tour-poster-neon.jpg` (the landing page plays the
   Neon files); the build adds cache-busting versions. The poster is the title-card frame at 1440x810.
