const express = require('express');
const { query, withTransaction } = require('../db');

const router = express.Router();

const FIELD_TO_COLUMN = { cat: 'cat', rec: 'rec', techRec: 'tech_rec' };
const FIELD_TO_CONFIRMED_COLUMN = { cat: 'cat_confirmed', rec: 'rec_confirmed', techRec: 'tech_rec_confirmed' };

async function applyOverrideRow(conn, row) {
  const column = FIELD_TO_COLUMN[row.field];
  const confirmedColumn = FIELD_TO_CONFIRMED_COLUMN[row.field];
  if (!column) return false;

  const [companies] = await conn.execute('SELECT id FROM companies WHERE name = ?', [row.company]);
  if (!companies.length) return false;
  const companyId = companies[0].id;

  await conn.execute(
    `INSERT INTO quarterly_data (company_id, quarter, ${column}, ${confirmedColumn})
     VALUES (?, ?, ?, 1)
     ON DUPLICATE KEY UPDATE ${column} = VALUES(${column}), ${confirmedColumn} = 1${row.field === 'rec' ? ', rec_suggested = 0' : ''}`,
    [companyId, row.quarter, row.corrected_value]
  );
  await conn.execute('UPDATE overrides SET applied = 1, applied_at = NOW() WHERE id = ?', [row.id]);
  return true;
}

// GET /api/overrides - full audit log.
router.get('/', async (req, res, next) => {
  try {
    const rows = await query('SELECT * FROM overrides ORDER BY created_at DESC');
    res.json(rows);
  } catch (err) {
    next(err);
  }
});

// POST /api/overrides - insert a new confirmed correction and apply it
// immediately. This is the durable mechanism from Priority 5: a future
// correction is a POST here, never a code change.
router.post('/', async (req, res, next) => {
  try {
    const { company, quarter, field, corrected_value, confirmed } = req.body || {};
    if (!company || !quarter || !field || corrected_value === undefined) {
      return res.status(400).json({ error: 'company, quarter, field, corrected_value are required' });
    }
    if (!FIELD_TO_COLUMN[field]) {
      return res.status(400).json({ error: `field must be one of: ${Object.keys(FIELD_TO_COLUMN).join(', ')}` });
    }

    const result = await withTransaction(async (conn) => {
      const [insertResult] = await conn.execute(
        'INSERT INTO overrides (company, quarter, field, corrected_value, confirmed) VALUES (?, ?, ?, ?, ?)',
        [company, quarter, field, corrected_value, confirmed === false ? 0 : 1]
      );
      const row = { id: insertResult.insertId, company, quarter, field, corrected_value };
      const applied = confirmed === false ? false : await applyOverrideRow(conn, row);
      return { id: insertResult.insertId, applied };
    });

    res.status(201).json(result);
  } catch (err) {
    next(err);
  }
});

// POST /api/overrides/apply-pending - re-scan for any unapplied confirmed
// rows and apply them. Useful after a bulk insert straight into the table
// (e.g. during a data migration) that bypassed the POST / route above.
router.post('/apply-pending', async (req, res, next) => {
  try {
    const pending = await query('SELECT * FROM overrides WHERE confirmed = 1 AND applied = 0');
    let appliedCount = 0;
    await withTransaction(async (conn) => {
      for (const row of pending) {
        if (await applyOverrideRow(conn, row)) appliedCount++;
      }
    });
    res.json({ appliedCount, scanned: pending.length });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
