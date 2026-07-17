# Discovery

This module finds new candidate people by expanding out from known signals — via collaborator/graph relationships, provider-based expansion (PDL/Coresignal), curated-lab lead-gen (OpenAlex), fellowship seed lists, entity resolution/dedup, and concentration (school/region overrepresentation) detection.

## backend/discovery/__init__.py
Empty file — the package has no re-exports; submodules are imported directly by their full path.

## backend/discovery/collab_expansion.py
Promotes unresolved public collaboration graph edges (Devpost hackathon teammates, Semantic Scholar/OpenAlex co-authors, and Semantic Scholar paper citations) into full discovery `Person` records with independent evidence. Handles the `hackathon_teammate`, `co_author`, and `paper_citation` graph-edge relationship types (`COLLAB_EDGE_TYPES`).

- `CollaborationExpansionResult` — dataclass holding the promoted people, count of candidates considered, and a per-source (`devpost`/`semantic_scholar`/`openalex`) promotion counter.
- `CollaborationExpander` — resolves dead-end `hackathon_teammate`/`co_author`/`paper_citation` edges (edges with no `target_person_id`) into new or existing `Person` records, capped per run.
  - `CollaborationExpander.__init__(persons, signals, edges, github, devpost, scholar, provider_enricher=None, openalex=None)` — stores the repositories, scrapers, and optional provider enricher used during promotion.
  - `CollaborationExpander.expand(max_promotions=15, follower_cap=2000)` — groups unresolved edges by (edge type, source, normalized target name), attaches groups whose target already resolves to an existing person, otherwise attempts to promote a new person via Devpost (`hackathon_teammate`) or, for `co_author`/`paper_citation`, dispatches by the edge's `source` field to either Semantic Scholar or OpenAlex (both resolved the same way — the target name is just a name to look up against that edge's own source), saves it, optionally runs provider enrichment, and returns a `CollaborationExpansionResult`.
  - `CollaborationExpander._unresolved_groups()` — scans all graph edges and buckets those of a collab edge type with no resolved target into groups keyed by (edge_type, normalized source, normalized target name).
  - `CollaborationExpander._mark_repeats(groups)` — counts the number of distinct projects/papers/edge-ids within each group and writes that count into each edge's `metadata["repeat"]` if it changed, persisting only the changed edges.
  - `CollaborationExpander._promote_devpost(edge, follower_cap)` — resolves the edge's Devpost username (and any linked GitHub login) to an unknown-but-independently-evidenced GitHub profile via `GraphExpander._is_unknown`, scrapes it, and builds a new `Person`/signals pair, or returns `(None, [])` if nothing qualifies.
  - `CollaborationExpander._promote_scholar(edge)` — looks up the edge's target name as a Semantic Scholar author, requires at least one dated paper, and builds a new candidate `Person` with Semantic Scholar contact info, or returns `(None, [])` if the author/lookup is ambiguous or undated.
  - `CollaborationExpander._promote_openalex(edge)` — same as `_promote_scholar` but against OpenAlex (`OpenAlexScraper.find_author`/`collect`), used when the edge's `source == "openalex"`; builds a candidate `Person` with an `openalex_author_id` contact-info entry.
  - `CollaborationExpander._attach(group, person)` — points every edge in the group's `target_person_id`/`target_name` at the resolved or newly-promoted person and persists the edges.

## backend/discovery/concentrations.py
Implements the `ConcentrationDetector` that finds schools, regions, or fellowship programs producing 3 or more flagged candidates and persists them as `Concentration` records.

- `ConcentrationDetector` — computes overrepresentation clusters (concentrations) among a list of flagged persons.
  - `ConcentrationDetector.__init__(repo)` — stores the `ConcentrationRepository` used to persist computed clusters.
  - `ConcentrationDetector.compute(flagged)` — buckets flagged persons by `("school", stripped school name without parenthetical)`, `("region", region)`, and `("program", fellowship name minus its trailing cohort year)`; keeps only buckets with `count >= MIN_CLUSTER` (3), builds a `Concentration` per bucket with member ids/names and today's date, sorts descending by count, replaces all stored concentrations via the repo, and returns the list.

## backend/discovery/entity_resolution.py
Implements the `EntityResolver` (spec §6) that attaches raw signals and graph edges to canonical `Person` records using GitHub username first, then normalized full name, flagging ambiguous name collisions for review instead of merging.

- `normalize_name(name) -> str` — Unicode-normalizes and ASCII-folds a name, lowercases it, strips periods, drops single-character tokens, and collapses more than two name parts down to first+last (dropping middle names/initials).
- `EntityResolver` — idempotent resolver that links `Signal`/`GraphEdge` records to existing `Person` records by github login or normalized name.
  - `EntityResolver.__init__(persons, signals, edges)` — stores the person/signal/edge repositories used for lookups and saves.
  - `EntityResolver._index()` — builds and returns `(by_name, by_github, ambiguous)` indexes over all persons, including their aliases, and records any normalized name that maps to more than one person as ambiguous.
  - `EntityResolver.resolve_signals(signals)` — for each signal, matches it to a person by github login (from `raw_data["login"]`) or normalized name, sets `signal.person_id`, and marks the matched person `needs_review=True` if their normalized name is ambiguous.
  - `EntityResolver.resolve_edges(edges)` — for each edge, matches its source (by `metadata["follower_login"]` or name) and target (by name) to existing persons and sets `source_person_id`/`target_person_id` accordingly.
  - `EntityResolver._match(name, github_login, by_name, by_github) -> Person | None` — static helper that prefers an exact github-login match (case-insensitive) and falls back to a normalized-name lookup.

## backend/discovery/fellowship_seeds.py
Loads a curated, opt-in JSON file of publicly verified fellowship alumni, upserts them as seed `Person` records, and creates same-cohort `fellowship_cohort` graph edges between cohort-mates. Handles the `fellowship_cohort` graph-edge relationship type.

- `FellowshipSeedLoader` — reads an alumni JSON file and materializes fellowship seed people plus cohort edges.
  - `FellowshipSeedLoader.__init__(persons, edges, alumni_file)` — stores the person/edge repositories and the path to the alumni JSON file.
  - `FellowshipSeedLoader.load() -> list[str]` — reads the alumni file's `"alumni"` rows, finds-or-creates a `Person` per row (matched by GitHub username or name, else created with `cohort="seed"` and a `fellowship` label), groups members by `(program, cohort_year)`, creates a `fellowship_cohort` `GraphEdge` between every unordered pair within each cohort that doesn't already exist (in either direction), saves the new edges, and returns the list of collected GitHub usernames to seed further expansion.

## backend/discovery/graph_expansion.py
Implements the `GraphExpander` (spec §10), a one-hop expansion from seed GitHub founder accounts across multiple GitHub relationship surfaces (follows, repo contributors, org membership, stargazers, forkers, issue/PR interactions), keeping only unknown, independently-evidenced candidates. Handles the `github_follows` (with `seed_follows`/`follows_seed` direction metadata), `co_contributor`, `org_mate`, `starred_repo`, `forked_repo`, and `issue_pr_interaction` graph-edge relationship types.

- `GraphExpander` — one-hop GitHub graph expander that turns seed usernames into new discovery people plus the graph edges that link them to their seed.
  - `GraphExpander.__init__(scraper, persons, edges)` — stores the `GithubScraper`, person repository, and graph-edge repository used during expansion.
  - `GraphExpander.expand(seed_usernames, max_per_seed=60, follower_cap=2000, on_progress=None, repos_per_seed=3, contributors_per_repo=30, org_members_per_seed=30, stargazers_per_repo=20, forkers_per_repo=15, interactions_per_repo=20, niche_repo_star_ceiling=2000) -> list[Person]` — for each seed, collects candidate GitHub logins via following/followers, contributors/stargazers/forkers/issue-PR-authors on the seed's top non-fork niche repos (capped by `niche_repo_star_ceiling`), and fellow public-org members; deduplicates links per (login, seed, link_type, metadata); then for each not-already-known candidate login, fetches the profile, filters via `_is_unknown`, scrapes independent evidence, builds a new `Person`, saves it, builds and saves the appropriate directional `GraphEdge`(s) for every link type it was found through, reports progress via `on_progress`, and returns the list of newly discovered people.
  - `GraphExpander._is_unknown(profile, follower_cap) -> bool` — static filter that rejects non-`User` (org/bot) profiles, profiles above `follower_cap` followers, and accounts older than `MAX_ACCOUNT_AGE_YEARS` (13 years), keeping only plausibly pre-breakout individuals.

## backend/discovery/openalex_labs.py
Implements `OpenAlexLabExpander`, an opt-in curated-lab LEAD discovery lane: for a small hand-maintained list of target research labs, pulls recent OpenAlex works matching the lab (by precise institution id or a raw-affiliation-string fallback — no HTML scraping of lab "people" pages) and admits early-career authors as new `discovery` people. Mirrors the curated-seed shape of `fellowship_seeds.py` and the LEAD-lane shape of `provider_expansion.py`, but with no budget/credit system — a target list is small enough to just run.

- `OpenAlexLabResult` — dataclass holding the created people and a count of authors considered.
- `OpenAlexLabExpander` — turns curated lab targets into new discovery people plus `co_author` collaboration edges.
  - `OpenAlexLabExpander.__init__(persons, signals, edges, client, targets_file)` — stores the person/signal/edge repositories, an `OpenAlexClient`, and the path to the curated targets JSON file.
  - `OpenAlexLabExpander.expand(max_new=10, works_per_target=25, on_progress=None) -> OpenAlexLabResult` — loads curated targets (`{school, affiliation, institution_id?, area}`), and for each target's recent works (`OpenAlexClient.works_by_affiliation`, `LOOKBACK_DAYS=730`) processes every not-yet-known co-author: gates them with `OpenAlexScraper.is_early_career`, and for those that pass builds a `Person(cohort="discovery", discovery_origin="openalex_lab", school=target.school, area=target.area)`, a `research_paper` signal, and `co_author` edges to every other author on that same work (genuine collaboration evidence, promotable by `CollaborationExpander`); stops once `max_new` people are created; reports progress via `on_progress`.
  - `OpenAlexLabExpander._process_work(work, target, seen_author_ids, result, max_new)` — per-work admission logic: dedupes authors already seen this run or already a known `Person` (by name), fetches the full author record and applies the early-career gate, and on a pass builds/saves the person, signal, and co-author edges.
  - `OpenAlexLabExpander._build_person(name, target, work)` (static) — constructs the new discovery `Person` from a target/work pair.
  - `OpenAlexLabExpander._build_signals(person, work, target)` — builds the `research_paper` evidence signal (strength 0.6, `source="openalex"`) for a newly admitted person.
  - `OpenAlexLabExpander._build_coauthor_edges(person, names, work)` — builds a `co_author` `GraphEdge` from the new person to every other co-author name on the same work.
  - `OpenAlexLabExpander._load_targets() -> list[dict]` — reads and JSON-parses the targets file, returning `[]` and logging a warning if missing/invalid.

## backend/discovery/provider_expansion.py
Implements `ProviderExpander`, the independent LEAD discovery lane that queries licensed enrichment provider SEARCH APIs (PDL primary, Coresignal independent) for target cohorts of current/recent students and early-career builders at top technical programs, creating `discovery` people with no GitHub account required. Provider-first discovery: each provider's `search_page` is called per configured filter set, budgeted by the SEARCH lane of `ProviderBudget`, with per-run/per-filter checkpointing via `ProviderIdentityRepository` so search resumes via cursor instead of restarting, and a strict dedupe ladder (provider identity → canonical LinkedIn URL → normalized name + school) so PDL/Coresignal search results never create duplicate people; every candidate must also pass confidence and evidence-tier gates before being admitted, and provider errors are logged and skipped rather than raised.

- `ProviderExpansionResult` — dataclass accumulating created people, per-provider counts, and detailed run stats (merged/rejected/attempted/pages/api requests/credit units/verified/review/duplicates/rejection reasons/planned pages).
- `ProviderExpander` — orchestrates budgeted, checkpointed provider search across all configured providers and filter sets, ingesting and deduping results into `Person` records.
  - `ProviderExpander.__init__(providers, persons, identities, enricher, budget, filters_file)` — stores the list of enrichment providers, person repo, provider-identity repo, `ProviderEnricher`, `ProviderBudget`, and path to the discovery filters config.
  - `ProviderExpander.expand(dry_run=False, on_progress=None) -> ProviderExpansionResult` — loads the filter config, and for each provider/filter-set computes remaining SEARCH budget and per-filter checkpoint state, plans a page size respecting caps (`max_results_per_filter`, `max_new_people_per_run`, remaining budget minus search-credit overhead), either simulates it (`dry_run`, no API/spend) or calls `provider.search_page`, ingests each returned record via `_ingest`, spends credits, and records checkpoint progress (cursor/exhausted/outcomes) so subsequent runs resume; returns the aggregated `ProviderExpansionResult`.
  - `ProviderExpander._load_config() -> dict` — reads and JSON-parses the filters file, returning `{}` and logging a warning if it's missing or invalid.
  - `ProviderExpander._ingest(provider, record, today) -> tuple[str, Person | None, str]` — rejects unconfident records, computes an evidence tier via `_evidence_tier`, resolves the record against existing people via `_resolve_existing` (updating and re-linking an existing match), or creates a new `discovery`/`provider_search` `Person` populated from the best recent technical education and enriched via `ProviderEnricher.apply_result`; returns the outcome status, the person (or `None`), and a reason/tier string.
  - `ProviderExpander._resolve_existing(provider, record) -> tuple[Person | None, str]` — dedupe ladder: first matches by provider person id or canonical LinkedIn via the identity repo (`"duplicate"`), then by normalized name plus matching/absent school among all persons (`"merged"`), else returns `(None, "")`.
  - `ProviderExpander._link(provider, record, person_id, today)` — records a provider-identity link (provider name, provider person id or canonical LinkedIn, person id, date) in the identity repository when an id is available.
  - `ProviderExpander._is_confident(record) -> bool` — static gate requiring a multi-token full name and either a canonical LinkedIn URL or a stable provider person id.
  - `ProviderExpander._evidence_tier(record) -> tuple[str | None, str]` — static gate that requires at least one dated technical education entry (else rejected with a reason), then classifies the record as `"verified"` if there's also recent movement (a recent position start or recent provider first-seen date), otherwise `"review"`.
  - `ProviderExpander._effective_filters(provider, filter_set) -> dict` — static helper that strips the `"label"` key and, if the provider declares `supported_search_filters`, restricts the filter set to only supported keys.
  - `ProviderExpander._filter_identity(filters) -> str` — static helper returning a stable SHA-256 hex digest of the canonicalized (sorted-key) JSON filters, used as the checkpoint identity key.
- `_best_education(education) -> Education | None` — returns the education entry with the latest end date, treating `end_date is None` (still enrolled) as the most recent.
- `_recent_education(education) -> bool` — returns whether any education entry in the list is recent per `_education_is_recent`.
- `_education_is_recent(education) -> bool` — returns True if the entry has an end date within `RECENT_EDUCATION_DAYS` (~3 years) of today, or is ongoing (`end_date is None`) with a start date within `CURRENT_EDUCATION_HORIZON` (~5 years).
- `_best_recent_technical_education(education) -> Education | None` — filters to entries that are both technical (`_technical_education`) and recent (`_education_is_recent`), then returns the best one via `_best_education`.
- `_recent_movement(record) -> bool` — returns True if any position started within `RECENT_MOVEMENT_DAYS` (365 days) of today, or the provider's `profile_created_at` is within the last year.
- `_technical_education(education) -> bool` — returns True if the entry's degree/field-of-study text contains `"cs"` as a token or any of `TECHNICAL_EDUCATION_TERMS` (computer, software, engineering, math, physics, robotics, AI/ML, data science, informatics, cyber) as a substring.
- `_date(iso) -> date | None` — parses the first 10 characters of an ISO string into a `date`, returning `None` on missing/invalid input.
- `_year(iso) -> int | None` — returns the year of `_date(iso)`, or `None` if unparseable.
