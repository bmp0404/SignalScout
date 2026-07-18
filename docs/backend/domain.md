# Domain

Plain dataclasses shared across the whole backend, defining the core entities (people, signals, edges, digests, reviews, concentrations, subscribers, discovery recipes) with no business logic attached. Other layers (scrapers, scoring, services, digest, API) all import these types as the common vocabulary for data passed between them.

## backend/domain/__init__.py
Empty package marker file with no exported symbols.

## backend/domain/candidate_review.py
Defines the human launch-review record attached to a candidate before it can ship in a digest.

- `CandidateReview` — frozen dataclass representing a reviewer's verdict on a person; fields: `person_id`, `state` (default `"pending"`), `why_now`, `notes`, `source_bucket`, `contactable` (bool), `primary_evidence_url`, `reviewer`, `approved_at` (nullable), `updated_at`.

## backend/domain/concentration.py
Defines a "hotspot" grouping of flagged candidates sharing a school, program, or region (spec §9).

- `Concentration` — dataclass representing a cluster of people tied to one `kind` (`school`/`region`/`program`) and `key` (e.g. "MIT"); fields: `kind`, `key`, `count`, `person_ids`, `person_names`, `computed_at`, and an auto-generated `id` (uuid4).

## backend/domain/digest.py
Defines the models for the weekly "people you should know" investor email (spec §12).

- `DigestEntry` — dataclass representing one person's card inside a digest; fields: `person_id`, `name`, `score`, `thesis`, `school_line` (e.g. "MIT '26 • AI Research"), `location_line` (e.g. "From Raleigh, NC — now in SF"), `top_signals` (top-3 signal tags), `connection_context`, `warm_intro`, `why_now`, `evidence_links` (list of `{label, url}` dicts), `contact_links` (dict of channel -> URL).
- `Digest` — dataclass representing one generated digest issue; fields: `generated_at`, `entries` (list of `DigestEntry`), `subject`, `html` (rendered body, default empty), and an auto-generated `id` (uuid4).

## backend/domain/discovery_recipe.py
Defines `DiscoveryRecipe`, a named, scheduled, approvable provider-search query — the metadata wrapper layered on top of `ProviderExpander` (see `discovery.md`) so PDL/Coresignal discovery can be run as reusable, operator-managed recipes instead of only the batch `provider_discovery_filters.json` config. `filters` IS the filter_set `ProviderExpander._run_filter_set` already consumes; its `_filter_identity` hash links the recipe to its run-log row in `provider_search_checkpoints`.

- `DiscoveryRecipe` — dataclass representing one recipe; fields: `id`, `name`, `provider` (`"pdl"` | `"coresignal"`), `query_type` (`"student_technical"` | `"founder"` | `"company_first"` — selects the admission gate in `ProviderExpander._ingest`), `filters` dict (the search filter set; for `company_first` recipes this is instead the nested `{"company": {...}, "employee_title": {...}}` shape consumed by `ProviderExpander._run_company_first`), `relative_filters` dict (filter key -> lookback days, computed relative to "today" at run time and merged into `filters` — e.g. `{"job_start_date_gte": 30}` — so recipes never store an absolute date), `default_limit` (default 25), `frequency` (`"weekly"` | `"biweekly"` | `"manual"`), `status` (`"active"` | `"paused"`), `approval_state` (`"pending"` | `"approved"` — a real, non-dry run is refused until this is `"approved"`), `last_run` (nullable ISO timestamp).

## backend/domain/graph_edge.py
Defines the typed connection edge between two people used for the social-graph/connections features, plus the edge-type vocabulary and trust weighting used in scoring.

- `EDGE_TYPES` — tuple constant of the allowed edge type strings (e.g. `github_follows`, `mutual_star`, `starred_repo`, `forked_repo`, `issue_pr_interaction`, `co_author`, `co_contributor`, `org_mate`, `hackathon_teammate`, `fellowship_cohort`, `twitter_follows`, `paper_citation`).
- `EDGE_QUALITY` — dict constant mapping each edge type to a relative trust weight (0.0-1.0) used when scoring connections, e.g. `co_author` = 1.0 (strongest) down to `twitter_follows` = 0.4 (weakest); `paper_citation` = 0.45, same attention tier as `starred_repo` (one-way awareness, not a proven relationship).
- `GraphEdge` — dataclass representing a directed edge between two people; fields: `source_name`, `target_name`, `edge_type`, `observed_date` (ISO date, backtest only counts pre-breakout edges), `source` (data source name), auto-generated `id` (uuid4), optional `source_person_id`/`target_person_id`, and a `metadata` dict.
  - `GraphEdge.__post_init__()` — validates that `edge_type` is one of `EDGE_TYPES`, raising `ValueError` otherwise.

## backend/domain/person.py
Defines the core `Person` entity representing a discovered individual, along with contact/location/scoring extensions beyond the base spec (spec §6).

- `Person` — dataclass representing a discovered individual; fields: `name`, auto-generated `id` (uuid4), `aliases`, `cohort` (`founder`/`control`/`discovery`/`seed`/`demo`/`unknown`, default `"unknown"`), contact fields (`github_username`, `twitter_handle`, `linkedin_url`, `email`, `personal_site`, `contact_info` dict), location/education fields (`school`, `graduation_year`, `origin_location`, `current_location`, `region`), narrative fields (`fellowship`, `breakout_date`, `area`, `thesis`), and scoring/pipeline fields (`score`, `needs_review`, `discovery_origin`, `discovery_source` — e.g. `"pdl_discovery"`/`"coresignal_discovery"` for provider-search recipe hits, or the free-source id (e.g. `"z_fellows"`) for `resolve.py`-identified leads; distinct from `discovery_origin`, which stays a coarser lane label like `"provider_search"` — `evidence_tier`, `review_required`, `enrichment_status`, `enrichment_provider`, `enrichment_updated_at`, `notes`).
  - `Person.display_contacts() -> dict[str, str]` — builds a dict of user-facing contact links (github/linkedin/x/email/site URLs) derived from the raw contact fields, including only channels that are populated.

## backend/domain/signal.py
Defines the standard signal record that every scraper emits (spec §4).

- `Signal` — dataclass representing one piece of evidence about a person; fields: `person_name`, `signal_type` (e.g. `usaco_platinum`, `github_star_project`, `co_author`), `signal_category` (`competition`/`code`/`research`/`hackathon`/`connection`/`fellowship`/`debate`), `signal_date`, `signal_strength` (float 0.0-1.0), `source` (scraper name), `source_url`, `summary`, `raw_data` dict, `metadata` dict, auto-generated `id` (uuid4), and optional `person_id` (set after entity resolution).
  - `Signal.__post_init__()` — validates that `signal_strength` is within `[0.0, 1.0]`, raising `ValueError` otherwise.

## backend/domain/subscriber.py
Defines the subscriber entity used for digest email delivery/preferences.

- `utc_now() -> str` — returns the current UTC time as an ISO 8601 string with second precision.
- `Subscriber` — dataclass representing a digest subscriber; fields: `email`, `frequency`, `preferences` dict, auto-generated `id` (uuid4), auto-generated `unsubscribe_token` (uuid4), `active` (bool, default `True`), `created_at` and `updated_at` (both default to `utc_now()`).
