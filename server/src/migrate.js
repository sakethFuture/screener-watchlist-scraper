// One-time (and idempotent - every statement is CREATE ... IF NOT EXISTS)
// schema setup. Run with: npm run migrate
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { getPool } = require('./db');

async function main() {
  const sql = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8');
  // Strip line comments before splitting, so a semicolon inside a comment
  // (there isn't one here, but be safe) never breaks the split.
  const statements = sql
    .split('\n')
    .filter((line) => !line.trim().startsWith('--'))
    .join('\n')
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean);

  const pool = getPool();
  for (const statement of statements) {
    console.log('Running:', statement.slice(0, 70).replace(/\s+/g, ' '), '...');
    await pool.query(statement);
  }
  console.log(`Done - ${statements.length} statement(s) applied.`);
  await pool.end();
}

main().catch((err) => {
  console.error('Migration failed:', err);
  process.exit(1);
});
