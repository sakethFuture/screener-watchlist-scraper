# Fund Analysis Platform — backend

Shared MySQL-backed API for the ledger + fund-factsheet analysis in
`docs/index.html`. Replaces per-browser localStorage/IndexedDB — every
visitor reads and writes through this API, so everyone sees the same data.

## Local verification (no cloud accounts needed)

Two scripts spin up a real, ephemeral MySQL instance (downloads a MySQL
binary on first run) and exercise the whole stack for real:

```
npm install
npm run verify       # migrate + seed + API, hit every route directly
npm run verify:e2e   # same, plus a real headless browser driving the real docs/index.html
```

Both are safe to re-run any time you change something — they tear the
ephemeral database down when done and touch nothing outside a temp DB.

## Deploying for real: Aiven (MySQL) + Fly.io (API)

You'll need to do the account-creation steps yourself (email verification,
ToS) — everything after that, I can drive for you if you hand me the
resulting credentials, or you can run these commands yourself.

### 1. Aiven MySQL (free tier)

1. Sign up at https://console.aiven.io (free tier available, no card required for the free plan as of writing).
2. Create a new service → **MySQL** → pick the free plan → any region close to your users (e.g. Mumbai/Singapore for India).
3. Once it's running, open the service's **Overview** tab and copy the **Service URI** — it looks like:
   `mysql://avnadmin:PASSWORD@mysql-xxxx.aivencloud.com:12345/defaultdb?ssl-mode=REQUIRED`
4. That whole string is your `DATABASE_URL`.

### 2. Fly.io (API)

1. Install flyctl: https://fly.io/docs/flyctl/install/ (on Windows: `iwr https://fly.io/install.ps1 -useb | iex` in PowerShell).
2. `fly auth signup` (or `fly auth login` if you already have an account).
3. From inside this `server/` directory:
   ```
   fly launch --no-deploy
   ```
   It'll detect the Dockerfile and ask to create an app — say yes, pick a
   name (must be globally unique), and the `sin` (Singapore) region or
   whichever is closest to you. **Say no** if it asks to set up a Postgres
   or Redis database — we're using Aiven MySQL instead.
4. Set your secrets (never commit these):
   ```
   fly secrets set DATABASE_URL="mysql://avnadmin:...@....aivencloud.com:12345/defaultdb?ssl-mode=REQUIRED"
   fly secrets set ALLOWED_ORIGINS="https://sakethfuture.github.io"
   ```
5. Deploy:
   ```
   fly deploy
   ```
6. Run the one-time migration + seed against the *production* database —
   easiest is to run them locally pointed at the production `DATABASE_URL`:
   ```
   set DATABASE_URL=mysql://avnadmin:...@....aivencloud.com:12345/defaultdb?ssl-mode=REQUIRED
   npm run migrate
   npm run seed
   ```
   (On Windows PowerShell: `$env:DATABASE_URL = "..."` instead of `set`.)
7. Confirm it's up: `curl https://YOUR-APP.fly.dev/health` should return `{"ok":true}`.

### 3. Point the frontend at it

In `docs/index.html`, find:
```js
const API_BASE = 'https://REPLACE-WITH-YOUR-FLY-APP.fly.dev';
```
and set it to your real Fly.io app URL, then commit + push. That's the
only frontend change needed to go from "not deployed yet" to "live for
everyone."

## Schema changes later

`src/schema.sql` uses `CREATE TABLE IF NOT EXISTS`, so `npm run migrate` is
always safe to re-run. For an actual schema change (new column, etc.), add
an `ALTER TABLE` statement to a new file rather than editing the existing
`CREATE TABLE` blocks (those won't re-run against a table that already
exists).
