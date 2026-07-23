# frontend/pages

The four page-level views that make up the Signal Scout single-page app, switched between by `App.jsx`'s tab nav.

## frontend/src/pages/Backtest.jsx
Loads and renders the historical backtest report: headline recall/lead-time stats, a score-distribution chart, top predictive signal types, and a sortable per-founder results table that opens the evidence panel.

- `Metric({ label, value, detail })` — renders a single labeled stat tile (used for recall, lead time, false positives, pre-connected counts).
- `Backtest()` — fetches `api.backtest()` on mount, shows loading/error/empty states, then renders headline copy, four `Metric` tiles, a `ScoreDistribution` chart, a bar list of `top_signal_types`, and a results table where clicking a row opens `EvidencePanel` for that `person_id`.

## frontend/src/pages/Digest.jsx
Operator digest view: generate/send controls and the full entry list. Mounted from `App` with `operatorMode` always on.

- `Digest({ operatorMode = false })` — when `operatorMode` is false renders `DigestSignup` plus a static notice; when true (production UI) loads the latest digest via `api.latestDigest()`, provides GENERATE (`api.generateDigest()`) and SEND TO SUBSCRIBERS (`api.sendDigest()`, a real send to all active subscribers with a sent/subscriber-count receipt) buttons, and renders each digest entry (name, score, thesis, top signals, orbit/intro context, why-now, and `ContactLinks`).

## frontend/src/pages/Discover.jsx
Primary review workspace: loads discovery candidates and sorts them into Unreviewed / Approved / Rejected buckets with one-click Approve/Reject.

- `BUCKETS` — filter-tab definitions for the three review states.
- `reviewBucket(state) -> string` — maps `approval_state` to `approved` / `rejected` / `unreviewed` (`pending` and missing values count as unreviewed).
- `Discover()` — loads candidates via `api.candidates('discovery')` and source mix via `api.overview()`; renders filter tabs with per-bucket counts (default Unreviewed); filters the list by selected bucket; `reviewCandidate(id, state)` calls `api.reviewCandidate` then refetches; renders `DigestSignup`, `SourceMix`, and either `CandidateTable` (with Approve/Reject) or `CandidateCard` (with review buttons + prev/next) plus `EvidencePanel` with the same review actions. Recipe/run controls live on the Pipeline tab, not here.

## frontend/src/pages/Pipeline.jsx
Ungated page for the `DiscoveryRecipe` layer (see `../backend/discovery.md`, `../backend/services.md`): lists every recipe with its approval/run status, lets the operator approve/dry-run/run a recipe, and shows spend + discovery-source-mix dashboards. Candidate review lives on Discover, not here.

- `providerLabel(provider) -> string` — maps `"pdl"`/`"coresignal"`/`"exa"` to display labels, passes through anything else.
- `outcomeBadge(recipe) -> {text, cls}` — derives a per-recipe outcome badge from the recipe row: "key missing" (provider not configured), "never run", "error", "no matches" (ran but created/saw nothing), or "N new".
- `Pipeline()` — on mount `loadAll()` fetches `api.discoveryRecipes()` and `api.discoveryCostSummary()`; explains that recipes also run automatically in the background; renders `CostDashboard` and `SourceMixChart` side by side, a run-summary banner after any run/dry-run (`runRecipe(id, dryRun)` calls `api.dryRunRecipe`/`api.runRecipe`, surfacing a specific message on a 403 "needs approval" response and distinguishing "provider key missing"/"budget exhausted" no-ops from real results), and a recipe table (name/id/query_type, provider, approval-state badge, outcome badge, frequency, last run, last result/credit counts, and APPROVE/DRY RUN/RUN buttons — RUN is disabled until `approval_state === 'approved'`, `approve(id)` calls `api.approveRecipe`). Manual RUN is for pulling extra people outside the automatic cadence.
