// Walkthrough recorder: drives the real site, captures every repaint (CDP screencast, JPEG q92) with timestamps.
// Usage: node rec.js <outdir> [stillsOnly]
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const OUT = process.argv[2], STILLS = process.argv[3] === "stills";
const W = 1440, H = 810, DSF = 4/3;     // -> 1920x1080 frames

const OVERLAY_CSS = `
#vo-cap{position:fixed;left:50%;bottom:34px;transform:translate(-50%,14px);z-index:9998;display:flex;align-items:center;gap:14px;
  max-width:min(980px,82vw);padding:13px 22px 13px 14px;border-radius:16px;background:rgba(9,13,19,.86);backdrop-filter:blur(14px);
  border:1px solid rgba(61,220,151,.35);box-shadow:0 18px 50px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.03) inset;
  font:600 23px/1.35 "Exo 2",sans-serif;color:#e7eef6;opacity:0;transition:opacity .45s ease,transform .55s cubic-bezier(.2,.8,.2,1);pointer-events:none}
#vo-cap.on{opacity:1;transform:translate(-50%,0)}
#vo-cap b.n{flex:0 0 auto;display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:#3ddc97;color:#04170d;font:800 17px "Oxanium",sans-serif}
#vo-cap em{font-style:normal;color:#3ddc97}
#vo-cur{position:fixed;left:0;top:0;z-index:9999;width:26px;height:26px;margin:-3px 0 0 -3px;pointer-events:none;opacity:0;transition:opacity .3s;filter:drop-shadow(0 3px 6px rgba(0,0,0,.5))}
#vo-cur.on{opacity:1}
.vo-rip{position:fixed;z-index:9998;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;border:2px solid #3ddc97;pointer-events:none;animation:vorip .6s ease-out forwards}
@keyframes vorip{from{transform:scale(.4);opacity:1}to{transform:scale(3.2);opacity:0}}
#vo-card{position:fixed;inset:0;z-index:10000;display:grid;place-items:center;background:radial-gradient(120% 90% at 50% 0%,#12301f 0%,#0a0e13 55%);
  opacity:0;transition:opacity .7s ease;pointer-events:none}
#vo-card.on{opacity:1}
#vo-card .in{text-align:center;transform:translateY(10px);transition:transform 1.2s cubic-bezier(.2,.8,.2,1)}
#vo-card.on .in{transform:none}
#vo-card h1{margin:0;font:800 74px/1 "Oxanium",sans-serif;letter-spacing:1px;color:#e7eef6}
#vo-card h1 b{color:#3ddc97}
#vo-card p{margin:18px 0 0;font:500 25px/1.4 "Exo 2",sans-serif;color:#8796a8}
#vo-card p.s{margin-top:28px;font-size:18px;color:#5f6f82}
#vo-card .bar{width:120px;height:3px;margin:26px auto 0;border-radius:3px;background:linear-gradient(90deg,transparent,#3ddc97,transparent)}
.totop{display:none!important}
`;
const CURSOR = `<svg id="vo-cur" viewBox="0 0 24 24"><path d="M4 2.5l15 9.2-6.6 1.3 3.9 7.4-2.9 1.5-3.9-7.5L4.6 19z" fill="#fff" stroke="#0a0e13" stroke-width="1.3" stroke-linejoin="round"/></svg>`;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: DSF, colorScheme: "dark" });
  await ctx.addInitScript(() => { localStorage.setItem('age18', '1'); });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('PAGEERR', e.message));
  await p.goto('http://localhost:8771/app/index.html');
  await p.waitForTimeout(1500);
  await p.evaluate(() => document.fonts.ready);
  await p.addStyleTag({ content: OVERLAY_CSS });
  await p.evaluate((cur) => {
    document.body.insertAdjacentHTML("beforeend", `<div id="vo-cap"></div><div id="vo-card"><div class="in"></div></div>` + cur);
    const c = document.getElementById("vo-cur");
    addEventListener("mousemove", e => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
    window.voEase = (y, ms) => new Promise(res => { const y0 = scrollY, t0 = performance.now();
      const f = now => { const k = Math.min(1, (now - t0) / ms), e = k < .5 ? 4*k*k*k : 1 - Math.pow(-2*k + 2, 3) / 2; scrollTo(0, y0 + (y - y0) * e); k < 1 ? requestAnimationFrame(f) : res(); };
      requestAnimationFrame(f); });
  }, CURSOR);

  // ---- capture
  const cdp = await ctx.newCDPSession(p);
  const frames = []; let marks = [];
  let t0 = null;
  cdp.on('Page.screencastFrame', async (f) => {
    const ts = f.metadata.timestamp; if (t0 === null) t0 = ts;
    const i = frames.length, file = path.join(OUT, `f${String(i).padStart(5, '0')}.jpg`);
    fs.writeFileSync(file, Buffer.from(f.data, 'base64'));
    frames.push({ file, t: ts - t0 });
    try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) {}
  });
  const now = async () => (await p.evaluate(() => performance.now()));
  let tStart = 0;
  const mark = async (name) => { marks.push({ name, at: frames.length ? frames[frames.length - 1].t : 0 }); };

  // ---- helpers
  const cap = async (n, html) => { await p.evaluate(([n, h]) => { const c = document.getElementById("vo-cap"); c.classList.remove("on");
      setTimeout(() => { c.innerHTML = (n ? `<b class="n">${n}</b>` : "") + `<span>${h}</span>`; c.classList.add("on"); }, 260); }, [n, html]); };
  const capOff = async () => p.evaluate(() => document.getElementById("vo-cap").classList.remove("on"));
  const card = async (html) => p.evaluate((h) => { const c = document.getElementById("vo-card"); c.querySelector(".in").innerHTML = h; c.classList.add("on"); }, html);
  const cardOff = async () => p.evaluate(() => document.getElementById("vo-card").classList.remove("on"));
  const curOn = async (on = true) => p.evaluate((on) => document.getElementById("vo-cur").classList.toggle("on", on), on);
  let mx = W * 0.6, my = H * 0.55;
  const moveTo = async (sel, opts = {}) => {
    const el = typeof sel === "string" ? await p.$(sel) : sel; if (!el) { console.log("missing", sel); return null; }
    const bb = await el.boundingBox(); if (!bb) return null;
    const x = bb.x + bb.width * (opts.fx ?? 0.5), y = bb.y + bb.height * (opts.fy ?? 0.5);
    const steps = Math.max(12, Math.round(Math.hypot(x - mx, y - my) / 14));
    await p.mouse.move(x, y, { steps }); mx = x; my = y; return { x, y };
  };
  const click = async (sel, opts = {}) => { const at = await moveTo(sel, opts); if (!at) return;
    await p.waitForTimeout(260);
    await p.evaluate(([x, y]) => { const r = document.createElement("div"); r.className = "vo-rip"; r.style.left = x + "px"; r.style.top = y + "px"; document.body.appendChild(r); setTimeout(() => r.remove(), 700); }, [at.x, at.y]);
    await p.mouse.down(); await p.waitForTimeout(70); await p.mouse.up(); };
  const scrollToEl = async (sel, offset = 120, ms = 1100) => p.evaluate(([s, o, ms]) => { const el = typeof s === "string" ? document.querySelector(s) : s; if (!el) return;
      return window.voEase(el.getBoundingClientRect().top + scrollY - o, ms); }, [sel, offset, ms]);
  const scrollBy = async (dy, ms = 1200) => p.evaluate(([dy, ms]) => window.voEase(scrollY + dy, ms), [dy, ms]);
  const wait = (ms) => p.waitForTimeout(ms);

  // pick a game with odds and two best picks, and a player with a value tag in its roster
  const plan = await p.evaluate(() => {
    const gs = DATA.games.filter(g => !g.frozen && DATA.odds?.[g.id]?.players && (!g.state || ["FUT","PRE"].includes(g.state)));
    let best = null;
    for (const g of gs) { state.bpg = "all"; const bp = bestPicks(g); if (bp.picks.length >= 2 && (!best || bp.picks[0].ev > best.ev)) best = { gid: g.id, ev: bp.picks[0].ev, team: bp.picks[0].pr.team, pid: bp.picks[0].pr.id }; }
    return best;
  });
  console.log("plan", JSON.stringify(plan));

  if (STILLS) { await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 }); }
  else await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });

  // ---------------- 0. title card
  await card(`<h1>NHL PROPS <b>LAB</b></h1><p>The model vs the market, for every NHL player prop</p><div class="bar"></div>`);
  await wait(2600);
  await mark("intro");
  // ---------------- 1. tonight's games
  await cardOff(); await wait(500);
  await cap(1, `Every game tonight, modelled player by player: <em>projected score, win chance, totals</em>`);
  await curOn(); await p.mouse.move(mx, my);
  await wait(900); await scrollBy(320, 1500); await wait(500);
  const cardSel = `.card.grow[data-g="${plan.gid}"]`;
  await scrollToEl(cardSel, 220, 1000); await wait(400);
  await moveTo(cardSel, { fx: 0.45, fy: 0.5 }); await wait(500);
  await mark("games");
  // ---------------- 2. game page: best picks
  await click(cardSel, { fx: 0.45, fy: 0.5 }); await wait(900);
  await cap(2, `<em>Best picks</em>: the two biggest edges against the sportsbook, with the facts behind them`);
  await scrollToEl('.box.picks', 90, 1100); await wait(900);
  await moveTo('.box.picks .pick', { fx: 0.3, fy: 0.55 }); await wait(2000);
  await click('.bpseg button[data-bpg="s"]'); await wait(1100);
  await click('.bpseg button[data-bpg="all"]'); await wait(900);
  await mark("picks");
  // ---------------- 3. roster grid
  await cap(3, `Every player's chances next to the odds. Green tags show the <em>edge</em>; outlined = value`);
  await scrollToEl('#sidebody table', 70, 1300); await wait(700);
  const tag = await p.$('#sidebody .cell .evtag.big') || await p.$('#sidebody .cell .evtag');
  let tagRow = null;
  if (tag) { const cell = await tag.evaluateHandle(e => e.closest('.cell')); await moveTo(cell, { fx: 0.5, fy: 0.6 }); tagRow = await tag.evaluateHandle(e => e.closest('tr.row').querySelector('td')); }
  await wait(2300);
  await mark("grid");
  // ---------------- 4. player breakdown
  await cap(4, `Open any player: <em>prices & value</em>, how the number is built, skill or luck`);
  await click(tagRow || '#sidebody tr.row td', { fx: 0.4 }); await wait(1500);
  await moveTo('.oddsgrid', { fx: 0.3, fy: 0.45 }); await wait(2000);
  await cap(4, `<em>Game by game</em>: every game with ice time, shots, xG, hits and blocks, against the line`);
  await scrollToEl('.l10box', 100, 1200); await wait(600);
  const bars = await p.$$('.l10box .gtg');
  if (bars.length > 5) { await moveTo(bars[bars.length - 3], { fy: 0.7 }); await wait(1500); await moveTo(bars[bars.length - 6], { fy: 0.7 }); await wait(1300); }
  const lb = await p.$('.l10lines-pick .seg button:nth-child(3)'); if (lb) { await click(lb); await wait(1300); }
  await mark("player");
  // ---------------- 5. props tab + parlay check
  await click('nav button[data-tab="props"], nav [data-tab="props"]'); await wait(1000);
  await p.evaluate(() => scrollTo(0, 0));
  await cap(5, `<em>Props</em>: every player in every game, sorted by chance, edge or value`);
  await wait(1500);
  const sl = await p.$$('#ptb tr.row:has(.ev-pos) .slipbtn');
  if (sl.length >= 2) { await click(sl[0]); await wait(800); await click(sl[1]); await wait(800); }
  await cap(5, `<em>Parlay check</em>: combined chance, fair odds and EV. Same-game legs are simulated together`);
  await click('.slipfab'); await wait(2400);
  await scrollBy(0, 10);
  const mb = await p.$('.slipbox'); if (mb) { await p.evaluate(() => { const m = document.querySelector('#modal .mbox'); if (m) m.scrollTo({ top: 260, behavior: 'smooth' }); }); }
  await wait(1800);
  await click('#modal [data-close], #modal .cmclose'); await wait(500);
  await p.evaluate(() => { try { SLIP.length = 0; store.set("slip", "[]"); slipSync?.(); } catch (e) {} render(); });
  await mark("props");
  // ---------------- 6. futures
  await click('nav [data-tab="futures"]'); await wait(1000);
  await cap(6, `<em>Futures</em>: playoff and Stanley Cup chances from thousands of simulated seasons`);
  await wait(1200); await scrollBy(300, 1700); await wait(900);
  await mark("futures");
  // ---------------- 7. track record
  await click('nav [data-tab="track"]'); await wait(300);
  await p.evaluate(() => scrollTo(0, 0));
  await cap(7, `Every best pick is graded in public: <em>wins, losses and voids, nothing removed</em>`);
  await wait(2200); await scrollToEl('.rkchart', 130, 1200); await wait(2300);
  await scrollToEl('.rklist', 160, 1200); await wait(1600);
  await mark("record");
  // ---------------- outro
  await capOff(); await curOn(false);
  await card(`<h1>NHL PROPS <b>LAB</b></h1><p>A tool and a guide for your own decisions</p><p class="s">Analysis, not a promise of profit · 18+ · Play responsibly</p>`);
  await wait(3400);
  await mark("end");
  await cdp.send('Page.stopScreencast');
  await wait(300);
  fs.writeFileSync(path.join(OUT, 'frames.json'), JSON.stringify({ frames, marks }));
  console.log("frames", frames.length, "duration", frames.length ? frames[frames.length - 1].t.toFixed(1) : 0, "s; fps", (frames.length / frames[frames.length - 1].t).toFixed(1));
  await b.close();
})();
