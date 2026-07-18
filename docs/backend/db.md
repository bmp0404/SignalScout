# Db

The `db` module is SignalScout's persistence layer: a thin `Database` connection provider plus a set of table-scoped repository classes that translate between SQL rows and `backend/domain` dataclasses. It supports SQLite (default, path from `SIGNAL_SCOUT_DB`) for local dev/demos/backtests and Postgres via `psycopg` automatically when `DATABASE_URL` is set, with repositories writing SQLite-flavored SQL (qmark params, `INSERT OR REPLACE`) that a shim translates on the fly so both backends run identical code.

## backend/db/__init__.py
Empty file (no re-exports).

## backend/db/database.py
Defines the `Database` connection provider, schema initialization, and the `PostgresConnection` shim that lets Postgres run the same SQLite-flavored SQL the repositories write.

- `_translate_placeholders(sql) -> str` — converts SQLite `?` qmark placeholders to psycopg `%s` style, skipping `?` inside single-quoted string literals.
- `_split_statements(script) -> list[str]` — splits a multi-statement SQL script on `;` the way `sqlite3.executescript` would, respecting quoted strings and `--` line comments.
- `PostgresConnection` — wraps a psycopg connection to expose the `sqlite3.Connection` surface (`execute`/`executescript`/`commit`/`close`) that repositories rely on, returning rows as dicts via `dict_row`.
  - `PostgresConnection.__init__(database_url)` — connects via psycopg with dict-row results and raises if psycopg isn't installed.
  - `PostgresConnection.execute(sql, params)` — translates the SQL (placeholders, upsert rewriting) and executes it, no-opping SQLite-only statements like `PRAGMA`.
  - `PostgresConnection.executescript(script)` — splits the script into statements and executes each one.
  - `PostgresConnection.commit()` — commits the underlying psycopg connection.
  - `PostgresConnection.close()` — closes the underlying psycopg connection.
  - `PostgresConnection._translate(sql) -> str | None` — routes a SQL string through placeholder translation and `INSERT OR REPLACE` → upsert rewriting, returning `None` for PRAGMA statements.
  - `PostgresConnection._to_upsert(sql, match)` — rewrites `INSERT OR REPLACE INTO table (...)` into a Postgres `INSERT ... ON CONFLICT (pk) DO UPDATE/DO NOTHING` using the table's primary key columns.
  - `PostgresConnection._qualify_upsert_arithmetic(sql)` — disambiguates self-referential arithmetic in `DO UPDATE SET col = col + ...` by qualifying the target-side column with the table name (needed because Postgres would otherwise resolve it to `EXCLUDED`).
  - `PostgresConnection._primary_key(table) -> list[str]` — looks up and caches a table's primary-key column names from `information_schema`.
- `Database` — owns the connection lifecycle for either backend and exposes schema init/reset/close.
  - `Database.__init__(db_path, database_url=None)` — picks `postgres` vs `sqlite` backend based on whether `database_url` (or `DATABASE_URL` env var) is set, and sets up thread-local SQLite connection tracking.
  - `Database.conn` (property) — returns the shared `PostgresConnection` for Postgres, or a thread-local SQLite connection (with `PRAGMA foreign_keys = ON`) for SQLite, creating one if needed.
  - `Database.init_schema()` — runs `schema.sql` via `executescript` and commits.
  - `Database.reset()` — drops all tables (via `pg_tables`/`CASCADE` on Postgres, or `sqlite_master` with foreign keys toggled off on SQLite) and recreates the schema; used by `build_db` for idempotent rebuilds.
  - `Database.close()` — closes the Postgres connection, or closes and clears all tracked SQLite connections across threads.

## backend/db/repositories/__init__.py
Empty file (no re-exports).

## backend/db/repositories/base.py
Defines `BaseRepository`, the shared superclass every concrete repository extends: it stores the `Database` handle, exposes a `conn` property that proxies to `db.conn` (so each repository always gets the correct thread-local/Postgres connection), and provides static `dumps`/`loads` helpers so repositories can serialize Python values into TEXT/JSON columns and deserialize them back with a default fallback when the column is empty/NULL.

- `BaseRepository` — shared base class providing connection access and JSON (de)serialization for TEXT columns.
  - `BaseRepository.__init__(db)` — stores the `Database` instance.
  - `BaseRepository.conn` (property) — returns `self.db.conn`, the active connection.
  - `BaseRepository.dumps(value) -> str` — JSON-serializes a value (`ensure_ascii=False`) for storage in a TEXT column.
  - `BaseRepository.loads(text, default)` — JSON-deserializes a TEXT column value, returning `default` if the text is empty/None.

## backend/db/repositories/candidate_reviews.py
Manages the `candidate_reviews` table, which holds human review decisions (pending/approved/rejected) on discovery candidates, mapping rows to/from the `CandidateReview` domain dataclass.

- `utc_now() -> str` — returns the current UTC time as an ISO-8601 string with second precision.
- `CandidateReviewRepository` — CRUD/upsert access to `candidate_reviews`.
  - `CandidateReviewRepository.get(person_id)` — fetches a single review row by `person_id`.
  - `CandidateReviewRepository.all(state=None)` — lists all reviews, optionally filtered by `state`, ordered by `updated_at` descending.
  - `CandidateReviewRepository.approved_contactable()` — lists approved reviews with `contactable = 1`, ordered by `approved_at` then `person_id`.
  - `CandidateReviewRepository.upsert(person_id, state, why_now, notes, source_bucket, contactable, primary_evidence_url, reviewer)` — validates `state`/`source_bucket` against allowed sets, sets/clears `approved_at` based on state transitions, and upserts the row via `ON CONFLICT(person_id)`.
  - `CandidateReviewRepository._to_model(row) -> CandidateReview` — converts a DB row into a `CandidateReview` dataclass instance.

## backend/db/repositories/concentrations.py
Manages the `concentrations` table, which stores precomputed groupings (e.g. by school/company) of persons with counts, mapping to/from the `Concentration` domain dataclass.

- `ConcentrationRepository` — full-replace access to `concentrations`.
  - `ConcentrationRepository.replace_all(concentrations)` — deletes all existing rows and re-inserts the given list (person id/name lists JSON-encoded).
  - `ConcentrationRepository.all()` — returns all concentrations ordered by `count` descending.
  - `ConcentrationRepository._to_model(row) -> Concentration` — converts a DB row into a `Concentration` dataclass, decoding the JSON `person_ids`/`person_names` columns.

## backend/db/repositories/digests.py
Manages the `digests` table, which stores generated email digests (subject, entries, rendered HTML), mapping to/from the `Digest`/`DigestEntry` domain dataclasses.

- `DigestRepository` — save/read access to `digests`.
  - `DigestRepository.save(digest)` — upserts (`INSERT OR REPLACE`) a digest row, JSON-encoding its `entries` list of dataclasses via `asdict`.
  - `DigestRepository.latest()` — returns the most recently generated digest, or `None`.
  - `DigestRepository._to_model(row) -> Digest` — converts a DB row into a `Digest`, decoding JSON entries into `DigestEntry` instances.

## backend/db/repositories/discovery_recipes.py
Self-provisioning (`CREATE TABLE IF NOT EXISTS`) repository for `DiscoveryRecipe` rows (see `domain.md`), backing the recipe layer on top of `ProviderExpander` (see `discovery.md`). Never overwrites an existing recipe row on seed, so operator edits (approval, status, last_run) survive container restarts.

- `TABLE_SQL` — module-level DDL for the `discovery_recipes` table.
- `DiscoveryRecipeRepository` — CRUD + idempotent-seed access to `discovery_recipes`.
  - `DiscoveryRecipeRepository.__init__(db)` — creates the table if missing.
  - `DiscoveryRecipeRepository.seed(recipes)` — inserts each recipe in the list only if a row with that `id` doesn't already exist; used by `container.py` to load `backend/discovery/recipe_seeds.py`'s `INITIAL_RECIPES` on every startup without clobbering operator changes.
  - `DiscoveryRecipeRepository.get(recipe_id) -> DiscoveryRecipe | None` — fetches one recipe by id.
  - `DiscoveryRecipeRepository.all() -> list[DiscoveryRecipe]` — lists all recipes ordered by name.
  - `DiscoveryRecipeRepository.upsert(recipe)` — inserts or fully replaces (`INSERT OR REPLACE`) a recipe row.
  - `DiscoveryRecipeRepository.set_last_run(recipe_id, when)` — updates just the `last_run` column.
  - `DiscoveryRecipeRepository.set_approval_state(recipe_id, approval_state)` — updates just the `approval_state` column.
  - `DiscoveryRecipeRepository.set_status(recipe_id, status)` — updates just the `status` column.
  - `DiscoveryRecipeRepository._to_model(row) -> DiscoveryRecipe` — converts a DB row into a `DiscoveryRecipe`, decoding the JSON `filters_json`/`relative_filters_json` columns.

## backend/db/repositories/enrichment.py
Two self-provisioning (`CREATE TABLE IF NOT EXISTS`) repositories backing the licensed-enrichment guardrails: `enrichment_cache` (cached provider payloads per person) and `enrichment_usage` (per-provider/lane/day request counters), created inline because the live database predates them and is never reset.

- `CACHE_TABLE_SQL` / `USAGE_TABLE_SQL` — module-level DDL constants for the two tables and their indexes.
- `EnrichmentCacheRepository` — caches provider enrichment payloads keyed by `provider:person_id`.
  - `EnrichmentCacheRepository.__init__(db)` — creates the `enrichment_cache` table/index if missing.
  - `EnrichmentCacheRepository.get(provider, person_id) -> tuple[dict, str] | None` — returns `(payload, fetched_at)` for a cached lookup, or `None` if never fetched (an empty payload dict is still a valid cached miss).
  - `EnrichmentCacheRepository.put(provider, person_id, payload, fetched_at)` — upserts (`INSERT OR REPLACE`) the cached payload for a provider+person.
  - `EnrichmentCacheRepository._key(provider, person_id) -> str` — builds the `"provider:person_id"` cache key.
- `EnrichmentUsageRepository` — tracks daily per-provider/per-lane API usage against budgets.
  - `EnrichmentUsageRepository.__init__(db)` — migrates away any legacy schema, then creates the `enrichment_usage` table if missing.
  - `EnrichmentUsageRepository._migrate_legacy_schema()` — drops the old global `day -> count` table shape (which predates the `provider`/`lane` columns and is empty in production) before the new table is created.
  - `EnrichmentUsageRepository._has_legacy_usage_table() -> bool` — detects the legacy schema by checking for a `day` column without a `provider` column, via `PRAGMA table_info` (SQLite) or `information_schema.columns` (Postgres).
  - `EnrichmentUsageRepository.count_for(provider, day, lane=None) -> int` — sums usage counts for a provider on a given UTC day, optionally scoped to one lane.
  - `EnrichmentUsageRepository.count_for_month(provider, month, lane=None) -> int` — sums usage counts for a provider across a `YYYY-MM` month via a `LIKE` prefix match on `day`.
  - `EnrichmentUsageRepository.increment(provider, lane, day, by=1)` — upserts the day's counter, adding `by` on conflict (`ON CONFLICT ... DO UPDATE SET count = count + excluded.count`).

## backend/db/repositories/graph_edges.py
Manages the `graph_edges` table, which stores directed relationships between persons (e.g. co-founder, colleague) with observation dates and metadata, mapping to/from the `GraphEdge` domain dataclass.

- `GraphEdgeRepository` — save/query access to `graph_edges`.
  - `GraphEdgeRepository.save(edge)` — upserts (`INSERT OR REPLACE`) a single edge; does not commit.
  - `GraphEdgeRepository.save_many(edges)` — saves a list of edges and commits once at the end.
  - `GraphEdgeRepository.for_person(person_id, before=None)` — returns all edges touching a person as either source or target, optionally filtered to edges observed before a given date.
  - `GraphEdgeRepository.all()` — returns every edge row.
  - `GraphEdgeRepository._to_model(row) -> GraphEdge` — converts a DB row into a `GraphEdge`, decoding the JSON `metadata` column.

## backend/db/repositories/page_views.py
Manages the `page_views` table, a privacy-minimal log of page path + referrer + timestamp used for basic traffic counting.

- `PageViewRepository` — write/count access to `page_views`.
  - `PageViewRepository.record(path, referrer=None)` — inserts a new page-view row with a generated UUID and current UTC timestamp, returning the view id.
  - `PageViewRepository.count() -> int` — returns the total number of recorded page views.

## backend/db/repositories/persons.py
Manages the `persons` table (the core entity: candidate/tracked individuals with contact info, cohort, discovery/enrichment metadata, and score), mapping to/from the `Person` domain dataclass; also self-migrates the table by adding newer columns and backfilling discovery metadata on legacy rows.

- `PersonRepository` — CRUD access to `persons`, plus schema/data migration helpers.
  - `PersonRepository.EXTRA_COLUMNS` — dict of columns (`discovery_origin`, `evidence_tier`, `review_required`, `enrichment_status`, `enrichment_provider`, `enrichment_updated_at`, `discovery_source`) added on top of the base schema if missing.
  - `PersonRepository.__init__(db)` — ensures the extra columns exist, then backfills legacy discovery metadata.
  - `PersonRepository.save(person)` — upserts (`INSERT OR REPLACE`) a full person row.
  - `PersonRepository.save_many(persons)` — saves a list of persons (one `save` call each, no aggregate commit).
  - `PersonRepository.get(person_id)` — fetches a person by id.
  - `PersonRepository.find_by_name(name)` — case-insensitive lookup by exact name match.
  - `PersonRepository.find_by_github(username)` — case-insensitive lookup by GitHub username.
  - `PersonRepository.all(cohort=None)` — lists all persons, optionally filtered by cohort.
  - `PersonRepository.update_score(person_id, score)` — updates just the `score` column for a person.
  - `PersonRepository.delete(person_id)` — deletes a person row outright. Used by `backend/scrapers/resolve.py`'s `LeadResolver` to roll back the tentative row it saves before calling `ProviderEnricher.run()` (required because `run()` derives `Signal` rows that FK-reference `persons`, so the row must exist first) when PDL Identify doesn't confirm a match.
  - `PersonRepository._to_model(row) -> Person` — converts a DB row into a `Person`, decoding JSON `aliases`/`contact_info` columns.
  - `PersonRepository._ensure_columns()` — adds any of `EXTRA_COLUMNS` missing from the live `persons` table via `ALTER TABLE ADD COLUMN`.
  - `PersonRepository._column_names() -> set[str]` — returns the current set of column names on `persons`, via `information_schema.columns` (Postgres) or `PRAGMA table_info` (SQLite).
  - `PersonRepository._derive_legacy_discovery_metadata()` — for pre-migration `cohort='discovery'` rows missing `discovery_origin`/`evidence_tier`/`enrichment_status`/`discovery_source`, infers and backfills those fields from `contact_info`, presence of a `provider_identities` row, and related `signals` rows (e.g. `job_change` or `linkedin_created_recently` signals imply a "verified" evidence tier); `discovery_source` is backfilled as `f"{provider}_discovery"` when `contact_info["discovered_via"]` is `"pdl"`/`"coresignal"` and the row predates the `discovery_source` column.

## backend/db/repositories/provider_identities.py
Two self-provisioning repositories used by provider-search discovery: `ProviderIdentityRepository` dedupes people found via paid data providers against existing `persons` (backed by `provider_identities`), and tracks pagination/outcome checkpoints per provider+filter (backed by `provider_search_checkpoints`) — this checkpoint table doubles as the recipe run-log the recipe API surfaces (see `services.md`'s `discovery_recipe_service.py`).

- `TABLE_SQL` — module-level DDL for `provider_identities` (+ indexes) and `provider_search_checkpoints`.
- `ProviderSearchCheckpoint` — dataclass capturing a provider search's pagination cursor, page/request/record counters, per-outcome counters (verified/review/merged/duplicate/rejected/error), `search_credit_units`/`collect_credit_units` (Coresignal's separately-billed Search vs Collect API calls — always 0 for PDL, which doesn't distinguish them), rejection-reason histogram, and last outcome string.
- `canonical_linkedin(url) -> str | None` — normalizes a LinkedIn URL into a stable lower-cased dedupe key (strips scheme, query, fragment, `www.`, trailing slash).
- `ProviderIdentityRepository` — identity dedupe + checkpoint persistence.
  - `ProviderIdentityRepository.__init__(db)` — creates the two tables/indexes and ensures the checkpoint table's newer columns exist.
  - `ProviderIdentityRepository.find_person_by_provider_id(provider, provider_person_id) -> str | None` — looks up an existing `person_id` by exact `(provider, provider_person_id)` key.
  - `ProviderIdentityRepository.find_person_by_linkedin(url) -> str | None` — looks up an existing `person_id` by canonicalized LinkedIn URL.
  - `ProviderIdentityRepository.link(provider, provider_person_id, person_id, linkedin_url, observed_at)` — upserts (`INSERT OR REPLACE`) a provider-identity-to-person mapping.
  - `ProviderIdentityRepository.checkpoint(provider, filter_identity) -> ProviderSearchCheckpoint | None` — loads a checkpoint row for a given provider+filter, or `None` if absent.
  - `ProviderIdentityRepository.record_search_page(checkpoint, *, next_cursor, exhausted, api_requests, returned_records, credit_units, outcomes, rejection_reasons, last_outcome, updated_at, advance=True, search_credit_units=0, collect_credit_units=0)` — builds an updated `ProviderSearchCheckpoint` by accumulating the new page's counters (including the Search/Collect credit split) onto the existing checkpoint, persists it (`INSERT OR REPLACE`), and returns it.
  - `ProviderIdentityRepository._ensure_checkpoint_columns()` — adds the `error_count`, `search_credit_units`, and `collect_credit_units` columns to `provider_search_checkpoints` if missing, tolerating a "duplicate column" error on SQLite.
  - `ProviderIdentityRepository.ensure_checkpoint(provider, filter_identity, filters, updated_at) -> ProviderSearchCheckpoint` — returns the existing checkpoint or a fresh in-memory (not yet persisted) default one.
  - `ProviderIdentityRepository.checkpoints() -> list[ProviderSearchCheckpoint]` — lists all checkpoints across all providers/filters, ordered by provider then filter identity.

## backend/db/repositories/signals.py
Manages the `signals` table, which stores discrete detected events (e.g. job changes, GitHub activity) tied to a person, mapping to/from the `Signal` domain dataclass.

- `SignalRepository` — save/query access to `signals`.
  - `SignalRepository.save(signal)` — upserts (`INSERT OR REPLACE`) a single signal; does not commit.
  - `SignalRepository.save_many(signals)` — saves a list of signals and commits once at the end.
  - `SignalRepository.for_person(person_id, before=None)` — returns a person's signals ordered by date, optionally only those before a given date.
  - `SignalRepository.unresolved()` — returns signals not yet linked to a person (`person_id IS NULL`).
  - `SignalRepository.assign_person(signal_id, person_id)` — links an unresolved signal to a person by updating `person_id`; does not commit.
  - `SignalRepository.commit()` — commits pending changes (used after `assign_person`/`save` calls that intentionally defer commit).
  - `SignalRepository._to_model(row) -> Signal` — converts a DB row into a `Signal`, decoding JSON `raw_data`/`metadata` columns.

## backend/db/repositories/subscriptions.py
Three repositories covering the digest-email subscription lifecycle: `SubscriberRepository` (`subscribers` table — email list membership and preferences), `DigestSendRepository` (`digest_sends` table — per-subscriber send history to prevent repeats), and `FeedbackRepository` (`feedback_votes` table — subscriber up/down votes on persons).

- `SubscriberRepository` — CRUD access to `subscribers`.
  - `SubscriberRepository.subscribe(email, frequency, preferences)` — creates a new `Subscriber` domain object and upserts it (`ON CONFLICT(email)` updates frequency/preferences/reactivates), returning the persisted record.
  - `SubscriberRepository.get_by_email(email)` — fetches a subscriber by normalized (trimmed, lower-cased) email.
  - `SubscriberRepository.get(subscriber_id)` — fetches a subscriber by id.
  - `SubscriberRepository.get_by_token(token)` — fetches a subscriber by their unsubscribe token.
  - `SubscriberRepository.active(frequency=None, email=None)` — lists active subscribers, optionally filtered by frequency and/or email, ordered by creation time.
  - `SubscriberRepository.deactivate(token) -> bool` — sets `active = 0` for the subscriber matching an unsubscribe token; returns whether a row was actually updated.
  - `SubscriberRepository._to_model(row) -> Subscriber` — converts a DB row into a `Subscriber`, decoding the JSON `preferences` column.
- `DigestSendRepository` — tracks which persons have been sent to which subscribers.
  - `DigestSendRepository.sent_since(subscriber_id, since)` — returns whether any digest was sent to a subscriber at/after a given datetime.
  - `DigestSendRepository.sent_person_ids(subscriber_id) -> set[str]` — returns the set of person ids already sent to a subscriber (for never-repeat filtering).
  - `DigestSendRepository.record_many(subscriber_id, person_ids, provider_message_id)` — records one send row per person id for a subscriber, ignoring duplicates (`ON CONFLICT(subscriber_id, person_id) DO NOTHING`).
- `FeedbackRepository` — records subscriber feedback votes on persons.
  - `FeedbackRepository.upsert(subscriber_id, person_id, vote)` — inserts or updates (`ON CONFLICT(subscriber_id, person_id)`) a subscriber's vote for a person, refreshing `updated_at`.
