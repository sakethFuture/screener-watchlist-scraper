const express = require('express');
const { query, withTransaction } = require('../db');

const router = express.Router();

const CAT_VALUES = new Set(['Slow Grower', 'Stalwart', 'Fast Grower', 'Cyclical', 'Turnaround', 'Asset Play', '']);
const REC_VALUES = new Set(['Buy', 'Hold', 'Sell', '']);

function rowToApiShape(row) {
  return {
    id: row.id,
    name: row.name,
    sector: row.sector,
    notes: row.notes,
    cat: row.cat || '',
    cat2: row.cat2 || '',
    cat2Reason: row.cat2_reason || '',
    rec: row.rec || '',
    recSuggested: !!row.rec_suggested,
    catConfirmed: !!row.cat_confirmed,
    recConfirmed: !!row.rec_confirmed,
    techRec: row.tech_rec || '',
    techRecConfirmed: !!row.tech_rec_confirmed,
  };
}

// GET /api/quarters/:quarter - every company's ledger row for this quarter,
// defaulted to empty for any company with no quarterly_data row yet.
router.get('/:quarter', async (req, res, next) => {
  try {
    const rows = await query(
      `SELECT c.id, c.name, c.sector, c.notes,
              q.cat, q.cat2, q.cat2_reason, q.rec, q.rec_suggested,
              q.cat_confirmed, q.rec_confirmed, q.tech_rec, q.tech_rec_confirmed
       FROM companies c
       LEFT JOIN quarterly_data q ON q.company_id = c.id AND q.quarter = ?
       ORDER BY c.name`,
      [req.params.quarter]
    );
    res.json(rows.map(rowToApiShape));
  } catch (err) {
    next(err);
  }
});

// PATCH /api/quarters/:companyId/:quarter - upsert one field or several.
// Confirmed fields (cat_confirmed/rec_confirmed/tech_rec_confirmed) are
// only ever set by the overrides route - a direct ledger edit through this
// endpoint never sets them, but it's also never blocked by them: a person
// editing the table by hand is always allowed to change the value (the
// confirmed lock only stops Sync/Autofill from silently re-clobbering it).
router.patch('/:companyId/:quarter', async (req, res, next) => {
  try {
    const { cat, cat2, cat2Reason, rec, recSuggested, techRec } = req.body || {};
    if (cat !== undefined && !CAT_VALUES.has(cat)) return res.status(400).json({ error: 'invalid cat' });
    if (rec !== undefined && !REC_VALUES.has(rec)) return res.status(400).json({ error: 'invalid rec' });
    if (techRec !== undefined && !REC_VALUES.has(techRec)) return res.status(400).json({ error: 'invalid techRec' });

    const { companyId, quarter } = req.params;
    // mysql2 rejects `undefined` bind params outright (it wants explicit
    // SQL NULL) - every field not present in the request body must become
    // `null` here, both for the INSERT defaults and the COALESCE-on-update
    // params, so an unrelated field can never accidentally get overwritten.
    const recSuggestedVal = recSuggested === undefined ? null : (recSuggested ? 1 : 0);
    await query(
      `INSERT INTO quarterly_data (company_id, quarter, cat, cat2, cat2_reason, rec, rec_suggested, tech_rec)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE
         cat = COALESCE(?, cat), cat2 = COALESCE(?, cat2), cat2_reason = COALESCE(?, cat2_reason),
         rec = COALESCE(?, rec), rec_suggested = COALESCE(?, rec_suggested), tech_rec = COALESCE(?, tech_rec)`,
      [
        companyId, quarter, cat ?? '', cat2 ?? '', cat2Reason ?? '', rec ?? '', recSuggestedVal ?? 0, techRec ?? '',
        cat ?? null, cat2 ?? null, cat2Reason ?? null, rec ?? null, recSuggestedVal, techRec ?? null,
      ]
    );
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

// POST /api/quarters/:quarter/sync - bulk-apply scraper output (the shape
// of data/output.json's values) into this quarter. Mirrors the old
// client-side runSync(), now enforced centrally so cat_confirmed/
// rec_confirmed guards can never be bypassed by a stale frontend.
router.post('/:quarter/sync', async (req, res, next) => {
  try {
    const entries = Array.isArray(req.body) ? req.body : Object.values(req.body || {});
    const quarter = req.params.quarter;
    let updated = 0;
    const unmatched = [];

    await withTransaction(async (conn) => {
      for (const entry of entries) {
        if (!entry || !entry.name) continue;
        const [companies] = await conn.execute('SELECT id FROM companies WHERE name = ?', [entry.name]);
        if (!companies.length) { unmatched.push(entry.name); continue; }
        const companyId = companies[0].id;

        const [existing] = await conn.execute(
          'SELECT cat_confirmed, rec_confirmed FROM quarterly_data WHERE company_id = ? AND quarter = ?',
          [companyId, quarter]
        );
        const catConfirmed = existing.length ? !!existing[0].cat_confirmed : false;
        const recConfirmed = existing.length ? !!existing[0].rec_confirmed : false;

        // Accepts either the scraper's output.json shape (classification/
        // recommendation) or a plain {cat, rec} shape - the same guarded
        // bulk-update path is reused for GitHub Sync, the Screener CSV
        // importer, and the manual paste-a-few-rows Autofill box, since
        // all three are "external/rule-derived" sources that a confirmed
        // override must always outrank.
        const rawCat = entry.classification ?? entry.cat;
        const rawRec = entry.recommendation ?? entry.rec;
        const cat = CAT_VALUES.has(rawCat) ? rawCat : null;
        const rec = REC_VALUES.has(rawRec) ? rawRec : null;
        const tieOthers = Array.isArray(entry.category_tie) ? entry.category_tie.slice(1) : [];
        const secondaryCat = entry.secondary_category || null;
        const cat2 = tieOthers[0] || secondaryCat || null;
        const cat2Reason = tieOthers[0] ? 'tie' : (secondaryCat ? 'secondary' : null);

        await conn.execute(
          `INSERT INTO quarterly_data (company_id, quarter, cat, cat2, cat2_reason, rec, rec_suggested)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
             cat = IF(cat_confirmed, cat, COALESCE(?, cat)),
             cat2 = IF(cat_confirmed, cat2, COALESCE(?, cat2)),
             cat2_reason = IF(cat_confirmed, cat2_reason, COALESCE(?, cat2_reason)),
             rec = IF(rec_confirmed, rec, COALESCE(?, rec)),
             rec_suggested = IF(rec_confirmed, rec_suggested, IF(? IS NULL, rec_suggested, 1))`,
          [companyId, quarter, cat, cat2, cat2Reason, rec, rec !== null ? 1 : 0, cat, cat2, cat2Reason, rec, rec]
        );
        if (!catConfirmed || !recConfirmed) updated++;
      }
    });

    res.json({ updated, unmatched });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
