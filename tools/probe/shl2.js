// Probe 2: game centre tabs, a player page, older seasons, full player list.
const { chromium } = require('playwright');
const fs = require('fs');
const OUT = 'probe-shl2'; fs.mkdirSync(OUT, { recursive: true });
const log = []; let jn = 0;
const watch = (p, name) => p.on('response', async r => { const u = r.url(), ct = (r.headers()['content-type'] || '');
  if (/json/.test(ct) && /shl\.se\/api/.test(u) && !/site\/settings|layouts|sponsors|media\/render/.test(u)) { try { const body = await r.text(); jn++; const f = `${OUT}/j${String(jn).padStart(3,'0')}_${name}.json`; fs.writeFileSync(f, body); log.push({ page: name, url: u, status: r.status(), bytes: body.length, file: f }); } catch (e) {} } });
const consent = async p => { for (const sel of ['button:has-text("Godkänn alla")', 'button:has-text("Godkänn")', 'button:has-text("Acceptera alla")', 'button:has-text("Acceptera")', 'button:has-text("AGREE")', 'button:has-text("Jag godkänner")']) { try { const el = await p.$(sel); if (el) { await el.click({ timeout: 2000 }); await p.waitForTimeout(1500); return; } } catch (e) {} } };
const grab = async (p, name) => { const info = await p.evaluate(() => ({ url: location.href, title: document.title, text: document.body.innerText.slice(0, 12000),
    links: [...new Set([...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => /player|spelare|game-center/.test(h)))].slice(0, 60),
    tabs: [...document.querySelectorAll('[role="tab"], nav a, button')].map(x => x.innerText.trim()).filter(x => x && x.length < 30).slice(0, 80),
    tables: [...document.querySelectorAll('table')].map(t => ({ head: [...t.querySelectorAll('thead th')].map(x => x.innerText.trim()), rows: [...t.querySelectorAll('tbody tr')].slice(0, 4).map(tr => [...tr.children].map(td => td.innerText.trim())) })) }));
  fs.writeFileSync(`${OUT}/${name}.page.json`, JSON.stringify(info, null, 1)); await p.screenshot({ path: `${OUT}/${name}.png` }); return info; };
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'sv-SE' });
  // 1. finished game centre, every tab
  const p = await ctx.newPage(); watch(p, 'game');
  await p.goto('https://www.shl.se/game-center/fqfzcjooa4/qa98unlbd6/?state=post-game', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  await p.waitForTimeout(6000); await consent(p); await p.waitForTimeout(2000);
  let info = await grab(p, 'game_0');
  const tabNames = ['Matchfakta', 'Statistik', 'Laguppställning', 'Uppställning', 'Händelser', 'Spelare', 'Skott', 'Skottkarta', 'Lineup', 'Stats', 'Rapport', 'Matchrapport', 'Live', 'Översikt'];
  let k = 1;
  for (const t of tabNames) { try { const el = await p.$(`text="${t}"`); if (el) { await el.click({ timeout: 3000 }); await p.waitForTimeout(3500); await grab(p, `game_${k++}_${t}`); } } catch (e) {} }
  // 2. a player page from the game links
  const plink = (info.links || []).find(l => /player/.test(l));
  const all = fs.readdirSync(OUT).filter(f => f.endsWith('.page.json')).flatMap(f => JSON.parse(fs.readFileSync(`${OUT}/${f}`)).links || []);
  const pl = plink || all.find(l => /player/.test(l));
  if (pl) { const q = await ctx.newPage(); watch(q, 'player'); await q.goto(pl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {}); await q.waitForTimeout(7000); await consent(q); await grab(q, 'player');
    for (const t of ['Statistik', 'Matcher', 'Karriär', 'Säsong']) { try { const el = await q.$(`text="${t}"`); if (el) { await el.click({ timeout: 3000 }); await q.waitForTimeout(3000); await grab(q, `player_${t}`); } } catch (e) {} } }
  else log.push({ page: 'player', error: 'no player link' });
  // 3. API checks directly (same origin, via the page context)
  const api = await ctx.newPage(); await api.goto('https://www.shl.se/', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  const tryGet = async (name, url) => { try { const r = await api.evaluate(async u => { const x = await fetch(u); return { s: x.status, t: (await x.text()) }; }, url); jn++; const f = `${OUT}/j${String(jn).padStart(3,'0')}_${name}.json`; fs.writeFileSync(f, r.t); log.push({ page: name, url, status: r.s, bytes: r.t.length, file: f }); return r; } catch (e) { log.push({ page: name, url, error: String(e).slice(0, 150) }); } };
  await tryGet('types2025', 'https://www.shl.se/api/sports-v2/season-series-game-types-filter?series=qQ9-bb0bzEWUk&season=xs4m9qupsi');
  await tryGet('types2022', 'https://www.shl.se/api/sports-v2/season-series-game-types-filter?series=qQ9-bb0bzEWUk&season=qbN-XMFfjGVt');
  await tryGet('players_all', 'https://www.shl.se/api/statistics-v2/stats-info/players_summary?count=1000&ssgtUuid=qa98unlbd6&provider=statnet');
  await tryGet('sched2022', 'https://www.shl.se/api/sports-v2/game-schedule?seasonUuid=qbN-XMFfjGVt&seriesUuid=qQ9-bb0bzEWUk&gameTypeUuid=qQ9-af37Ti40B&gamePlace=all&played=all');
  // play-by-play for the first finished game of 2022/23 and 2018/19
  for (const [nm, season] of [['pbp2022', 'qbN-XMFfjGVt'], ['pbp2018', 'qUv-YXiuQN45']]) {
    const r = await tryGet(`sch_${nm}`, `https://www.shl.se/api/sports-v2/game-schedule?seasonUuid=${season}&seriesUuid=qQ9-bb0bzEWUk&gameTypeUuid=qQ9-af37Ti40B&gamePlace=all&played=all`);
    try { const g = JSON.parse(r.t).gameInfo.find(x => x.state === 'post-game'); if (g) { await tryGet(nm, `https://www.shl.se/api/gameday/play-by-play/${g.uuid}`); await tryGet(`${nm}_meta`, `https://www.shl.se/api/meta/game-metadata/multiple-uuids?gameUuids=${g.uuid}`); } } catch (e) { log.push({ page: nm, error: String(e).slice(0, 150) }); }
  }
  // candidate per-game player stats endpoints
  for (const [nm, u] of [['box1', 'https://www.shl.se/api/gameday/game-stats/fqfzcjooa4'], ['box2', 'https://www.shl.se/api/gameday/lineups/fqfzcjooa4'], ['box3', 'https://www.shl.se/api/gameday/player-stats/fqfzcjooa4'], ['box4', 'https://www.shl.se/api/statistics-v2/game/fqfzcjooa4'], ['box5', 'https://www.shl.se/api/gameday/team-stats/fqfzcjooa4']]) await tryGet(nm, u);
  fs.writeFileSync(`${OUT}/requests.json`, JSON.stringify(log, null, 1));
  await b.close();
})();
