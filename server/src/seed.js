// One-time data migration: ports the dataset currently baked into
// docs/index.html (SEED, SECTOR_MAP, Q1_2026_CLASSIFICATIONS,
// Q1_2026_REC_SUGGESTIONS) plus data/overrides.json into the new database,
// so a fresh DB starts from exactly what every browser already shows today
// - no surprise data changes beyond what's already landed in git.
//
// Safe to re-run: companies are upserted by name, quarterly_data by
// (company_id, quarter), and overrides that already exist (same company/
// quarter/field/corrected_value) are skipped.
//
// Usage: npm run seed
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { getPool, withTransaction } = require('./db');

function extractJsConst(html, name) {
  const re = new RegExp(`const ${name}\\s*=\\s*(\\{[\\s\\S]*?\\}|\\[[\\s\\S]*?\\]);`);
  const m = html.match(re);
  if (!m) throw new Error(`Could not find ${name} in docs/index.html`);
  // These constants are plain JSON-compatible object/array literals in the
  // source (double-quoted keys/strings) - safe to JSON.parse directly.
  return JSON.parse(m[1]);
}

async function main() {
  const htmlPath = path.join(__dirname, '..', '..', 'docs', 'index.html');
  const html = fs.readFileSync(htmlPath, 'utf8');

  const seed = extractJsConst(html, 'SEED');
  const sectorMap = extractJsConst(html, 'SECTOR_MAP');
  const q1Classifications = extractJsConst(html, 'Q1_2026_CLASSIFICATIONS');
  const q1RecSuggestions = extractJsConst(html, 'Q1_2026_REC_SUGGESTIONS');

  const overridesPath = path.join(__dirname, '..', '..', 'data', 'overrides.json');
  const overrides = fs.existsSync(overridesPath) ? JSON.parse(fs.readFileSync(overridesPath, 'utf8')) : [];

  console.log(`Seeding ${seed.length} companies, ${overrides.length} override row(s)...`);

  await withTransaction(async (conn) => {
    const nameToId = new Map();

    for (const row of seed) {
      const sector = sectorMap[row.name] || 'Unclassified';
      const [result] = await conn.execute(
        `INSERT INTO companies (name, sector) VALUES (?, ?)
         ON DUPLICATE KEY UPDATE sector = VALUES(sector)`,
        [row.name, sector]
      );
      let id = result.insertId;
      if (!id) {
        const [rows] = await conn.execute('SELECT id FROM companies WHERE name = ?', [row.name]);
        id = rows[0].id;
      }
      nameToId.set(row.name, id);

      const cat = q1Classifications[row.name] || '';
      const rec = q1RecSuggestions[row.name] || '';
      await conn.execute(
        `INSERT INTO quarterly_data (company_id, quarter, cat, rec, rec_suggested)
         VALUES (?, 'Q1 2026', ?, ?, ?)
         ON DUPLICATE KEY UPDATE
           cat = IF(cat_confirmed, cat, VALUES(cat)),
           rec = IF(rec_confirmed, rec, VALUES(rec)),
           rec_suggested = IF(rec_confirmed, rec_suggested, VALUES(rec_suggested))`,
        [id, cat, rec, !!rec]
      );
    }

    const fieldToColumn = { cat: 'cat', rec: 'rec', techRec: 'tech_rec' };
    const fieldToConfirmedColumn = { cat: 'cat_confirmed', rec: 'rec_confirmed', techRec: 'tech_rec_confirmed' };

    for (const o of overrides) {
      const [existing] = await conn.execute(
        'SELECT id FROM overrides WHERE company = ? AND quarter = ? AND field = ? AND corrected_value = ?',
        [o.company, o.quarter, o.field, o.corrected_value]
      );
      if (existing.length) continue; // already seeded

      const [insertResult] = await conn.execute(
        'INSERT INTO overrides (company, quarter, field, corrected_value, confirmed) VALUES (?, ?, ?, ?, ?)',
        [o.company, o.quarter, o.field, o.corrected_value, o.confirmed ? 1 : 0]
      );

      const companyId = nameToId.get(o.company);
      const column = fieldToColumn[o.field];
      const confirmedColumn = fieldToConfirmedColumn[o.field];
      if (!companyId || !column || !o.confirmed) continue;

      await conn.execute(
        `INSERT INTO quarterly_data (company_id, quarter, ${column}, ${confirmedColumn})
         VALUES (?, ?, ?, 1)
         ON DUPLICATE KEY UPDATE ${column} = VALUES(${column}), ${confirmedColumn} = 1${o.field === 'rec' ? ', rec_suggested = 0' : ''}`,
        [companyId, o.quarter, o.corrected_value]
      );
      await conn.execute('UPDATE overrides SET applied = 1, applied_at = NOW() WHERE id = ?', [insertResult.insertId]);
    }
  });

  console.log('Seed complete.');
  await getPool().end();
}

main().catch((err) => {
  console.error('Seed failed:', err);
  process.exit(1);
});
