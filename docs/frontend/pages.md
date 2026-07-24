# frontend/pages

The four page-level views that make up the Signal Scout single-page app, switched between by `App.jsx`'s tab nav.

## frontend/src/pages/Backtest.jsx
Loads and renders the historical backtest report: headline recall/lead-time stats, a score-distribution chart, top predictive signal types, and a sortable per-founder results table that opens the evidence panel.

- `Metric({ label, value, detail })` — renders a single labeled stat tile (used for recall, lead time, false positives, pre-connected counts).
- `Backtest()` — loads `api.backtest()` via the shared `useAsyncData` hook (loading/error/empty states, RUN AGAIN calls its `reload`), then renders headline copy, four `Metric` tiles, a `ScoreDistribution` chart, a bar list of `top_signal_types`, and a results table where clicking a row opens `EvidencePanel` for that `person_id`.

## frontend/src/pages/Digest.jsx
Public digest view showing the rotating "up next" list subscribers receive, plus auto-send status; the manual send is operator-gated.

- `CADENCE_LABELS` / `autoSendSummary(auto)` — format the auto-send status line ("Sends automatically every 3 days · N active subscribers · last sent YYYY-MM-DD").
- `Digest()` — loads the upcoming digest via `api.upcomingDigest(offset)` through the shared `useAsyncData` hook, tracking a pagination `offsetRef` that the `NEW BATCH` button advances to the response's `next_offset` so each click cycles to a fresh batch of people; renders the auto-send status banner and each entry (rank, name + `provisional` badge for verified-tier backfill, school/location, thesis, top signals, orbit/intro context, why-now, `ContactLinks`), and exposes a `SEND NOW` button (`api.sendDigest()`) wrapped in `AdminOnly` so only unlocked operators can trigger an immediate send. Also renders `DigestSignup`.

## frontend/src/pages/Discover.jsx
Primary review workspace: loads discovery candidates and sorts them into Unreviewed / Approved / Rejected buckets with one-click Approve/Reject.

- `BUCKETS` — filter-tab definitions for the three review states.
- `reviewBucket(state) -> string` — maps `approval_state` to `approved` / `rejected` / `unreviewed` (`pending` and missing values count as unreviewed).
- `Discover()` — on mount loads candidates via `api.candidates('discovery')` and source mix via `api.overview()` together in one `Promise.all`; renders filter tabs with per-bucket counts (default Unreviewed); filters the list by selected bucket; `reviewCandidate(id, state)` calls `api.reviewCandidate` then refetches; renders `DigestSignup`, `SourceMix`, and either `CandidateTable` (with Approve/Reject) or `CandidateCard` (with review buttons + prev/next) plus `EvidencePanel` with the same review actions. Recipe/run controls live on the Pipeline tab, not here.

## frontend/src/pages/Pipeline.jsx
Page for the `DiscoveryRecipe` layer (see `../backend/discovery.md`, `../backend/services.md`): shows spend + discovery-source-mix dashboards and per-recipe status to everyone, but the approve/dry-run/run controls are operator-gated. Candidate review lives on Discover, not here.

- Provider display names use the shared `sourceLabel` helper from `SignalBadge.jsx` (imported as `providerLabel`; maps `pdl`/`coresignal`/`exa`, passes through anything else).
- `SKIP_LABELS` / `runSummaryText(summary)` — turn a run's `skip_reason` (`provider_not_configured`/`budget_exhausted`/`up_to_date`) into a human sentence, falling back to the created/duplicates/merged/credits result line.
- `outcomeBadge(recipe) -> {text, cls}` — derives a per-recipe outcome badge from the recipe row: "key missing" (provider not configured), "never run", "error", "no matches" (ran but created/saw nothing), or "N new".
- `Pipeline()` — on mount `loadAll()` fetches `api.discoveryRecipes()` and `api.discoveryCostSummary()`; renders `CostDashboard` and `SourceMixChart` side by side, a run-summary banner after any run/dry-run (`runRecipe(id, dryRun)` calls `api.dryRunRecipe`/`api.runRecipe`, surfacing a 403 "needs approval" message and `skip_reason` explanations via `runSummaryText`), and a recipe table (name/id/query_type, provider, approval-state badge, outcome badge, frequency, last run, last result/credit counts) with a "Loading recipes…" row until data arrives. The APPROVE/DRY RUN/RUN action cell is wrapped in `AdminOnly`, so recipe controls only appear once an operator unlocks; RUN stays disabled until `approval_state === 'approved'`.
