# frontend/api

Thin fetch wrapper plus the single `api` object listing every backend endpoint the frontend calls.

## frontend/src/api/client.js
JSON error bodies are surfaced via `err.detail`/`err.status`. Operator-only pages (`DiscoveryAdmin`) authenticate by calling `setOperatorToken` once with an operator-entered secret; every subsequent request automatically carries it as a Bearer header — this is additive (empty header when unset) so it doesn't change behavior for existing unauthenticated calls.

- `operatorToken` — module-level string, empty by default.
- `setOperatorToken(token)` — sets `operatorToken`, called by `DiscoveryAdmin`'s unlock form.
- `authHeaders() -> object` — returns `{ Authorization: 'Bearer <operatorToken>' }` when `operatorToken` is set, else `{}`.
- `request(path, options = {}) -> Promise<any>` — wraps `fetch`, merging `authHeaders()` into every request's headers (caller-supplied headers win on conflict), throws an `Error` (with `.status` and message from the response's `detail` field when present) on non-OK responses, otherwise resolves the parsed JSON body.
- `api.overview() -> Promise` — `GET /api/overview`.
- `api.candidates(cohort = 'discovery') -> Promise` — `GET /api/candidates?cohort=<cohort>`.
- `api.candidate(id) -> Promise` — `GET /api/candidates/:id`.
- `api.backtest() -> Promise` — `GET /api/backtest`.
- `api.concentrations() -> Promise` — `GET /api/concentrations`.
- `api.latestDigest() -> Promise` — `GET /api/digests/latest`.
- `api.generateDigest() -> Promise` — `POST /api/digests/generate`.
- `api.sendDigest() -> Promise` — `POST /api/digests/send`.
- `api.subscribe(payload) -> Promise` — `POST /api/subscribers` with a JSON body.
- `api.sendTestDigest(payload) -> Promise` — `POST /api/digest/test` with a JSON body.
- `api.pageView(payload) -> Promise` — `POST /api/analytics/page-view` with a JSON body.
- `api.runDiscovery() -> Promise` — `POST /api/discovery/run`.
- `api.discoveryStatus() -> Promise` — `GET /api/discovery/status`.
- `api.discoveryRecipes() -> Promise` — `GET /api/discovery/recipes`.
- `api.runRecipe(id, limit) -> Promise` — `POST /api/discovery/recipes/:id/run`, with `?limit=` appended when `limit` is truthy.
- `api.dryRunRecipe(id, limit) -> Promise` — `POST /api/discovery/recipes/:id/dry-run`, with `?limit=` appended when `limit` is truthy.
- `api.approveRecipe(id) -> Promise` — `POST /api/discovery/recipes/:id/approve`.
- `api.discoveryCostSummary() -> Promise` — `GET /api/discovery/cost-summary`.
