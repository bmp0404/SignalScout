# Deploying Signal Scout to Railway

Railway is like Vercel but for long-running servers: instead of serverless functions it runs
your Dockerfile as an always-on process, and Postgres + cron live in the same project.
One service serves both the API and the built frontend, so there is one public URL and no CORS.

## 0. Prerequisites

- The repo pushed to GitHub (`ali8hsn/signalScout`).
- A [railway.com](https://railway.com) account (sign in with GitHub — free trial is enough to start).
- The Railway CLI for the one-off migration step: `npm i -g @railway/cli` (or `brew install railway`).

## 1. Test the container locally (optional but recommended)

```bash
docker build -t signal-scout .
docker run --rm -p 8000:8000 -v "$PWD/signal_scout.db:/app/signal_scout.db" signal-scout
# In another terminal:
curl http://localhost:8000/api/health     # → {"status":"ok","db":"sqlite"}
open http://localhost:8000                # the built frontend
```

(The `-v` mount gives the container your local SQLite DB; in production Postgres is used instead.)

## 2. Create the project from the GitHub repo

1. Go to [railway.com/new](https://railway.com/new) → click **Deploy from GitHub repo**.
2. Authorize Railway's GitHub app if prompted, then pick **ali8hsn/signalScout**.
3. Click **Deploy now**. Railway detects the `Dockerfile` automatically. On each start,
   `scripts/build_db.py --if-empty` initializes the backtest seed set only if `persons` is empty.
   It never resets a migrated database or removes real discoveries.
4. The first deploy can build before Postgres exists. Add Postgres before sharing the URL.

## 3. Add the Postgres plugin

1. Return to the project canvas and click **+ Create** → **Database** → **Add PostgreSQL**.
2. A `Postgres` service appears next to your app service. Nothing else to configure.

## 4. Set environment variables

1. Click your **app service** (not Postgres) → **Variables** tab → **+ New Variable**.
2. Click **Add Reference**, choose the **Postgres** service, and select `DATABASE_URL`. Confirm the
   app variable is named `DATABASE_URL` and displays `${{Postgres.DATABASE_URL}}`.
3. Add the rest from the exact variable checklist at the end of this document.
   Set `PUBLIC_BASE_URL` to the generated Railway origin, for example
   `https://signalscout-production.up.railway.app` (no trailing slash).
   - `SIGNAL_SCOUT_DB` is **not** needed on Railway (Postgres is used when `DATABASE_URL` is set).
4. Click **Deploy** on the banner that appears — variable changes trigger a redeploy.

## 5. Run the data migration

This copies every table (founders, discoveries, signals, edges, digests) from your local
`signal_scout.db` into Railway's Postgres. Run it from the repo root on your machine:

```bash
railway login
railway link          # choose the Signal Scout project, production environment, and app service
railway run --service <app-service-name> python scripts/migrate_sqlite_to_postgres.py
```

`railway run` executes the command locally with the service's environment variables injected,
so it reads your local SQLite file and writes to the hosted Postgres.

Preview what would be copied without touching Postgres:

```bash
python scripts/migrate_sqlite_to_postgres.py --dry-run
```

The migration is transactional: it replaces the destination tables with the complete local
SQLite dataset, including all real discoveries and graph data, verifies each row count, and
rolls back if any table fails. Run it only from the canonical `signal_scout.db`. Later app
restarts see a non-empty database and leave it unchanged.

> If `DATABASE_URL` in the service references the plugin's *private* network URL and the
> migration can't connect from your laptop, copy the **public** connection string instead:
> Postgres service → **Connect** tab → *Public network* → run
> `DATABASE_URL='<that url>' python scripts/migrate_sqlite_to_postgres.py`.

## 6. Generate a public domain

1. App service → **Settings** tab → **Networking** section → **Generate Domain**.
2. When asked for the port, enter **8000** (the Dockerfile default; Railway also injects `$PORT`).
3. Click **Generate Domain**. Copy the URL, such as
   `https://signalscout-production.up.railway.app`.

## 7. Verify

```bash
curl https://<your-domain>/api/health     # → {"status":"ok","db":"postgres"}
curl https://<your-domain>/api/overview   # → backtest stats + discovery counts
```

Open the root URL in a private browser and confirm the public Cory-facing Discover UI loads only
the reviewed launch cohort without any operator controls.

## 8. Configure Resend and the daily digest cron

1. In Resend, verify the domain used by `DIGEST_FROM_EMAIL`, create an API key, and add both
   values to the app service. Signal Scout sends HTML and plain text through Resend's supported
   Email API; open tracking is not a per-email request field. Resend disables tracking by
   default: in **Domains → your domain → Tracking**, add a tracking subdomain, publish its CNAME,
   wait for verification, and explicitly enable **Open tracking**. See
   [Resend tracking setup](https://resend.com/docs/dashboard/domains/tracking).
   With either email value missing, Signal Scout safely renders previews and records no sends.
2. Railway cron jobs execute commands. Project canvas → **+ Create** → **Empty Service**, name it
   `digest-cron`, connect it to the same GitHub repo, and copy the app service variables
   (`DATABASE_URL`, `RESEND_API_KEY`, `DIGEST_FROM_EMAIL`, and `PUBLIC_BASE_URL`).
3. Service **Settings** → set its start command to `python scripts/run_digest_cron.py`.
4. Set **Cron Schedule** to `0 15 * * *` (15:00 UTC = 8:00 AM PDT). Railway schedules in UTC
   and does not follow DST, so this runs at 7:00 AM PST in winter; use `0 16 * * *` then if
   8 AM local delivery matters. The command sends daily subscriptions every run and weekly
   subscriptions only on Monday.

The always-on app also exposes an equivalent endpoint protected by the exact header
`Authorization: Bearer $CRON_SECRET`. Use dry-run first; it renders the selected subscriber's
HTML/plain-text preview but does not call Resend or consume candidates:

```bash
curl -X POST \
  -H "Authorization: Bearer $CRON_SECRET" \
  "https://<your-domain>/api/digest/cron?dry_run=true&recipient=you@example.com"

# Real manual run for one active subscriber:
curl -X POST \
  -H "Authorization: Bearer $CRON_SECRET" \
  "https://<your-domain>/api/digest/cron?recipient=you@example.com"
```

For a local exact preview, call `GET /api/digest/preview?email=you@example.com`
(no auth required — single-operator product).

## 9. Background discovery (keep Discover populated)

Discovery recipes run automatically — you should not need to click RUN on Pipeline
just to have people appear. Manual RUN is only for pulling extra people outside the
normal cadence.

**In-process (always-on app):** on startup the API starts a background ticker
(`DISCOVERY_BACKGROUND=1` by default) that every
`DISCOVERY_BACKGROUND_INTERVAL_HOURS` (default `6`) calls `run_due()`. Each recipe
still only spends credits when its own `weekly` / `biweekly` window has elapsed
since `last_run`. Seeded recipes are auto-approved so the ticker can run them;
pause a recipe in the DB (`status=paused`) or set `DISCOVERY_BACKGROUND=0` to stop.

**Railway cron (recommended backup):** Project canvas → **+ Create** → **Empty Service**,
name it `discovery-cron`, same repo + `DATABASE_URL` (and provider keys). Start command:

```bash
python scripts/run_discovery_cron.py
```

Cron schedule example: `0 16 * * *` (daily UTC tick; recipes that are not due no-op).

HTTP equivalent (Bearer `CRON_SECRET`):

```bash
curl -X POST \
  -H "Authorization: Bearer $CRON_SECRET" \
  "https://<your-domain>/api/discovery/cron"
```

Without `PDL_API_KEY` / `CORESIGNAL_API_KEY` / `EXA_API_KEY`, due recipes for that provider no-op (no crash, no people).

## Troubleshooting

- **Build fails on `npm ci`** — make sure `frontend/package-lock.json` is committed.
- **`/api/health` returns 500** — check `DATABASE_URL` is a valid reference (Variables tab shows
  the resolved value); the app falls back to SQLite (empty in the container) only when it's unset.
- **Frontend 404s** — the container serves `frontend/dist` built during the Docker build; check the
  build logs' frontend stage. API routes always win because they're mounted before the static files.
- **Logs** — service → **Deployments** → click the active deployment → **View Logs**
  (equivalent of `vercel logs`).

## Final environment and key checklist

Required for hosted storage and links:

- `DATABASE_URL` — Postgres connection used by every repository. Railway provides it: app
  **Variables → Add Reference → Postgres → DATABASE_URL**; do not paste it into source control.
- `PUBLIC_BASE_URL` — generated Railway HTTPS origin used in feedback/unsubscribe links.
- `CRON_SECRET` — protects the manual cron endpoint. Generate locally with
  `openssl rand -hex 32`, then store only the output in Railway Variables.
- `APP_ENV=production` — enables fail-closed cron configuration.
- `OWNER_TEST_EMAIL` — explicit owner-only address permitted for production test sends.

Required for live discovery:

- `GITHUB_TOKEN` — authenticates GitHub public-data API calls and raises rate limits. Create a
  token at [GitHub token settings](https://github.com/settings/tokens); grant only the minimum
  read access needed for public repositories and organizations.
- `DISCOVERY_SEED_LIMIT` — optional number of seed accounts per run; default `4`.
- `DISCOVERY_MAX_PER_SEED` — optional expansion cap per seed; default `30`.

Licensed enrichment runs a PDL-first, Coresignal-fallback chain plus an
independent provider-search discovery lane. A provider is used whenever its key
is present; missing keys degrade to a no-op.

- `ENRICHMENT_PROVIDER` — legacy single-provider hint (`pdl`/`coresignal`); the
  chain is PDL-first regardless. Default `pdl`.
- `PDL_API_KEY` — People Data Labs key (primary enricher + lead search lane).
  Obtain it from the [PDL dashboard](https://dashboard.peopledatalabs.com/).
- `CORESIGNAL_API_KEY` — Coresignal employee API key (independent search + PDL
  no-match fallback). [Coresignal self-service](https://dashboard.coresignal.com/sign-up).
- `EXA_API_KEY` — Exa AI key (https://exa.ai) for the semantic web people-search
  lead lane (search-only; PDL still does one-person enrichment). Missing key ->
  Exa recipes no-op. `EXA_DAILY_CAP` sets its separate daily record cap (default `20`).
- `PDL_MONTHLY_CAP` — PDL lookups/month (free tier ~`100`); default `100`.
- `PDL_SEARCH_SPLIT` — fraction of the PDL monthly cap reserved for the
  provider-search lane (search-first); default `0.7`.
- `PROVIDER_PER_RUN_CAP` — max fresh provider lookups per run/process; default `100`.
- `CORESIGNAL_DAILY_CAP` — Coresignal's separate daily cap (search + fallback);
  default `20`.
- `DAILY_ENRICHMENT_BUDGET` — legacy global daily counter, superseded by the
  provider-scoped budgets above; default `100`.

Required for real email delivery:

- `RESEND_API_KEY` — sends subscriber digests. Create it in
  [Resend API Keys](https://resend.com/api-keys).
- `DIGEST_FROM_EMAIL` — sender on a verified Resend domain, for example
  `Signal Scout <digest@example.com>`. Set up the domain in
  [Resend Domains](https://resend.com/domains).

`SIGNAL_SCOUT_DB` is local-only and must not be set on Railway. Do not set provider keys for this
release: provider allocations are exhausted. Missing email keys keep delivery preview-only.

## Five-minute pre-Cory QA

1. **0:00–0:30 — service/data:** open `/api/health` and confirm
   `{"status":"ok","db":"postgres"}`; open `/api/overview` and confirm discovery counts are
   non-zero and the backtest reads 70.0% recall, 16.9 months lead, and 1.7% false positives.
2. **0:30–1:30 — Discover:** open the root URL on desktop and a narrow mobile viewport. Confirm
   the exact line “Finding exceptional people before the world knows their names,” all three
   tabs, ranked real people, visible signal summaries, and a prominent digest signup card.
3. **1:30–2:15 — evidence/pipeline:** open one evidence receipt and one profile link. If
   `GITHUB_TOKEN` is configured, start one real discovery run and confirm live stage progress;
   otherwise confirm the UI gives the friendly token instruction. Do not wait on a staged demo.
4. **2:15–3:00 — Backtest:** open Backtest and confirm the headline, four metrics, chart, and
   founder table render without overflow or raw errors.
5. **3:00–4:00 — signup/digest:** subscribe a controlled address, reload Digest, generate a
   preview, then run the documented authenticated `dry_run=true` curl for that address. Confirm
   exact signal lines and feedback/unsubscribe links; do not run a real send during QA.
6. **4:00–4:30 — analytics/privacy:** switch through all three tabs, then check Postgres
   `page_views` rows contain only `id`, `path`, `viewed_at`, and optional `referrer`.
7. **4:30–5:00 — final checks:** confirm Resend's tracking subdomain is verified and Open
   tracking is enabled, Railway logs have no current errors, and no secret appears in the
   public page or repository. Only then copy the Railway URL for Cory.
