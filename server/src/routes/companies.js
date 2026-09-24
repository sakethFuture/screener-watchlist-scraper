const express = require('express');
const { query } = require('../db');

const router = express.Router();

// GET /api/companies - every company, independent of quarter data.
router.get('/', async (req, res, next) => {
  try {
    const rows = await query('SELECT id, name, sector, notes FROM companies ORDER BY name');
    res.json(rows);
  } catch (err) {
    next(err);
  }
});

// POST /api/companies - add a new company (the ledger's "+ Add stock").
router.post('/', async (req, res, next) => {
  try {
    const { name, sector, notes } = req.body || {};
    if (!name || !name.trim()) {
      return res.status(400).json({ error: 'name is required' });
    }
    const result = await query(
      'INSERT INTO companies (name, sector, notes) VALUES (?, ?, ?)',
      [name.trim(), sector || 'Unclassified', notes || '']
    );
    res.status(201).json({ id: result.insertId, name: name.trim(), sector: sector || 'Unclassified', notes: notes || '' });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(409).json({ error: 'a company with that name already exists' });
    }
    next(err);
  }
});

// PATCH /api/companies/:id - rename / re-sector / edit notes.
router.patch('/:id', async (req, res, next) => {
  try {
    const { name, sector, notes } = req.body || {};
    const fields = [];
    const params = [];
    if (name !== undefined) { fields.push('name = ?'); params.push(name); }
    if (sector !== undefined) { fields.push('sector = ?'); params.push(sector); }
    if (notes !== undefined) { fields.push('notes = ?'); params.push(notes); }
    if (!fields.length) return res.status(400).json({ error: 'nothing to update' });
    params.push(req.params.id);
    await query(`UPDATE companies SET ${fields.join(', ')} WHERE id = ?`, params);
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

// DELETE /api/companies/:id - cascades quarterly_data via FK.
router.delete('/:id', async (req, res, next) => {
  try {
    await query('DELETE FROM companies WHERE id = ?', [req.params.id]);
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
