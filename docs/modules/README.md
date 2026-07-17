# SignalScout Module Docs

Index of per-module documentation. Each file lists every file in that module with a one-sentence blurb per function/class/method, written so a coding agent can orient without reading the source. **Keep these current**: any code change (new file, new function, new feature, deletion, rename) must update the matching doc in the same change — see `AGENTS.md`.

Backend pipeline order: `domain` → `db` → `scrapers` → `scoring` / `discovery` / `enrichment` → `services` → `digest` / `api`, all wired together by `core` (`backend/container.py`).

## Backend

| Doc | Covers | What it is |
|---|---|---|
| [core.md](core.md) | `backend/main.py`, `config.py`, `container.py` | Entry point + composition root: settings, DI wiring, FastAPI app construction. |
| [domain.md](domain.md) | `backend/domain/*` | Plain dataclasses (Person, Signal, GraphEdge, Digest, CandidateReview, Concentration, Subscriber) — the shared vocabulary every other layer imports. |
| [db.md](db.md) | `backend/db/*`, `db/repositories/*` | Persistence layer: `Database` connection provider (SQLite or Postgres via `DATABASE_URL`) + table-scoped repository classes. |
| [scrapers.md](scrapers.md) | `backend/scrapers/*` | Pulls raw evidence from GitHub, Devpost, Semantic Scholar, OpenAlex, and seeded fixtures into `Signal`/`GraphEdge` records; fails soft. |
| [scoring.md](scoring.md) | `backend/scoring/*` | Turns collected signals into a normalized 0-100 score (weighted sum × diversity × recency × age); backtests the formula against known founders/controls. |
| [discovery.md](discovery.md) | `backend/discovery/*` | Expands the candidate pool via graph/collaborator relationships, provider-based (PDL/Coresignal) expansion, curated-lab lead-gen (OpenAlex), fellowship seeds, entity resolution, and concentration detection. |
| [enrichment.md](enrichment.md) | `backend/enrichment/*`, `enrichment/providers/*` | Adds location/contact/identity data via pluggable licensed providers (PDL, Coresignal) with caching and per-provider budgets; never scrapes LinkedIn. |
| [services.md](services.md) | `backend/services/*` | Application-level orchestration classes that the API layer calls — glue between repositories/scoring/discovery/enrichment/digest. |
| [digest.md](digest.md) | `backend/digest/*` | Builds and sends the investor-facing digest email via Resend (or no-op preview sender). |
| [security.md](security.md) | `backend/security/*` | Tamper-proof, expiring action tokens for one-click email links (feedback/unsubscribe). |
| [api.md](api.md) | `backend/api/*` | FastAPI router — the public HTTP surface; translates requests into service calls, no business logic. |

## Frontend

| Doc | Covers | What it is |
|---|---|---|
| [frontend-core.md](frontend-core.md) | `main.jsx`, `App.jsx`, `api/client.js`, `pages/*` | App entry, shell, API client, and the three page views (Discover, Backtest, Digest). |
| [frontend-components.md](frontend-components.md) | `components/*` | Reusable presentational components and view-model helpers shared across pages. |

## Other

| Doc | Covers | What it is |
|---|---|---|
| [scripts.md](scripts.md) | `scripts/*` | Standalone CLI entry points for pipeline stages / maintenance, run directly against a `Container`. |
| [tests.md](tests.md) | `tests/*` | What each test file/function actually asserts. |
