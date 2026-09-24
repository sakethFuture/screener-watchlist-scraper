const { createDB } = require('mysql-memory-server');
const { execFileSync, spawn } = require('child_process');
const path = require('path');

const SERVER_DIR = __dirname;

function log(...args) { console.log('[TEST]', ...args); }

function assert(cond, msg) {
  if (!cond) throw new Error('ASSERTION FAILED: ' + msg);
  log('PASS:', msg);
}

async function waitForHealth(baseUrl, tries = 30) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(baseUrl + '/health');
      if (res.ok) return;
    } catch (e) { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error('server never became healthy');
}

(async () => {
  log('Starting ephemeral MySQL (this downloads a MySQL binary on first run)...');
  const db = await createDB({ dbName: 'test_fund_platform' });
  log(`MySQL up on port ${db.port}`);

  const env = {
    ...process.env,
    DATABASE_URL: `mysql://${db.username}:@127.0.0.1:${db.port}/${db.dbName}`,
    DB_SSL: 'false',
    PORT: '3411',
    ALLOWED_ORIGINS: '*',
  };

  try {
    log('Running migration...');
    execFileSync(process.execPath, ['src/migrate.js'], { cwd: SERVER_DIR, env, stdio: 'inherit' });

    log('Running seed (imports docs/index.html SEED + data/overrides.json)...');
    execFileSync(process.execPath, ['src/seed.js'], { cwd: SERVER_DIR, env, stdio: 'inherit' });

    log('Starting API server...');
    const server = spawn(process.execPath, ['src/index.js'], { cwd: SERVER_DIR, env, stdio: 'inherit' });
    try {
      const base = 'http://127.0.0.1:3411';
      await waitForHealth(base);
      log('Server healthy.');

      // ---- companies ----
      let r = await fetch(base + '/api/companies');
      let companies = await r.json();
      // 259 rows in SEED, but "NMDC" and "Coforge" each appear twice with
      // the same name - a pre-existing duplicate in the ledger's own data,
      // not something this migration introduces. companies.name is UNIQUE
      // by design, so the migration correctly collapses them to 257.
      assert(companies.length === 257, `companies count is 257 (259 SEED rows minus 2 duplicate names: NMDC, Coforge), got ${companies.length}`);
      const hdfc = companies.find((c) => c.name === 'HDFC Bank');
      assert(hdfc.sector === 'Financial Services', 'HDFC Bank sector correctly seeded');

      // ---- quarters: Q1 2026 reflects backfilled seed + overrides ----
      r = await fetch(base + '/api/quarters/Q1%202026');
      let q1 = await r.json();
      const itc = q1.find((c) => c.name === 'ITC');
      assert(itc.cat === 'Stalwart', `ITC Q1 2026 cat is Stalwart (override), got ${itc.cat}`);
      assert(itc.rec === 'Sell', `ITC Q1 2026 rec is Sell (override), got ${itc.rec}`);
      assert(itc.catConfirmed === true, 'ITC catConfirmed is true');
      assert(itc.recConfirmed === true, 'ITC recConfirmed is true');

      const reliance = q1.find((c) => c.name === 'Reliance Industry');
      assert(reliance.cat === 'Stalwart', `Reliance cat overridden to Stalwart, got ${reliance.cat}`);
      assert(reliance.catConfirmed === true, 'Reliance catConfirmed true');
      assert(reliance.recConfirmed === false, 'Reliance recConfirmed left false (rec was NOT overridden)');

      // ---- guard: sync must not clobber a confirmed field ----
      r = await fetch(base + '/api/quarters/Q1%202026/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([{ name: 'ITC', classification: 'Fast Grower', recommendation: 'Buy' }]),
      });
      assert(r.status === 200, 'sync request succeeded');
      r = await fetch(base + '/api/quarters/Q1%202026');
      q1 = await r.json();
      const itcAfterSync = q1.find((c) => c.name === 'ITC');
      assert(itcAfterSync.cat === 'Stalwart', `ITC cat survives a sync attempt (still Stalwart), got ${itcAfterSync.cat}`);
      assert(itcAfterSync.rec === 'Sell', `ITC rec survives a sync attempt (still Sell), got ${itcAfterSync.rec}`);

      // ---- sync DOES update an unconfirmed company ----
      const before = q1.find((c) => c.name === 'HDFC Bank');
      r = await fetch(base + '/api/quarters/Q1%202026/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([{ name: 'HDFC Bank', classification: 'Stalwart', recommendation: 'Hold' }]),
      });
      const syncResult = await r.json();
      assert(syncResult.updated === 1, `sync reported 1 update, got ${syncResult.updated}`);
      r = await fetch(base + '/api/quarters/Q1%202026');
      q1 = await r.json();
      const hdfcAfter = q1.find((c) => c.name === 'HDFC Bank');
      assert(hdfcAfter.cat === 'Stalwart', `HDFC Bank cat updated by sync (unconfirmed), got ${hdfcAfter.cat}`);

      // ---- direct PATCH edit (the ledger dropdown path) ----
      r = await fetch(base + `/api/quarters/${hdfc.id}/${encodeURIComponent('Q2 2026')}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cat: 'Fast Grower', rec: 'Buy', techRec: 'Hold' }),
      });
      assert(r.status === 200, 'PATCH edit succeeded');
      r = await fetch(base + '/api/quarters/Q2%202026');
      const q2 = await r.json();
      const hdfcQ2 = q2.find((c) => c.name === 'HDFC Bank');
      assert(hdfcQ2.cat === 'Fast Grower' && hdfcQ2.rec === 'Buy' && hdfcQ2.techRec === 'Hold', 'PATCH edit persisted cat/rec/techRec for Q2 2026');

      // ---- overrides: insert a NEW correction via POST, verify it applies ----
      r = await fetch(base + '/api/overrides', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company: 'TCS', quarter: 'Q1 2026', field: 'rec', corrected_value: 'Sell', confirmed: true }),
      });
      const overrideResult = await r.json();
      assert(overrideResult.applied === true, 'new override applied immediately');
      r = await fetch(base + '/api/quarters/Q1%202026');
      q1 = await r.json();
      const tcs = q1.find((c) => c.name === 'TCS');
      assert(tcs.rec === 'Sell' && tcs.recConfirmed === true, `TCS override applied, got rec=${tcs.rec}`);

      // ---- funds ----
      r = await fetch(base + '/api/funds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          amc: 'ITI',
          fundName: 'ITI Test Fund',
          uploadDate: new Date().toISOString(),
          holdings: [
            { name: 'HDFC Bank Limited', pctAum: 5.09, matchedName: 'HDFC Bank', fundamental: 'Buy', technical: 'Hold' },
            { name: 'ICICI Bank Limited', pctAum: 3.74, matchedName: 'ICICI Bank', fundamental: 'Hold', technical: 'Buy' },
            { name: 'Some Unlisted Thing', pctAum: 1.5, matchedName: null, fundamental: null, technical: null },
          ],
        }),
      });
      assert(r.status === 201, 'fund created');
      const created = await r.json();

      r = await fetch(base + '/api/funds');
      const funds = await r.json();
      assert(funds.length === 1, `1 fund saved, got ${funds.length}`);
      const fund = funds[0];
      assert(fund.holdings.length === 3, 'fund has 3 holdings');
      assert(fund.holdings.find((h) => h.matchedName === null), 'unmatched holding stored with null matchedName, not guessed');
      assert(fund.fundamentalWeighted.buy > 50 && fund.fundamentalWeighted.buy < 60, `fundamental weighted buy% in range, got ${fund.fundamentalWeighted.buy}`);
      log('Fundamental weighted:', JSON.stringify(fund.fundamentalWeighted));

      // ---- cross-analysis ----
      r = await fetch(base + '/api/analysis/cross');
      const cross = await r.json();
      assert(cross.fundsAnalyzed === 1, 'cross analysis sees 1 fund');
      assert(cross.holdingsScanned === 3, 'cross analysis scanned 3 holdings');
      assert(cross.matched === 2, 'cross analysis: 2 matched');
      log('Cross analysis:', JSON.stringify(cross));

      // ---- most preferred ----
      r = await fetch(base + '/api/analysis/preferred');
      const preferred = await r.json();
      assert(preferred.stocks.length === 2, `2 ranked stocks, got ${preferred.stocks.length}`);
      const topStock = preferred.stocks[0];
      log('Top preferred stock:', JSON.stringify(topStock));
      assert(topStock.funds.length === 1 && topStock.funds[0].fundName === 'ITI Test Fund', 'drill-down shows contributing fund');

      // ---- delete fund ----
      r = await fetch(base + '/api/funds/' + created.id, { method: 'DELETE' });
      assert(r.status === 200, 'fund deleted');
      r = await fetch(base + '/api/funds');
      const fundsAfterDelete = await r.json();
      assert(fundsAfterDelete.length === 0, 'fund list empty after delete');

      // ---- CSV-shape sync (cat only, no rec) must not fabricate a "suggested" rec ----
      r = await fetch(base + '/api/quarters/Q3%202026/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([{ name: 'Wipro', cat: 'Slow Grower' }]),
      });
      assert(r.status === 200, 'CSV-shape sync succeeded');
      r = await fetch(base + '/api/quarters/Q3%202026');
      const q3 = await r.json();
      const wipro = q3.find((c) => c.name === 'Wipro');
      assert(wipro.cat === 'Slow Grower', `Wipro cat set via CSV-shape sync, got ${wipro.cat}`);
      assert(wipro.rec === '' && wipro.recSuggested === false, `Wipro rec untouched (no rec in payload), got rec=${wipro.rec} recSuggested=${wipro.recSuggested}`);

      log('\nALL BACKEND INTEGRATION CHECKS PASSED');
    } finally {
      server.kill();
    }
  } finally {
    await db.stop();
  }
})().catch(async (e) => {
  console.error('FAILURE:', e);
  process.exit(1);
});
