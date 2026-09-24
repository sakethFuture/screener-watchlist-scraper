const express = require('express');
const cors = require('cors');

const companiesRouter = require('./routes/companies');
const quartersRouter = require('./routes/quarters');
const overridesRouter = require('./routes/overrides');
const fundsRouter = require('./routes/funds');
const analysisRouter = require('./routes/analysis');

function createApp() {
  const app = express();

  const allowedOrigins = (process.env.ALLOWED_ORIGINS || '*')
    .split(',')
    .map((o) => o.trim())
    .filter(Boolean);
  app.use(
    cors({
      origin: allowedOrigins.includes('*') ? true : allowedOrigins,
    })
  );
  app.use(express.json({ limit: '2mb' }));

  app.get('/health', (req, res) => res.json({ ok: true }));

  app.use('/api/companies', companiesRouter);
  app.use('/api/quarters', quartersRouter);
  app.use('/api/overrides', overridesRouter);
  app.use('/api/funds', fundsRouter);
  app.use('/api/analysis', analysisRouter);

  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    console.error(err);
    res.status(500).json({ error: err.message || 'internal server error' });
  });

  return app;
}

module.exports = { createApp };
