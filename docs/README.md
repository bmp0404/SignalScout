# SignalScout Module Docs

Index of per-module documentation. Each file lists every file in that module with a one-sentence blurb per function/class/method, written so a coding agent can orient without reading the source. **Keep these current**: any code change (new file, new function, new feature, deletion, rename) must update the matching doc in the same change — see `AGENTS.md`.

Directory structure mirrors the actual codebase: `docs/backend/` for `backend/*`, `docs/frontend/` for `frontend/src/*`, and `docs/scripts.md`/`docs/tests.md` for the top-level `scripts/`/`tests/` directories.

Backend pipeline order: `domain` → `db` → `scrapers` → `scoring` / `discovery` / `enrichment` → `services` → `digest` / `api`, all wired together by `core` (`backend/container.py`).

## Backend

| Doc | Covers | What it is |
|---|---|---|
| [backend/core.md](backend/core.md) | `backend/main.py`, `config.py`, `container.py` | Entry point + composition root: settings, DI wiring, FastAPI app construction. |
| [backend/domain.md](backend/domain.md) | `backend/domain/*` | Plain dataclasses (Person, Signal, GraphEdge, Digest, CandidateReview, Concentration, Subscriber, DiscoveryRecipe) — the shared vocabulary every other layer imports. |
| [backend/db.md](backend/db.md) | `backend/db/*`, `db/repositories/*` | Persistence layer: `Database` connection provider (SQLite or Postgres via `DATABASE_URL`) + table-scoped repository classes. |
| [backend/scrapers.md](backend/scrapers.md) | `backend/scrapers/*` | Pulls raw evidence from GitHub, Devpost, Semantic Scholar, OpenAlex, fellowship/competition pages, and seeded fixtures into `Signal`/`GraphEdge` records or free-source discovery leads; fails soft. |
| [backend/scoring.md](backend/scoring.md) | `backend/scoring/*` | Turns collected signals into a normalized 0-100 score (weighted sum × diversity × recency × age); backtests the formula against known founders/controls. |
| [backend/discovery.md](backend/discovery.md) | `backend/discovery/*` | Expands the candidate pool via graph/collaborator relationships, provider-based (PDL/Coresignal) expansion — both the batch pipeline and the named, approvable `DiscoveryRecipe` layer — curated-lab lead-gen (OpenAlex), fellowship seeds, entity resolution, and concentration detection. |
| [backend/enrichment.md](backend/enrichment.md) | `backend/enrichment/*`, `enrichment/providers/*` | Adds location/contact/identity data via pluggable licensed providers (PDL, Coresignal) with caching and per-provider budgets; never scrapes LinkedIn. |
| [backend/services.md](backend/services.md) | `backend/services/*` | Application-level orchestration classes that the API layer calls — glue between repositories/scoring/discovery/enrichment/digest. |
| [backend/digest.md](backend/digest.md) | `backend/digest/*` | Builds and sends the investor-facing digest email via Resend (or no-op preview sender). |
| [backend/security.md](backend/security.md) | `backend/security/*` | Tamper-proof, expiring action tokens for one-click email links (feedback/unsubscribe). |
| [backend/api.md](backend/api.md) | `backend/api/*` | FastAPI router — the public HTTP surface; translates requests into service calls, no business logic. |

## Frontend

| Doc | Covers | What it is |
|---|---|---|
| [frontend/core.md](frontend/core.md) | `main.jsx`, `App.jsx`, `index.css` | App entry, shell, and global styling. |
| [frontend/api.md](frontend/api.md) | `api/client.js` | The fetch wrapper and `api` object listing every backend endpoint the frontend calls. |
| [frontend/pages.md](frontend/pages.md) | `pages/*` | The four page views: Discover, Backtest, Digest, and DiscoveryAdmin. |
| [frontend/components.md](frontend/components.md) | `components/*` | Reusable presentational components and view-model helpers shared across pages. |

## Other

| Doc | Covers | What it is |
|---|---|---|
| [scripts.md](scripts.md) | `scripts/*` | Standalone CLI entry points for pipeline stages / maintenance, run directly against a `Container`. |
| [tests.md](tests.md) | `tests/*` | What each test file/function actually asserts. |
