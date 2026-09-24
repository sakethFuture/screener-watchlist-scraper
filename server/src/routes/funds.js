const express = require('express');
const { query, withTransaction } = require('../db');
const { weightedVerdict } = require('../weighted');

const router = express.Router();

function fundId() {
  return 'f' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

async function fetchHoldings(fundIds) {
  if (!fundIds.length) return [];
  const placeholders = fundIds.map(() => '?').join(',');
  return query(
    `SELECT fh.*, c.name AS matched_name
     FROM fund_holdings fh
     LEFT JOIN companies c ON c.id = fh.matched_company_id
     WHERE fh.fund_id IN (${placeholders})`,
    fundIds
  );
}

function holdingToApiShape(h) {
  return {
    name: h.name,
    pctAum: Number(h.pct_aum),
    matchedName: h.matched_name || null,
    fundamental: h.fundamental || null,
    technical: h.technical || null,
  };
}

// GET /api/funds - every saved fund, with weighted verdicts, newest first.
router.get('/', async (req, res, next) => {
  try {
    const funds = await query('SELECT * FROM funds ORDER BY upload_date DESC');
    if (!funds.length) return res.json([]);
    const holdings = await fetchHoldings(funds.map((f) => f.id));
    const byFund = new Map(funds.map((f) => [f.id, []]));
    holdings.forEach((h) => byFund.get(h.fund_id).push(h));

    res.json(
      funds.map((f) => {
        const hs = byFund.get(f.id) || [];
        return {
          id: f.id,
          amc: f.amc,
          fundName: f.fund_name,
          uploadDate: f.upload_date,
          holdings: hs.map(holdingToApiShape),
          fundamentalWeighted: weightedVerdict(hs, 'fundamental'),
          technicalWeighted: weightedVerdict(hs, 'technical'),
        };
      })
    );
  } catch (err) {
    next(err);
  }
});

// POST /api/funds - save a parsed fund. Matching against the watchlist
// happens client-side (same PDF never leaves the browser); this just
// resolves each holding's matchedName to a company id and stores the
// Fundamental/Technical snapshot the client already looked up.
router.post('/', async (req, res, next) => {
  try {
    const { amc, fundName, uploadDate, holdings } = req.body || {};
    if (!fundName || !Array.isArray(holdings)) {
      return res.status(400).json({ error: 'fundName and holdings[] are required' });
    }
    const id = fundId();

    await withTransaction(async (conn) => {
      await conn.execute(
        'INSERT INTO funds (id, amc, fund_name, upload_date) VALUES (?, ?, ?, ?)',
        [id, amc || '', fundName, uploadDate ? new Date(uploadDate) : new Date()]
      );
      for (const h of holdings) {
        let matchedCompanyId = null;
        if (h.matchedName) {
          const [rows] = await conn.execute('SELECT id FROM companies WHERE name = ?', [h.matchedName]);
          if (rows.length) matchedCompanyId = rows[0].id;
        }
        await conn.execute(
          'INSERT INTO fund_holdings (fund_id, name, pct_aum, matched_company_id, fundamental, technical) VALUES (?, ?, ?, ?, ?, ?)',
          [id, h.name, h.pctAum, matchedCompanyId, h.fundamental || null, h.technical || null]
        );
      }
    });

    res.status(201).json({ id });
  } catch (err) {
    next(err);
  }
});

// DELETE /api/funds/:id - cascades fund_holdings via FK.
router.delete('/:id', async (req, res, next) => {
  try {
    await query('DELETE FROM funds WHERE id = ?', [req.params.id]);
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
