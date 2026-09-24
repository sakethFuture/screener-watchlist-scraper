// Full end-to-end check: ephemeral MySQL -> migrate -> seed -> real Express
// API -> real docs/index.html served statically -> a real headless browser
// driving the actual UI against the actual API. This is the closest thing
// to "does a colleague opening the URL see live shared data" without an
// actual Fly.io/Aiven deployment.
const { createDB } = require('mysql-memory-server');
const { execFileSync, spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const puppeteer = require('puppeteer');

const SERVER_DIR = __dirname;
const DOCS_DIR = path.join(__dirname, '..', 'docs');
const INDEX_PATH = path.join(DOCS_DIR, 'index.html');

function log(...args) { console.log('[E2E]', ...args); }
function assert(cond, msg) { if (!cond) throw new Error('ASSERTION FAILED: ' + msg); log('PASS:', msg); }

async function waitForHealth(baseUrl, tries = 40) {
  for (let i = 0; i < tries; i++) {
    try { const res = await fetch(baseUrl + '/health'); if (res.ok) return; } catch (e) {}
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error('API never became healthy');
}

function startStaticServer(dir, port) {
  const server = http.createServer((req, res) => {
    let p = req.url.split('?')[0];
    if (p === '/') p = '/index.html';
    const full = path.join(dir, p);
    fs.readFile(full, (err, data) => {
      if (err) { res.writeHead(404); res.end('not found'); return; }
      const ext = path.extname(full);
      const type = ext === '.html' ? 'text/html' : ext === '.js' ? 'application/javascript' : 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': type });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(port, () => resolve(server)));
}

(async () => {
  log('Starting ephemeral MySQL...');
  const db = await createDB({ dbName: 'test_e2e' });
  const env = {
    ...process.env,
    DATABASE_URL: `mysql://${db.username}:@127.0.0.1:${db.port}/${db.dbName}`,
    DB_SSL: 'false', PORT: '3412', ALLOWED_ORIGINS: '*',
  };

  log('Migrating + seeding...');
  execFileSync(process.execPath, ['src/migrate.js'], { cwd: SERVER_DIR, env, stdio: 'inherit' });
  execFileSync(process.execPath, ['src/seed.js'], { cwd: SERVER_DIR, env, stdio: 'inherit' });

  log('Starting API server on :3412...');
  const apiServer = spawn(process.execPath, ['src/index.js'], { cwd: SERVER_DIR, env, stdio: 'inherit' });
  await waitForHealth('http://127.0.0.1:3412');

  log('Patching docs/index.html API_BASE for this test run...');
  const original = fs.readFileSync(INDEX_PATH, 'utf8');
  const patched = original.replace(
    "const API_BASE = 'https://REPLACE-WITH-YOUR-FLY-APP.fly.dev';",
    "const API_BASE = 'http://127.0.0.1:3412';"
  );
  if (patched === original) throw new Error('API_BASE placeholder string not found - cannot patch for test');
  fs.writeFileSync(INDEX_PATH, patched);

  let staticServer, browser;
  try {
    staticServer = await startStaticServer(DOCS_DIR, 8790);
    log('Static server serving docs/ on :8790');

    browser = await puppeteer.launch({ headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));
    // "Failed to load resource ... 404" console lines are just the browser's
    // own echo of a network response - cross-checked properly via the
    // 'response' listener below (which also tells us the actual URL,
    // something the console message never includes).
    page.on('console', (msg) => {
      if (msg.type() === 'error' && !/failed to load resource/i.test(msg.text())) errors.push('CONSOLE: ' + msg.text());
    });
    page.on('response', (r) => { if (r.status() === 404 && !r.url().includes('favicon')) errors.push('404: ' + r.url()); });

    await page.goto('http://127.0.0.1:8790/index.html', { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForFunction(() => typeof state !== 'undefined' && state.companies && state.companies.length > 0, { timeout: 15000 });
    log('Ledger loaded from the real API.');

    const companyCount = await page.evaluate(() => state.companies.length);
    assert(companyCount === 257, `state.companies has 257 entries (deduped from 259 SEED rows), got ${companyCount}`);

    // ---- Q1 2026 overrides visible on load, via the real API ----
    await page.evaluate(() => { state.activeQ = 'Q1 2026'; render(); });
    const itc = await page.evaluate(() => state.companies.find(c => c.name === 'ITC').quarters['Q1 2026']);
    assert(itc.cat === 'Stalwart' && itc.rec === 'Sell', `ITC Q1 2026 shows the override (Stalwart/Sell) via the real API, got ${JSON.stringify(itc)}`);

    // ---- Baseline (Q4 FY25-26 prior-quarter reference) still present ----
    const baseline = await page.evaluate(() => state.companies.find(c => c.name === 'Reliance Industry').baseline);
    assert(baseline && (baseline.cat || baseline.rec), `Reliance Industry baseline is populated client-side, got ${JSON.stringify(baseline)}`);

    // ---- Direct table edit persists through the real API + a reload ----
    await page.evaluate(() => { state.activeQ = 'Q2 2026'; render(); });
    await new Promise(r => setTimeout(r, 200));
    const editedName = await page.evaluate(() => {
      const row = document.querySelector('#tableBody tr');
      const select = row.querySelector('select.rec-select');
      select.value = 'Buy';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      return row.querySelector('.co-name').value;
    });
    await new Promise(r => setTimeout(r, 300)); // let the PATCH request land
    await page.reload({ waitUntil: 'networkidle0' });
    await page.waitForFunction(() => typeof state !== 'undefined' && state.companies && state.companies.length > 0, { timeout: 15000 });
    const afterReload = await page.evaluate((name) => {
      const co = state.companies.find(c => c.name === name);
      return co.quarters['Q2 2026'].rec;
    }, editedName);
    assert(afterReload === 'Buy', `edit to ${editedName}'s Q2 2026 rec survived a full page reload (via the real DB), got ${afterReload}`);

    // ---- Fund Factsheets tab: upload -> save -> list, against the real API ----
    await page.click('.app-tab-btn[data-tab="factsheets"]');
    await new Promise(r => setTimeout(r, 300));
    const itiPath = 'C:/Users/saket/Downloads/ITI-MF-Factsheet.pdf';
    if (fs.existsSync(itiPath)) {
      const input = await page.$('#ffPdfInput');
      await input.uploadFile(itiPath);
      await page.waitForFunction(() => document.getElementById('ffReviewPanel').style.display === 'block', { timeout: 30000 });
      await page.click('#ffSaveBtn');
      await page.waitForFunction(() => document.getElementById('ffReviewPanel').style.display === 'none', { timeout: 15000 });
      await page.waitForFunction(() => document.querySelectorAll('.fund-card').length > 0, { timeout: 15000 });
      const cardCount = await page.evaluate(() => document.querySelectorAll('.fund-card').length);
      assert(cardCount > 0, `${cardCount} fund card(s) rendered after upload, saved to the real DB`);

      // Reload and confirm the fund is still there (came from the DB, not IndexedDB).
      await page.reload({ waitUntil: 'networkidle0' });
      await page.waitForFunction(() => document.querySelectorAll('.fund-card').length > 0, { timeout: 15000 });
      const cardCountAfterReload = await page.evaluate(() => document.querySelectorAll('.fund-card').length);
      assert(cardCountAfterReload === cardCount, `fund count unchanged after reload (${cardCountAfterReload}), proving it's DB-backed not per-browser`);

      await page.click('.app-tab-btn[data-tab="cross"]');
      await new Promise(r => setTimeout(r, 400));
      const xfText = await page.evaluate(() => document.getElementById('xfSummaryStats').textContent);
      assert(/Funds analyzed/.test(xfText), 'Cross-Factsheet Analysis tab loaded from the real API');
      log('Cross analysis stats:', xfText.replace(/\s+/g, ' ').trim());

      await page.click('.app-tab-btn[data-tab="preferred"]');
      await new Promise(r => setTimeout(r, 400));
      const rankRows = await page.evaluate(() => document.querySelectorAll('#mpStockList .rank-row').length);
      assert(rankRows > 0, `Most Preferred tab shows ${rankRows} ranked stock(s) from the real API`);
    } else {
      log('SKIP: ITI-MF-Factsheet.pdf not found locally, skipping fund-upload leg of this test');
    }

    if (errors.length) {
      log('Console/page errors:', JSON.stringify(errors, null, 2));
      throw new Error(`${errors.length} console/page error(s) occurred`);
    }
    log('No console/page errors.');
    log('\nALL END-TO-END CHECKS PASSED');
  } finally {
    fs.writeFileSync(INDEX_PATH, original);
    log('Restored docs/index.html to its committed API_BASE placeholder.');
    if (browser) await browser.close();
    if (staticServer) staticServer.close();
    apiServer.kill();
    await db.stop();
  }
})().catch(async (e) => {
  console.error('FAILURE:', e);
  process.exit(1);
});
