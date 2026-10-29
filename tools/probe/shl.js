// Probe: open SHL stats pages in a real browser, save visible text/tables and every JSON response the pages load.
const { chromium } = require('playwright');
const fs = require('fs');
const OUT = 'probe-shl'; fs.mkdirSync(OUT, { recursive: true });
const PAGES = [
  ['overview', 'https://www.shl.se/game-stats/overview'],
  ['players', 'https://www.shl.se/game-stats/players'],
  ['teams', 'https://www.shl.se/game-stats/teams'],
  ['matches', 'https://www.shl.se/game-stats/matches'],
  ['goalies', 'https://www.shl.se/game-stats/goalkeepers'],
  ['standings', 'https://www.shl.se/game-stats/standings/standings?count=25'],
  ['schedule', 'https://www.shl.se/game-schedule'],
];
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'sv-SE' });
  const log = [];
  let jn = 0;
  for (const [name, url] of PAGES) {
    const p = await ctx.newPage();
    p.on('response', async r => {
      const u = r.url(), ct = (r.headers()['content-type'] || '');
      if (/json/.test(ct) && !/google|facebook|cookie|consent|analytics|sentry|onetrust|cookiebot/i.test(u)) {
        try { const body = await r.text(); jn++; const f = `${OUT}/j${String(jn).padStart(3,'0')}_${name}.json`; fs.writeFileSync(f, body);
          log.push({ page: name, url: u, status: r.status(), bytes: body.length, file: f }); } catch (e) {}
      }
    });
    try {
      await p.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
    } catch (e) { log.push({ page: name, error: String(e).slice(0, 200) }); }
    // accept cookies if a banner blocks
    for (const sel of ['button:has-text("Godkänn")', 'button:has-text("Acceptera")', 'button:has-text("Tillåt alla")', '#onetrust-accept-btn-handler', 'button:has-text("OK")']) {
      try { const el = await p.$(sel); if (el) { await el.click({ timeout: 2000 }); await p.waitForTimeout(1500); break; } } catch (e) {}
    }
    await p.waitForTimeout(4000);
    // collect links in the page that look like stats/game/player pages
    const info = await p.evaluate(() => {
      const links = [...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => /shl\.se\/(game-stats|game|player|team|statistik|games|stats|gamecenter)/.test(h));
      const tables = [...document.querySelectorAll('table')].map(t => ({ head: [...t.querySelectorAll('thead th, thead td')].map(x => x.innerText.trim()), rows: [...t.querySelectorAll('tbody tr')].slice(0, 5).map(tr => [...tr.children].map(td => td.innerText.trim())) }));
      const selects = [...document.querySelectorAll('select, [role="combobox"], [role="tab"], button')].map(x => x.innerText.trim()).filter(x => x && x.length < 40).slice(0, 80);
      return { title: document.title, links: [...new Set(links)].slice(0, 120), tables, selects, text: document.body.innerText.slice(0, 6000) };
    });
    fs.writeFileSync(`${OUT}/${name}.page.json`, JSON.stringify(info, null, 1));
    await p.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
    await p.close();
  }
  // open one finished game page and one player page if links were found
  const all = PAGES.map(([n]) => { try { return JSON.parse(fs.readFileSync(`${OUT}/${n}.page.json`)); } catch (e) { return { links: [] }; } });
  const links = [...new Set(all.flatMap(x => x.links))];
  const game = links.find(l => /\/game\/|gamecenter|\/games\/[a-z0-9-]+/.test(l) && !/schedule/.test(l));
  const player = links.find(l => /\/player\//.test(l) || /players\/[a-z0-9-]{6,}/.test(l));
  for (const [name, url] of [['game', game], ['player', player]]) {
    if (!url) { log.push({ page: name, error: 'no link found' }); continue; }
    const p = await ctx.newPage();
    p.on('response', async r => { const u = r.url(), ct = (r.headers()['content-type'] || '');
      if (/json/.test(ct) && !/google|facebook|cookie|consent|analytics|sentry/i.test(u)) { try { const body = await r.text(); jn++; const f = `${OUT}/j${String(jn).padStart(3,'0')}_${name}.json`; fs.writeFileSync(f, body); log.push({ page: name, url: u, status: r.status(), bytes: body.length, file: f }); } catch (e) {} } });
    try { await p.goto(url, { waitUntil: 'networkidle', timeout: 60000 }); } catch (e) {}
    await p.waitForTimeout(4000);
    // click through tabs on a game page (stats, events, lineups)
    for (const t of ['Statistik', 'Händelser', 'Laguppställning', 'Uppställning', 'Spelare', 'Matchfakta', 'Play-by-play', 'Skott']) {
      try { const el = await p.$(`text="${t}"`); if (el) { await el.click({ timeout: 2000 }); await p.waitForTimeout(2500); } } catch (e) {}
    }
    const info = await p.evaluate(() => ({ url: location.href, title: document.title, text: document.body.innerText.slice(0, 8000),
      tables: [...document.querySelectorAll('table')].map(t => ({ head: [...t.querySelectorAll('thead th')].map(x => x.innerText.trim()), rows: [...t.querySelectorAll('tbody tr')].slice(0, 4).map(tr => [...tr.children].map(td => td.innerText.trim())) })) }));
    fs.writeFileSync(`${OUT}/${name}.page.json`, JSON.stringify(info, null, 1));
    await p.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
    await p.close();
  }
  fs.writeFileSync(`${OUT}/requests.json`, JSON.stringify(log, null, 1));
  await b.close();
})();
