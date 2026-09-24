const mysql = require('mysql2/promise');

let pool = null;

function getPool() {
  if (pool) return pool;

  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error('DATABASE_URL is not set - see .env.example');
  }

  // mysql2 accepts a connection-string URI directly. Aiven's MySQL requires
  // SSL; mysql2 honors the `ssl-mode=REQUIRED` query param for that. Local
  // dev / CI against a plaintext MySQL (no TLS listener at all) sets
  // DB_SSL=false to skip the SSL config entirely - passing an ssl object
  // to a server that never speaks TLS just hangs the connection.
  const sslDisabled = process.env.DB_SSL === 'false';
  pool = mysql.createPool({
    uri: url,
    waitForConnections: true,
    connectionLimit: 10,
    // Aiven (and most managed MySQL) present a cert not in Node's default
    // trust store unless you download their CA bundle - `rejectUnauthorized:
    // false` here trusts the connection without verifying that cert chain.
    // This still gets you TLS-in-transit encryption; it does NOT verify
    // you're talking to the real host over an active MITM. Fine for a
    // small internal tool; if that matters more later, download Aiven's
    // CA cert and pass `ca: fs.readFileSync(...)` instead.
    ssl: sslDisabled ? undefined : { rejectUnauthorized: false },
  });
  return pool;
}

async function query(sql, params) {
  const [rows] = await getPool().execute(sql, params);
  return rows;
}

async function withTransaction(fn) {
  const conn = await getPool().getConnection();
  try {
    await conn.beginTransaction();
    const result = await fn(conn);
    await conn.commit();
    return result;
  } catch (err) {
    await conn.rollback();
    throw err;
  } finally {
    conn.release();
  }
}

module.exports = { getPool, query, withTransaction };
