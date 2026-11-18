"""Short screen clips of the real evenout app for the videos ("see it on evenout").

The app is the built page on origin/site (app/index.html plus the data files it fetches), served to headless Chromium
from git objects (no network, no checkout of the 100 MB site tree: each file the page asks for is read with `git show`
once). Signed out = the free view everyone gets: Pro-only parts stay locked and are never shown as if free; every clip
is framed on sections that are free for every player. Nothing about odds may appear: the odds-format picker is hidden
and a guard hides any text about odds / markets / bookmakers / bets that is on screen (stats-only series go to TikTok).

Frames are recorded deterministically: for every video frame the script sets the scroll position, the finger / cursor
overlay and any CSS / Web Animations to the exact clip time (animations are paused and seeked), then takes a screenshot.
Output: <dir>/000.jpg ... at the size the video shows them (no scaling in the video template).

    clips = AppClips(work)                 # one browser for every clip of a video
    c = clips.player(pid, stat="p", kind="phone", caption="...", name="clip0")   # None when it can't be shown
    c = clips.explorer(name_query="Caufield", kind="desktop", caption="...", name="clip1")
    clips.close()

kind "phone": 390x844 CSS px at 2x, frames 560x1212 (Shorts phone mockup).  kind "desktop": 1024x659 CSS px (the
desktop layout) at 1.27x, frames 1296x834 (browser window in the long video).
"""
from __future__ import annotations

import json
import mimetypes
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
HOST = "http://site.local/"
FPS = 30

KINDS = {
    "phone": {"vw": 390, "vh": 844, "dpr": 2, "out": (560, 1212), "touch": True},
    "desktop": {"vw": 1024, "vh": 659, "dpr": 1296 / 1024, "out": (1296, 834), "touch": False},   # desktop layout, 1.27x for legibility
}

# Google Fonts the app asks for -> the same families from video/fonts (OFL), so captures need no network.
FONT_CSS = "\n".join(
    f'@font-face{{font-family:"{fam}";font-weight:{w};src:url({HOST}__vf/{f}) format("woff2")}}'
    for fam, w, f in [("Exo 2", "300 549", "exo2-500.woff2"), ("Exo 2", "550 749", "exo2-700.woff2"), ("Exo 2", "750 900", "exo2-800.woff2"),
                      ("Oxanium", "300 699", "oxanium-600.woff2"), ("Oxanium", "700 900", "oxanium-800.woff2")])

# page-side helpers: overlay (finger / cursor), odds guard, deterministic animations, scroll
DRIVER = r"""
(() => {
  if (window.__clip) return;
  const st = document.createElement('style');
  st.textContent = `#ofmt{visibility:hidden!important}#totop{display:none!important}html{scroll-behavior:auto!important}
    .hrstrip{display:none!important}[data-l10line]:not([data-l10line=""]){display:none!important}.pwlock{visibility:hidden!important}
    #__ptr{position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;opacity:0}
    #__ptr.touch i{position:absolute;left:-22px;top:-22px;width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.38);border:2px solid rgba(255,255,255,.9);box-shadow:0 2px 12px rgba(0,0,0,.5)}
    #__ptr.touch u{position:absolute;left:-22px;top:-22px;width:44px;height:44px;border-radius:50%;border:3px solid #ff4fa8}
    #__ptr.mouse svg{position:absolute;left:-3px;top:-2px;width:26px;height:32px;filter:drop-shadow(0 2px 4px rgba(0,0,0,.6))}
    #__ptr.mouse u{position:absolute;left:-26px;top:-26px;width:52px;height:52px;border-radius:50%;border:3px solid #ff4fa8}
    .__hl{outline:3px solid #ff4fa8!important;outline-offset:4px;border-radius:12px;box-shadow:0 0 0 9999px rgba(10,6,12,var(--dim,0))!important;position:relative;z-index:5}`;
  document.head.appendChild(st);
  const p = document.createElement('div'); p.id = '__ptr';
  document.body.appendChild(p);
  const FORBID = /\bodds\b|sportsbook|bookmaker|\bbook\b|stoiximan|bet365|unibet|pinnacle|draftkings|fanduel|betmgm|\bmarkets?\b|\bbet(s|ting|tor)?\b|parlay|wager|\bprice(s)?\b/i;
  window.__clip = {
    t0: null, anims: new Map(), hidden: [],
    setKind(touch) { p.className = touch ? 'touch' : 'mouse';
      p.innerHTML = touch ? '<u></u><i></i>' : '<u></u><svg viewBox="0 0 26 32"><path d="M2 2 L2 26 L8.5 20 L13 30 L17 28 L12.6 18.5 L21 18.5 Z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>'; },
    // pointer: x,y in viewport px, a = opacity, press 0..1 (scale), ring 0..1 (ripple progress, <0 = off)
    ptr(x, y, a, press, ring) { p.style.opacity = a; p.style.transform = `translate(${x}px,${y}px)`;
      const i = p.querySelector('i') || p.querySelector('svg'); if (i) i.style.transform = `scale(${1 - .18 * press})`;
      const u = p.querySelector('u'); u.style.opacity = ring >= 0 && ring <= 1 ? (1 - ring) : 0; u.style.transform = `scale(${.6 + ring * 1.6})`; },
    // every running CSS / Web animation at the clip time T (seconds), counted from when it first appeared
    anim(T) { for (const a of document.getAnimations()) { if (!this.anims.has(a)) this.anims.set(a, T); a.pause();
      try { a.currentTime = Math.max(0, (T - this.anims.get(a)) * 1000); } catch (e) {} } },
    // hide any on-screen text about odds, markets or bets (the smallest box around it that is not a whole section)
    guard() { const out = [];
      const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const hits = []; let n; while ((n = w.nextNode())) { if (n.parentElement && FORBID.test(n.nodeValue || '') && !n.parentElement.closest('#__ptr,script,style,noscript')) {
        // a sentence about books / odds inside a longer stats text: drop just that sentence
        const ss = (n.nodeValue || '').split(/(?<=[.!?])\s+/), keep = ss.filter(x => !FORBID.test(x));
        if (keep.length && keep.length < ss.length) { out.push('(sentence) ' + ss.filter(x => FORBID.test(x)).join(' ').slice(0, 60)); n.nodeValue = keep.join(' '); continue; }
        hits.push(n.parentElement); } }
      const sel = document.querySelector('#ofmt'); if (sel) hits.push(sel);
      for (let e of hits) { if (e.closest('footer, .foot, [hidden]')) continue;
        let x = e; while (x.parentElement && !x.parentElement.matches('.box, #main, body, section, .card, table, tbody, tr') && x.parentElement.getBoundingClientRect().height < 140) x = x.parentElement;
        if (x.style.visibility !== 'hidden') { x.style.visibility = 'hidden'; out.push((e.textContent || '').trim().slice(0, 60)); } }
      this.hidden.push(...out); return out; },
    box(sel, root) { const e = typeof sel === 'string' ? (root || document).querySelector(sel) : sel; if (!e) return null;
      const r = e.getBoundingClientRect(); return {x: r.left, y: r.top + scrollY, w: r.width, h: r.height}; },
    findBox(re, root) { const R = new RegExp(re, 'i');
      return [...(root || document).querySelectorAll('.box')].find(b => R.test(((b.querySelector('h4') || b).textContent || '').trim())) || null; },
  };
  window.__clip.setKind(false);
})();
"""


class Site:
    """origin/site (or a given ref) served from git objects."""

    def __init__(self, ref: str = "origin/site"):
        self.ref, self.cache = ref, {}

    def blob(self, rel: str):
        if rel not in self.cache:
            r = subprocess.run(["git", "show", f"{self.ref}:{rel}"], cwd=ROOT, capture_output=True)
            self.cache[rel] = r.stdout if r.returncode == 0 else None
        return self.cache[rel]

    def handle(self, route):
        u = route.request.url
        if u.startswith("https://fonts.googleapis.com/"):
            return route.fulfill(status=200, body=FONT_CSS, content_type="text/css")
        if not u.startswith(HOST):
            return route.abort()                           # no network: auth, live scores, CDN scripts stay off
        rel = u[len(HOST):].split("?")[0].split("#")[0]
        if rel.startswith("__vf/"):
            f = HERE / "fonts" / rel[5:]
            return route.fulfill(status=200, body=f.read_bytes(), content_type="font/woff2") if f.is_file() else route.fulfill(status=404, body="")
        if rel == "app" or rel.startswith("app/"):
            last = rel.rsplit("/", 1)[-1]
            if not ("." in last and self.blob(rel) is not None):
                rel = "app/index.html"                     # /app/<route>: the single-page app
        b = self.blob(rel or "index.html")
        if b is None:
            return route.fulfill(status=404, body="")
        route.fulfill(status=200, body=b, content_type=mimetypes.guess_type(rel)[0] or "application/octet-stream")


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 4 * x * x * x if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


class AppClips:
    def __init__(self, work: Path, ref: str = "origin/site", date: str | None = None):
        from playwright.sync_api import sync_playwright
        self.work, self.date = Path(work), date
        self.site = Site(ref)
        self._pw = sync_playwright().start()
        self.br = self._pw.chromium.launch()
        self.pages = {}
        self.log = []

    def close(self):
        try:
            self.br.close()
        finally:
            self._pw.stop()

    def page(self, kind: str, path: str = "app/"):
        K = KINDS[kind]
        if kind not in self.pages:
            ctx = self.br.new_context(viewport={"width": K["vw"], "height": K["vh"]}, device_scale_factor=K["dpr"], locale="en-US",
                                      timezone_id="America/New_York", color_scheme="dark", is_mobile=False, has_touch=K["touch"])
            ctx.add_init_script("try{localStorage.setItem('age18','1');localStorage.setItem('set:lang','en');localStorage.setItem('set:theme','dark')}catch(e){}")
            ctx.route("**/*", self.site.handle)
            pg = ctx.new_page()
            pg.goto(HOST + path)
            pg.wait_for_function("typeof DATA !== 'undefined' && typeof render === 'function' && typeof go === 'function' && document.querySelector('#main')", timeout=90000)
            pg.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
            pg.evaluate(DRIVER)
            pg.evaluate(f"() => __clip.setKind({'true' if K['touch'] else 'false'})")
            self.pages[kind] = pg
        return self.pages[kind]

    def _settle(self, pg, ms=900):
        pg.wait_for_timeout(ms)
        try:
            pg.wait_for_load_state("networkidle", timeout=4000)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ recording
    def _record(self, pg, kind: str, name: str, dur: float, scroll, pointer, actions, meta: dict) -> dict:
        """scroll(t) -> document y; pointer(t) -> (x, y, alpha, press, ring) in viewport px or None; actions: [(t, js)]
        run once when the clip reaches t (the page re-renders, then the guard runs again)."""
        from PIL import Image
        import io
        K = KINDS[kind]
        out = self.work / name
        out.mkdir(parents=True, exist_ok=True)
        pg.evaluate("() => { __clip.anims = new Map(); }")
        hidden = pg.evaluate("() => __clip.guard()")
        todo = sorted(actions, key=lambda a: a[0])
        n = int(round(dur * FPS))
        t0 = time.time()
        for f in range(n):
            t = f / FPS
            while todo and todo[0][0] <= t + 1e-6:
                pg.evaluate(todo.pop(0)[1])
                pg.wait_for_timeout(120)
                hidden += pg.evaluate("() => __clip.guard()")
            y = scroll(t)
            ptr = pointer(t) if pointer else None
            pg.evaluate("([y, p, T]) => { window.scrollTo(0, y); if (p) __clip.ptr(...p); else __clip.ptr(0, 0, 0, 0, -1); __clip.anim(T); }",
                        [y, list(ptr) if ptr else None, t])
            png = pg.screenshot(type="jpeg", quality=92)
            im = Image.open(io.BytesIO(png)).convert("RGB")
            if im.size != K["out"]:
                im = im.resize(K["out"], Image.LANCZOS)
            im.save(out / f"{f:03d}.jpg", quality=90)
        pg.evaluate("() => __clip.ptr(0, 0, 0, 0, -1)")
        url = pg.evaluate("() => location.href").replace(HOST, "https://evenout.app/")
        info = {"dir": name, "n": n, "w": K["out"][0], "h": K["out"][1], "dur": round(n / FPS, 3), "url": url, "kind": kind, **meta}
        print(f"  app clip {name}: {meta.get('what')} ({kind}, {n} frames, {time.time() - t0:.0f}s)" +
              (f", hid {sorted(set(hidden))}" if hidden else ""), flush=True)
        self.log.append(info)
        return info

    # ------------------------------------------------------------------ clips
    def player(self, pid, stat: str = "p", kind: str = "phone", name: str = "clip", caption: str = "", prefer_game=None, dur: float = 4.5):
        """His game page on the app: the "Last 10 games" box (Heat Index + game log, free for every player) and the
        "Game by game" chart; a tap on the Heat Index, then the chart switches to `stat` (s, g, a, p)."""
        pg = self.page(kind)
        pid = str(pid)
        info = pg.evaluate("""([pid, pref, stat]) => {
          const p = (typeof P === 'function' ? P(pid) : null) || DATA.players?.[pid];
          if (!p) return {err: 'no player'};
          const gs = DATA.games.filter(g => (g.home === p.team || g.away === p.team) && (!g.state || ['FUT','PRE'].includes(g.state)))
                               .sort((a, b) => (a.start || '').localeCompare(b.start || ''));
          const g = gs.find(x => String(x.id) === String(pref)) || gs[0];
          if (!g) return {err: 'no upcoming game for ' + p.team};
          state.l10m = stat === 's' ? 'g' : 's'; state.l10n = 10; state.l10pop = false; state.l10anim = false;
          go({tab: 'games', game: String(g.id), side: p.team}); state.open = String(pid); render();
          const r = document.querySelector(`tr.row[data-id="${pid}"]`);
          if (!r || !r.nextElementSibling) return {err: 'no row'};
          return {game: String(g.id), name: p.name, team: p.team, opp: g.home === p.team ? g.away : g.home};
        }""", [pid, str(prefer_game) if prefer_game else None, stat])
        if not info or info.get("err"):
            print(f"  app clip {name}: player {pid} not shown ({(info or {}).get('err')})")
            return None
        self._settle(pg, 1400)
        boxes = pg.evaluate("""(pid) => {
          const d = document.querySelector(`tr.row[data-id="${pid}"]`)?.nextElementSibling; if (!d) return null;
          const l10 = __clip.findBox('^last 10', d), gbg = __clip.findBox('^game by game', d);
          if (!l10 || !gbg) return null;
          const heat = l10.querySelector('.heat') || l10;
          return {l10: __clip.box(l10), gbg: __clip.box(gbg), heat: __clip.box(heat),
                  nav: (() => { const n = document.querySelector('#nav')?.getBoundingClientRect(); return n && n.top > innerHeight / 2 ? innerHeight - n.top : 0; })()};
        }""", pid)
        if not boxes:
            print(f"  app clip {name}: {info['name']} has no free Last 10 / Game by game boxes")
            return None
        K = KINDS[kind]
        top = 64 if kind == "phone" else 70                # leave room under the app's sticky header
        stat_js = f"""() => {{ const d = document.querySelector('tr.row[data-id="{pid}"]').nextElementSibling;
            const b = [...d.querySelectorAll('[data-l10]')].find(x => x.dataset.l10 === '{stat}'); if (b) b.click(); }}"""
        btn = lambda: pg.evaluate(f"""() => {{ const d = document.querySelector('tr.row[data-id="{pid}"]').nextElementSibling;
            const b = [...d.querySelectorAll('[data-l10]')].find(x => x.dataset.l10 === '{stat}'); if (!b) return null;
            const r = b.getBoundingClientRect(); return {{x: r.left + r.width / 2, y: r.top + scrollY + r.height / 2}}; }}""")
        hx, hy = boxes["heat"]["x"] + boxes["heat"]["w"] * .62, boxes["heat"]["y"] + min(30, boxes["heat"]["h"] / 2)
        if kind == "phone":
            y0 = boxes["l10"]["y"] - top
            G = boxes["gbg"]
            y1 = min(G["y"] - top, G["y"] + G["h"] - (K["vh"] - boxes["nav"]) + 14)   # chart box ends just above the tab bar
            y1 = max(y1, y0)
            b = btn()
            T = {"tap1": .7, "s0": 1.5, "s1": 3.0, "tap2": 3.35}
            def scroll(t):
                return y0 + (y1 - y0) * ease((t - T["s0"]) / (T["s1"] - T["s0"]))
            def pointer(t):
                if t < T["s0"]:                             # tap on the Heat Index
                    a = min(1, max(0, (t - .35) / .2)) * (1 - min(1, max(0, (t - 1.25) / .2)))
                    pr = max(0, 1 - abs(t - T["tap1"]) / .15)
                    return (hx, hy - scroll(t), a, pr, (t - T["tap1"]) / .45 if t >= T["tap1"] else -1)
                if b and t >= T["tap2"] - .45:              # tap on the stat button of the chart
                    bx, by = b["x"], b["y"] - scroll(t)
                    a = min(1, max(0, (t - (T["tap2"] - .45)) / .2)) * (1 - min(1, max(0, (t - (T["tap2"] + .6)) / .2)))
                    pr = max(0, 1 - abs(t - T["tap2"]) / .15)
                    return (bx, by, a, pr, (t - T["tap2"]) / .45 if t >= T["tap2"] else -1)
                return None
            actions = [(T["tap2"], stat_js)]
        else:
            L, G = boxes["l10"], boxes["gbg"]
            y1 = max(L["y"] - top, max(L["y"] + L["h"], G["y"] + G["h"]) + 24 - K["vh"])
            y0 = y1 - 70
            b = btn()
            T = {"tap1": 1.2, "tap2": 2.9}
            def scroll(t):
                return y0 + (y1 - y0) * ease(t / 1.6)
            sx, sy = K["vw"] * .55, K["vh"] * .75             # cursor enters from below the middle
            def pointer(t):
                pts = [(0.0, sx, sy), (1.05, hx, hy - y1), (1.5, hx, hy - y1)]
                if b:
                    pts += [(2.7, b["x"], b["y"] - y1), (dur, b["x"] + 6, b["y"] - y1 + 4)]
                for (ta, xa, ya), (tb, xb, yb) in zip(pts, pts[1:]):
                    if ta <= t <= tb:
                        k = ease((t - ta) / max(1e-6, tb - ta))
                        x, y = xa + (xb - xa) * k, ya + (yb - ya) * k
                        break
                else:
                    x, y = pts[-1][1], pts[-1][2]
                tap = T["tap2"] if t >= 2.2 else T["tap1"]
                return (x, y, min(1, t / .3), max(0, 1 - abs(t - tap) / .12), (t - tap) / .45 if t >= tap else -1)
            actions = [(T["tap2"], stat_js)]
        return self._record(pg, kind, name, dur, scroll, pointer, actions,
                            {"what": f"{info['name']}: last 10 games, Heat Index, game by game", "caption": caption, "game": info["game"], "pid": pid})

    def explorer(self, query: str, kind: str = "desktop", name: str = "clip", caption: str = "", dur: float = 5.0):
        """Players explorer (Luck: goals vs expected goals): the chart scrolls in, then the player's name is typed into
        the search box and his dot lights up."""
        pg = self.page(kind)
        pg.evaluate("() => { go({tab: 'explore'}); }")
        self._settle(pg, 900)
        q = pg.evaluate("""() => { const q = document.querySelector('#exq'), c = document.querySelector('#exchart');
            if (!q || !c) return null; q.value = ''; q.dispatchEvent(new Event('input', {bubbles: true}));
            return {q: __clip.box(q), c: __clip.box(c), st: __clip.box('#exstory')}; }""")
        if not q:
            return None
        K = KINDS[kind]
        if kind == "phone":
            y0, y1 = 0, max(0, q["c"]["y"] - 40)
            qs = (q["q"]["x"] + 60, q["q"]["y"] + q["q"]["h"] / 2)
        else:
            y0, y1 = 0, max(0, min(q["q"]["y"] - 90, q["c"]["y"] + q["c"]["h"] - K["vh"] + 20))
            qs = (q["q"]["x"] + 60, q["q"]["y"] + q["q"]["h"] / 2)
        ttype = 1.3
        y2 = max(y1, q["c"]["y"] + q["c"]["h"] - K["vh"] + 10)      # desktop: then down to the whole chart
        def scroll(t):
            if kind == "desktop":
                return y0 + (y1 - y0) * ease((t - .3) / 1.2) + (y2 - y1) * ease((t - 2.9) / 1.1)
            return y0 if t < 3.0 else y0 + (y1 - y0) * ease((t - 3.0) / 1.0)
        def pointer(t):
            x, y = qs[0], qs[1] - scroll(t)
            if kind == "desktop":
                x0, y0p = K["vw"] * .5, K["vh"] * .8
                k = ease(t / 1.1)
                x, y = x0 + (x - x0) * k, y0p + (y - y0p) * k
            a = min(1, t / .3) * (1 - min(1, max(0, (t - 2.5) / .3)))
            return (x, y, a, max(0, 1 - abs(t - 1.15) / .12), (t - 1.15) / .45 if t >= 1.15 else -1)
        word = query[:14]
        acts = [(1.15, "() => document.querySelector('#exq').focus()")]
        for i in range(1, len(word) + 1):
            acts.append((ttype + i * .09, f"() => {{ const q = document.querySelector('#exq'); q.value = {json.dumps(word[:i])}; q.dispatchEvent(new Event('input', {{bubbles: true}})); }}"))
        acts.append((ttype + len(word) * .09 + .1, "() => document.querySelector('#exq').blur()"))
        return self._record(pg, kind, name, dur, scroll, pointer, acts, {"what": f"Players explorer, goals vs expected goals, {query}", "caption": caption})
