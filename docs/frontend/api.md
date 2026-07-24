# frontend/api

Thin fetch wrapper plus the single `api` object listing every backend endpoint the frontend calls.

## frontend/src/api/client.js
JSON error bodies are surfaced via `err.detail`/`err.status`. Read endpoints are unauthenticated; operator-only endpoints (recipe approve/run/dry-run, digest generate/send) send an `X-Admin-Secret` header via `adminHeaders()` when an operator has unlocked (see `useAdmin`). The server-side cron endpoint uses a separate secret the browser never calls.

- `adminHeaders() -> object` — returns `{ 'X-Admin-Secret': <secret> }` from `localStorage` when set, else `{}` (storage errors swallowed).
- `request(path, options = {}) -> Promise<any>` — wraps `fetch`, throws an `Error` (with `.status` and message from the response's `detail` field when present) on non-OK responses, otherwise resolves the parsed JSON body.
- `api.overview() -> Promise` — `GET /api/overview`.
- `api.candidates(cohort = 'discovery') -> Promise` — `GET /api/candidates?cohort=<cohort>`.
- `api.candidate(id) -> Promise` — `GET /api/candidates/:id`.
- `api.backtest() -> Promise` — `GET /api/backtest`.
- `api.latestDigest() -> Promise` — `GET /api/digests/latest`.
- `api.upcomingDigest(offset = 0) -> Promise` — `GET /api/digest/upcoming?offset=<offset>` (the digest-lineup preview + auto-send status; `offset` paginates to a fresh batch, using the prior response's `next_offset`).
- `api.digestSettings() -> Promise` — `GET /api/digest/settings` (current `min_score`).
- `api.updateDigestSettings(payload) -> Promise` — `PUT /api/digest/settings` (public, no admin header) with a JSON body `{ min_score }`.
- `api.generateDigest() -> Promise` — `POST /api/digests/generate` (sends `X-Admin-Secret`).
- `api.sendDigest() -> Promise` — `POST /api/digests/send` (sends `X-Admin-Secret`).
- `api.subscribe(payload) -> Promise` — `POST /api/subscribers` with a JSON body.
- `api.pageView(payload) -> Promise` — `POST /api/analytics/page-view` with a JSON body.
- `api.discoveryRecipes() -> Promise` — `GET /api/discovery/recipes`.
- `api.runRecipe(id, limit) -> Promise` — `POST /api/discovery/recipes/:id/run` (sends `X-Admin-Secret`), with `?limit=` appended when `limit` is truthy.
- `api.dryRunRecipe(id, limit) -> Promise` — `POST /api/discovery/recipes/:id/dry-run` (sends `X-Admin-Secret`), with `?limit=` appended when `limit` is truthy.
- `api.approveRecipe(id) -> Promise` — `POST /api/discovery/recipes/:id/approve` (sends `X-Admin-Secret`).
- `api.discoveryCostSummary() -> Promise` — `GET /api/discovery/cost-summary`.
- `api.reviewCandidate(id, payload) -> Promise` — `PUT /api/candidate-reviews/:id` with a JSON body (at minimum `{ state }`).
