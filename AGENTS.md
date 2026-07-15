# AGENTS.md

## Learned User Preferences

- Candidates and signals must be REAL people scraped from live public sources, never fake, mocked, or synthetic data.
- Prefer building genuinely functional features over faked demos or animations (e.g., a real "Run Discovery" pipeline trigger with live progress, not a staged animation).
- The backtest should run against known founders to prove the tool surfaces people before they became well-known, while keeping real customer discovery intact for the actual use case.
- Push code to the user's own GitHub account (`ali8hsn`).
- The end audience for the product/demo is an investor named Cory; framing and "warm signal" logic should optimize for what Cory would trust.
- Do not scrape LinkedIn; obtain LinkedIn and contact data through licensed/compliant enrichment providers instead.
- Source diversification must be visible in the resulting candidates and signals; provider code is not complete until it actually populates credible non-GitHub discoveries when configured.
- Keep Discover, Backtest, and Digest as useful public product features; hide only destructive or operator-only controls rather than broadly suppressing functionality for a Cory release.

## Learned Workspace Facts

- "Signal Scout" (`signalScout`) finds exceptional people before they break out by collecting public signals (competitions, code, research, hackathons, network), scoring them, and backtesting against known founders.
- Stack: FastAPI + SQLite backend; Vite + React + Tailwind frontend with three tabs (Discover, Backtest, Digest). SQLite DB is `signal_scout.db`.
- Backend layering: domain dataclasses → repositories → scrapers → scoring/backtest → discovery/enrichment → digest → API, wired by `backend/container.py`.
- Data sources include GitHub, Twitter/X, USACO competitions, Semantic Scholar, Devpost, and opt-in fellowship seeds; `graph_edges` includes collaborator, repo-interaction, co-author, teammate, and fellowship-cohort relationships.
- Per candidate, capture location (origin + current), typed network connections (with a mini network viz), and contact info (email, LinkedIn URL, X handle) for one-click outreach in Cory's digest; a concentration detector flags overrepresented schools and regions.
- Provider-first discovery and enrichment are pluggable via `ENRICHMENT_PROVIDER` (PDL or Coresignal), with provider identity mapping, caching, and separate budget accounting; LinkedIn/contact data comes from these licensed providers, not scraping.
- Deployment target is Railway: the backend uses Postgres via `psycopg` when `DATABASE_URL` is set and SQLite otherwise, and `scripts/migrate_sqlite_to_postgres.py` ports all tables.
- Scoring formula: `score = (Σ strength×weight + recency bonus) × diversity × age factor`, normalized 0–100 across the cohort; weights live in `backend/scoring/weights.py`.
- Key scripts: `scripts/build_db.py`, `run_backtest.py`, `run_scrapers.py`, `run_discovery.py`. Live scrapers require `GITHUB_TOKEN` and are optional (never required for the demo).
- Backend runs via `uvicorn backend.main:app --port 8000`; frontend dev server runs on port 5173.
- `plan.md` is a reference spec — do not edit it when implementing its to-dos.
- Digest signup supports an authenticated immediate test send through Resend, limited to one successful test digest per subscriber every 24 hours.
