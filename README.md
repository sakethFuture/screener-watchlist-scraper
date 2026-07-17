# Screener.in Watchlist Scraper

Logs into your Screener.in Premium account, checks each stock in your watchlist for a
new quarterly result, and when one appears, computes YoY Sales/Net Profit growth and
classifies the stock as **Slow Grower** (<8%), **Stalwart** (8-15%), or **Fast Grower**
(15%+), based on the average of the two.

Runs as a single always-on service: a FastAPI app that serves `GET /output` (for a
browser dashboard) and internally schedules the scrape on a cron interval (default
daily). Everything - login session, per-stock "last seen result date" state, and the
output classifications - lives as JSON files under `DATA_DIR`.

## How it decides something changed

Each stock's screener.in page has a "Quarterly Results" table; the newest column's date
(e.g. `2026-06-30`) is compared against the last date we saw for that stock. A different
date means a new result was published. Growth is computed by comparing the new quarter's
Sales/Net Profit to the same quarter one year earlier (YoY, not quarter-over-quarter).

Notes/assumptions (easy to change in `growth.py` if you want it different):
- Classification bands are left-inclusive: `<8` Slow, `8-<15` Stalwart, `>=15` Fast.
  Negative growth currently falls into "Slow Grower" - the raw numbers are still in
  `output.json` so you can re-bucket a "Declining" tier later if you want one.
- If a stock has fewer than 5 quarters of history (e.g. recent IPO) or is missing the
  year-ago figure, growth can't be computed - the new result date is still recorded (so
  we don't reprocess it every run) but no output entry is written, and a warning is logged.
- On the very first run, every watchlist stock looks "new" (there's no prior state yet) -
  this is expected, it's what seeds `output.json` with an initial baseline for all stocks.
- If login fails (bad credentials, or screener.in changed its login page), a full-page
  screenshot is saved to `DATA_DIR/login_failure.png` before the error is raised - useful
  on Railway where there's no visible browser to watch. Pull it down with `railway run`
  or the dashboard's file browser.

## Local setup

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env   # fill in SCREENER_EMAIL / SCREENER_PASSWORD, etc.
```

Put your real watchlist at `data/watchlist.json` (or `.csv`) - see
`watchlist.example.json` / `watchlist.example.csv` for the expected shape. The slug
column must be the ticker screener.in uses in its URL (e.g. `TCS` for
screener.in/company/TCS/consolidated/) - accepted column names: `screener_slug`, `slug`,
`symbol`, `code`, `ticker` for the slug, and `name`, `stock_name`, `company`,
`company_name` for the display name.

Run one scrape pass directly (useful for testing, no server involved):

```bash
python scrape_job.py
```

Run the full service (API + internal scheduler) locally:

```bash
uvicorn app:app --reload
```

Then `curl http://localhost:8000/output` and `curl http://localhost:8000/health`.

First run: set `HEADLESS=false` in `.env` so you can watch the login happen and
confirm nothing unexpected (2FA prompt, layout change, etc.) is blocking it.

## Deploying to Railway

1. Create a new Railway service from this repo, build type **Dockerfile** (already
   configured via `railway.json`). Use the **Web Service** type, not Cron Job - the
   scrape schedule is handled internally by the app (APScheduler), not by Railway's own
   cron feature, because Railway doesn't support sharing a Volume between two separate
   services and this app needs the same Volume for both serving `/output` and running
   the scrape.
2. Attach a **Volume** to the service, mounted at `/app/data`.
3. Set environment variables in the Railway dashboard: `SCREENER_EMAIL`,
   `SCREENER_PASSWORD`, `DATA_DIR=/app/data`, `SCRAPE_CRON` (default `0 3 * * *`),
   `ALLOWED_ORIGINS` (comma-separated origins, or `*`), `RUN_NOW_TOKEN` (pick a random
   secret), `LOG_LEVEL=INFO`.
4. Copy your real watchlist file onto the volume once, e.g.:
   ```bash
   railway run --service <service-name> -- sh -c 'cat > /app/data/watchlist.json' < data/watchlist.json
   ```
   (or use `railway shell` / the Railway dashboard's file browser if available, then
   paste the file contents in).
5. Deploy. Railway will give the service a public URL - that's what a browser dashboard
   fetches `GET /<url>/output` from. `GET /health` is used for Railway's own healthcheck.
6. To trigger an out-of-schedule run: `curl -X POST https://<your-service>/run-now -H "X-Run-Token: <RUN_NOW_TOKEN>"`.

Logs (per-stock unchanged/new/error lines plus a `RUN SUMMARY: ...` line after each
scrape pass) show up directly in the Railway service's log viewer, since it's just
container stdout.

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI service: `/output`, `/health`, `/run-now`, owns the scheduler |
| `scrape_job.py` | `run_scrape_job()` - one full watchlist pass, callable directly or by the scheduler |
| `config.py` | Env-var driven settings |
| `auth.py` | Login, session persistence, mid-run re-login on expiry |
| `watchlist.py` | JSON/CSV watchlist loader |
| `scraper.py` | Playwright navigation + quarterly-results table parsing |
| `growth.py` | YoY growth math + classification (pure functions, no I/O) |
| `state_store.py` | Atomic JSON read/write + merge-update helper |
| `logging_setup.py` | stdout logging configuration |
