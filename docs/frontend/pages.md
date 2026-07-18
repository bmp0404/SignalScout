# frontend/pages

The four page-level views that make up the Signal Scout single-page app, switched between by `App.jsx`'s tab nav.

## frontend/src/pages/Backtest.jsx
Loads and renders the historical backtest report: headline recall/lead-time stats, a score-distribution chart, top predictive signal types, and a sortable per-founder results table that opens the evidence panel.

- `Metric({ label, value, detail })` — renders a single labeled stat tile (used for recall, lead time, false positives, pre-connected counts).
- `Backtest()` — fetches `api.backtest()` on mount, shows loading/error/empty states, then renders headline copy, four `Metric` tiles, a `ScoreDistribution` chart, a bar list of `top_signal_types`, and a results table where clicking a row opens `EvidencePanel` for that `person_id`.

## frontend/src/pages/Digest.jsx
Public/operator dual-mode view of the generated founder digest: public visitors see a signup prompt only, while `operatorMode` unlocks generate/send controls and the full entry list.

- `Digest({ operatorMode = false })` — in public mode renders `DigestSignup` plus a static "digest is server-managed" notice; in operator mode loads the latest digest via `api.latestDigest()`, provides GENERATE (`api.generateDigest()`) and SEND PREVIEW (`api.sendDigest()`) buttons, and renders each digest entry (name, score, thesis, top signals, orbit/intro context, why-now, and `ContactLinks`).

## frontend/src/pages/Discover.jsx
The main discovery browser: fetches ranked candidates for a cohort (discovery vs. founder/ground-truth), optionally exposes operator controls to trigger and poll a live discovery pipeline run, and toggles between a single-card browsing view and a full table view.

- `Discover({ showOperatorControls = false })` — loads candidates via `api.candidates(cohort)` and the source mix via `api.overview()`; when operator controls are shown, `runDiscovery()` calls `api.runDiscovery()` (the OG batch pipeline — see `../backend/services.md`'s `DiscoveryJobManager`, not the recipe layer on the Admin tab) then polls `api.discoveryStatus()` every 1200ms (`POLL_MS`) until the job reaches `done`/`error`, refreshing the candidate list and highlighting newly discovered IDs on completion; renders `DigestSignup`, cohort/view toggle buttons, `PipelineProgress`, `SourceMix`, and either `CandidateTable` or `CandidateCard` (with prev/next paging) plus `EvidencePanel` on selection.

## frontend/src/pages/DiscoveryAdmin.jsx
Operator-only admin page for the `DiscoveryRecipe` layer (see `../backend/discovery.md`, `../backend/services.md`): lists every recipe with its approval/run status, lets an operator approve/dry-run/run a recipe, and shows spend + discovery-source-mix dashboards. Gated by a locally-entered operator secret (via `setOperatorToken`), not tied to any browser session/cookie — re-entering is required on reload, by design (never persisted).

- `providerLabel(provider) -> string` — maps `"pdl"`/`"coresignal"` to display labels, passes through anything else.
- `DiscoveryAdmin()` — before unlock, renders a password-style operator-secret input form (`unlock()` calls `setOperatorToken` and flips `unlocked`); once unlocked, `loadAll()` fetches `api.discoveryRecipes()`, `api.discoveryCostSummary()`, and `api.candidates('discovery')` (filtered client-side to `approval_state === 'unreviewed'`, sorted by score, capped at 10, for the "awaiting review" list) in parallel; renders `CostDashboard` and `SourceMixChart` side by side, a run-summary banner after any run/dry-run (`runRecipe(id, dryRun)` calls `api.dryRunRecipe`/`api.runRecipe`, surfacing a specific message on a 403 "needs approval" response), a recipe table (name/id/query_type, provider, approval-state badge, frequency, last run, last result/credit counts, and APPROVE/DRY RUN/RUN buttons — RUN is disabled until `approval_state === 'approved'`, `approve(id)` calls `api.approveRecipe`), and the awaiting-review list (clicking a row opens `EvidencePanel`).
