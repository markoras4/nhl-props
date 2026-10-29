# Landing-page tour video

`rec.js` drives the real app in headless Chromium (Playwright), draws the captions, cursor and title cards inside
the page, and captures every repaint with the DevTools screencast (JPEG q92, 1920x1080) plus timestamps.

1. Serve a copy of the built app at `http://localhost:8771/app/index.html` (with `../data` and `../assets` next to it,
   and the Exo 2 / Oxanium fonts available locally).
2. `node rec.js take` → frames + `take/frames.json`.
3. Build a concat list with each frame's duration (timestamps / speed-up, about 1.2–1.3x to land near 60 s), then
   `ffmpeg -f concat -safe 0 -i list.txt -vf "fps=30,format=yuv420p" -c:v libx264 -preset veryfast -crf 21 -movflags +faststart -an tour-1080.mp4`
   and a 1280x720 copy at CRF 25.
4. Replace `site-assets/tour-720.mp4`, `tour-1080.mp4` and `tour-poster.jpg`; the build adds cache-busting versions.
