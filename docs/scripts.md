# Scripts

Standalone CLI entry points for running pipeline stages / maintenance tasks against the backend, invoked directly (e.g. `python scripts/run_discovery.py`) rather than through the API. Every script inserts the repo root onto `sys.path` and builds a `backend.container.Container` to reach the DB and services.

## scripts/build_db.py
Initializes or destructively rebuilds the SQLite/Postgres database: ground truth founders/researchers -> synthetic control group -> seeded signal fixtures -> graph edges -> entity resolution -> contact/location enrichment -> scoring -> concentration detection.

- `load_ground_truth(container) -> None` — loads founders and seed-researcher anchors from the ground-truth JSON file into `persons`.
- `load_controls(container) -> None` — deterministically (seed 42) generates 60 synthetic control-group people with 0-3 weak signals each, for false-positive pressure testing.
- `load_discoveries(container) -> None` — loads 12 hand-written fictional demo profiles tagged `cohort="demo"`; only runs with `--with-demo`.
- `load_signals(container) -> int` — runs `SeededScraper` over every fixture JSON in the seed-signals dir (except discoveries/graph_edges), resolves and saves the signals, returns total count.
- `load_edges(container) -> None` — loads `graph_edges.json`, resolves entities, and saves `GraphEdge` rows.
- `enrich(container) -> None` — runs contact enrichment + location resolution over every non-control person.
- `main() -> None` — parses `--with-demo` and `--if-empty`, resets the DB (unless `--if-empty`), runs the full load/enrich/score/concentration pipeline.
- Flags: `--with-demo` (include fictional demo profiles), `--if-empty` (only seed if `persons` table is empty; never touches existing/migrated data — safe for hosted startup). No flags = legacy explicit destructive local rebuild (calls `container.db.reset()`). Reads `container.settings.ground_truth_file`, `seed_signals_dir`, `flag_threshold`.

## scripts/migrate_sqlite_to_postgres.py
Copies every table row-for-row from the local SQLite DB into Postgres, truncating and re-verifying counts inside one transaction so a run is idempotent and only commits on full success.

- `sqlite_tables(conn) -> list[str]` — lists all non-system tables in the SQLite DB.
- `row_count(conn, table) -> int` — counts rows in a table.
- `dependency_order(conn, tables) -> list[str]` — topologically sorts tables by foreign key so parents copy before children (falls back to alphabetical on FK cycles).
- `dry_run(sqlite_conn) -> None` — prints table/row counts without touching Postgres.
- `ensure_table(pg_conn, sqlite_conn, table) -> None` — verifies the destination table exists in Postgres, falling back to replaying the SQLite DDL if `schema.sql` didn't create it.
- `truncate_tables(pg_conn, tables) -> None` — empties all destination tables at once (`TRUNCATE ... CASCADE`) so cascading deletes can't wipe rows already copied.
- `copy_table(pg_conn, sqlite_conn, table) -> int` — batches rows (500 at a time) from SQLite and inserts them into Postgres one row per `execute` call, returns count copied.
- `verify_table(pg_conn, table, expected) -> None` — raises if the copied row count doesn't match expected.
- `migrate(sqlite_conn, database_url) -> None` — orchestrates schema creation, table ordering, truncation, copy, and per-table verification, then commits.
- `main() -> None` — parses `--sqlite` and `--dry-run`, reads `DATABASE_URL`/`SIGNAL_SCOUT_DB` env vars.
- Env vars: `DATABASE_URL` (required unless `--dry-run`), `SIGNAL_SCOUT_DB` (optional SQLite path override). Requires `psycopg` installed for a real (non-dry-run) migration. Destructive to the Postgres destination tables (truncates them) but safe/idempotent to re-run.

## scripts/reenrich.py
Re-runs contact + location enrichment over everyone already in the DB using already-stored signals (no network calls), then rescores all candidates and prints LinkedIn yield stats.

- `main() -> None` — iterates all non-control people, re-enriches contacts/locations, saves, rescores, and prints a top-15 list of discoveries with resolved LinkedIn URLs.
- No CLI args or env vars. Safe to run repeatedly (no network I/O, purely reprocesses stored data).

## scripts/run_backtest.py
Runs the founder backtest (`container.backtest.run()`) and pretty-prints the pitch report (recall, lead time, false positives, top predictive signal types, per-founder results).

- `main() -> None` — runs the backtest and formats the report to stdout.
- No CLI args or env vars. Read-only against the DB.

## scripts/run_digest_cron.py
Runs the due-subscriber digest job for a Railway cron service, sending live emails (not a dry run).

- `main() -> None` — calls `container.subscriber_digest.run_due(dry_run=False)` and prints subscriber/sent counts.
- No CLI args. Sends real emails to due subscribers — not idempotent/safe to spam-run since each due subscriber gets a delivery per invocation (though the digest layer prevents re-sending the same candidate).

## scripts/run_discovery_cron.py
Runs every due discovery recipe for a Railway cron service (or a local one-shot). Honors each recipe's weekly/biweekly cadence via `DiscoveryRecipeService.run_due()`.

- `main() -> None` — calls `container.discovery_recipe_service.run_due()` and prints due/ran/error/created counts.
- No CLI args. Safe to invoke daily: recipes that are not due are skipped. Still spends provider credits for recipes that *are* due and whose provider key is configured.

## scripts/run_digest.py
Regenerates the digest from the current live discovery cohort and persists it (writes `out/digest-<date>.html`).

- `main() -> None` — calls `container.digest_generator.generate()` and prints each entry's name/score.
- No CLI args or env vars. Writes an output file each run; safe to rerun (overwrites/adds dated file).

## scripts/run_discovery.py
Runs the full live discovery pipeline: licensed provider search (PDL -> Coresignal), optional GitHub graph expansion from seed accounts, Semantic Scholar co-author enrichment, Devpost hackathon-teammate enrichment, and collaboration-based promotion — then rescores all candidates.

- `parse_args() -> argparse.Namespace` — defines all CLI flags (see below).
- `save_collected(container, signals, edges) -> None` — resolves and persists newly collected signals/edges through the entity resolver.
- `has_source(container, person, source) -> bool` — idempotency guard checking if a person already has a signal from a given source.
- `source_audit(container) -> None` — prints a full dry-run report (signal source mix, discovery origins, provider chain, budgets remaining, pending GitHub enrichments, allowlisted filters, and planned next provider-search pages) without writing or spending anything.
- `main() -> None` — orchestrates: provider-search lane, optional GitHub lane (needs `GITHUB_TOKEN`), Semantic Scholar lane, Devpost lane, `CollaborationExpander` promotion, and final rescore.
- CLI flags: `--seed-limit` (default 3), `--max-per-seed` (default 20), `--scholar-limit` (default 8), `--devpost-limit` (default 8), `--collab-cap`, `--include-fellowship-seeds`, `--dry-run` (source audit only, no writes/spend), `--provider-only` (skip GitHub/Scholar/Devpost lanes). Env var: `GITHUB_TOKEN` (GitHub lane is skipped without it). Founders' curated signals are never overwritten (protects the backtest); discovery cohort only gets new-source enrichment. Not fully idempotent — reruns can create/promote new people, though most steps check for existing signals/sources first.

## scripts/run_enrichment.py
Backfills licensed provider enrichment (PDL -> Coresignal, budget-governed) over existing people, merging contacts and (for discoveries only) emitting new scored signals.

- `main() -> None` — parses args, prints the provider chain/budgets if requested, prioritizes people needing enrichment, runs `provider_enricher.run()` per person, tallies outcome statuses, saves and rescores.
- `_print_chain(chain, settings) -> None` — prints the active provider chain and each provider's monthly/daily caps and search/enrich split.
- CLI flags: `--cohort` (default `discovery`; founders get contact fields only, no scored signals — backtest protection), `--limit` (default 100), `--dry-run` (reports the plan, spends no credits, writes nothing), `--provider-chain` (prints chain/budget info). Env vars: `PDL_API_KEY`, `CORESIGNAL_API_KEY` (script exits with a notice if neither is set). Safe to rerun: results are cached for 30 days and budget exhaustion stops the run cleanly rather than erroring.

## scripts/run_scrapers.py
Runs the live GitHub scraper for every person with a `github_username`, persisting scored signals for non-founders while still using founders' live data for contact/location enrichment only (protects the backtest from live-magnitude skew).

- `main() -> None` — builds a `GithubScraper`, scrapes all eligible people, splits founder vs. non-founder signals (only non-founder signals are persisted), enriches contacts/locations for everyone, and rescores.
- No CLI args. Env var: `GITHUB_TOKEN` (required; script prints a notice and exits early if unset, since seeded fixtures already cover the demo without it). Safe to rerun — re-scrapes current GitHub state and re-persists/rescoring.

## scripts/seed_launch_cohort.py
Idempotently persists the publicly verified first-launch cohort from `data/launch_cohort.json` into the DB — creates/updates each person, adds their primary evidence signal if not already stored, and marks them as an approved candidate review.

- `main() -> None` — reads the cohort JSON, upserts each `Person` (by fixed `person_id`), adds a `Signal` for the primary evidence URL if not already present (raising if `signal_date` is missing), calls `candidate_review_service.review(state="approved", ...)` for each, then rescores and prints the approved mix.
- No CLI args or env vars. Reads `data/launch_cohort.json` (fixed path relative to repo root). Idempotent — re-running does not duplicate signals or people (keyed by `person_id` and evidence URL).

## scripts/send_digests.py
Runs due subscriber digests, usable from a Railway cron service or a local shell, with dry-run and single-recipient testing support.

- `main() -> None` — parses `--dry-run`/`--recipient`, initializes the schema, calls `container.subscriber_digest.run_due(...)`, prints a JSON summary (subscriber count, sent count, per-result statuses).
- CLI flags: `--dry-run` (renders previews without sending or recording recipients), `--recipient` (force-run one specific active subscriber regardless of frequency/day, for manual testing). No live-send-affecting env vars beyond what `Container`/`Settings` already read. Safe to rerun in dry-run mode; live mode sends real emails to due subscribers.

## scripts/verify_candidates.py
Dumps the top N unranked discovery candidates to `out/verify.md` as a manual-verification checklist (GitHub link, follower count, score, top signal summaries, email, and a pre-built LinkedIn people-search URL) for a human to confirm each is a real, pre-breakout person before outreach.

- `linkedin_search_url(name, school) -> str` — builds a LinkedIn people-search URL from a name and optional school.
- `main() -> None` — pulls the top `--top` discovery candidates via `candidate_service.list_candidates("discovery")`, formats a markdown checklist per candidate, and writes it to `out/verify.md`.
- CLI flag: `--top` (default 15, how many candidates to include). No env vars. Writes/overwrites `out/verify.md` each run; read-only against the DB, safe to rerun.
