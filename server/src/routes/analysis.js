const express = require('express');
const { query } = require('../db');
const { weightedVerdict } = require('../weighted');

const router = express.Router();

async function fetchAllHoldingsWithSector() {
  return query(
    `SELECT fh.*, f.id AS fund_id_dup, f.fund_name, c.name AS matched_name, c.sector
     FROM fund_holdings fh
     JOIN funds f ON f.id = fh.fund_id
     LEFT JOIN companies c ON c.id = fh.matched_company_id`
  );
}

// GET /api/analysis/cross - aggregate weighted Buy/Hold/Sell across every
// stored fund, recalculated fresh on every request (never a static
// snapshot - it reflects whatever is in `funds` right now).
router.get('/cross', async (req, res, next) => {
  try {
    const [{ fundCount }] = await query('SELECT COUNT(*) AS fundCount FROM funds');
    const holdings = await fetchAllHoldingsWithSector();
    const totalHoldings = holdings.length;
    const totalMatched = holdings.filter((h) => h.matched_company_id).length;

    res.json({
      fundsAnalyzed: fundCount,
      holdingsScanned: totalHoldings,
      matched: totalMatched,
      unmatched: totalHoldings - totalMatched,
      fundamentalWeighted: weightedVerdict(holdings, 'fundamental'),
      technicalWeighted: weightedVerdict(holdings, 'technical'),
    });
  } catch (err) {
    next(err);
  }
});

// GET /api/analysis/preferred - stock and sector rankings by aggregate
// weighted % of AUM across all funds, each with its contributing
// funds/stocks for the click-to-drill-down view.
router.get('/preferred', async (req, res, next) => {
  try {
    const holdings = await fetchAllHoldingsWithSector();
    const matched = holdings.filter((h) => h.matched_company_id);

    const stockMap = new Map();
    for (const h of matched) {
      const key = h.matched_name;
      if (!stockMap.has(key)) stockMap.set(key, { name: key, totalPct: 0, funds: [] });
      const entry = stockMap.get(key);
      const pct = Number(h.pct_aum);
      entry.totalPct += pct;
      entry.funds.push({ fundId: h.fund_id, fundName: h.fund_name, pct });
    }
    const stocks = [...stockMap.values()].sort((a, b) => b.totalPct - a.totalPct);

    const sectorMap = new Map();
    for (const h of matched) {
      const sector = h.sector || 'Unclassified';
      if (!sectorMap.has(sector)) sectorMap.set(sector, { name: sector, totalPct: 0, stockTotals: new Map() });
      const entry = sectorMap.get(sector);
      const pct = Number(h.pct_aum);
      entry.totalPct += pct;
      entry.stockTotals.set(h.matched_name, (entry.stockTotals.get(h.matched_name) || 0) + pct);
    }
    const sectors = [...sectorMap.values()]
      .map((s) => ({
        name: s.name,
        totalPct: s.totalPct,
        stocks: [...s.stockTotals.entries()]
          .map(([name, totalPct]) => ({ name, totalPct }))
          .sort((a, b) => b.totalPct - a.totalPct),
      }))
      .sort((a, b) => b.totalPct - a.totalPct);

    res.json({ stocks, sectors });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
