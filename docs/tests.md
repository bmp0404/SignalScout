# Tests

These files verify graph-expansion/scoring behavior, the subscriber digest pipeline and its API endpoints, small polish-phase API/CLI behaviors, the multi-provider enrichment chain and both provider-search discovery paths (batch `expand()` and the recipe layer's `run_recipe()`, including company-first discovery), free-source (fellowship/competition) lead extraction and resolution, adapter allowlisting/escaping and PDL's scroll-token pagination, and public-release security gating (auth, secret exposure, fail-closed config).

## tests/test_graph_expansion_deepening.py
Covers GitHub REST call shaping, niche-repo surface-based graph expansion (stargazers/forkers/issue-PR interactions), Devpost/Semantic Scholar collaboration promotion with repeat-metadata, discovery-vs-founder scoring compounding, and opt-in idempotent fellowship seed loading, using fake GitHub/Devpost/Scholar clients (`FakeGithubClient`, `FakeDevpost`, `FakeScholarClient`, `RecordingSession`) against a temp SQLite `Container`.

- `test_github_client_uses_direct_capped_rest_endpoints()` — asserts `GithubClient` hits the exact stargazers/forks/issues REST endpoints with correct `per_page` limits and issue `state=all`.
- `test_graph_expansion_adds_precise_niche_repo_surfaces()` — asserts `GraphExpander.expand()` on a niche (low-star) repo adds `starred_repo`/`forked_repo`/`issue_pr_interaction` edges (not `mutual_star`) pointing candidate -> seed, using the configured per-surface limits.
- `test_collaboration_promotes_devpost_and_scholar_with_repeat_metadata()` — asserts `CollaborationExpander.expand()` promotes both a repeat Devpost hackathon teammate and a Semantic Scholar co-author, tagging repeat-project edges with `metadata["repeat"] == 2` and linking all edges to their new `target_person_id`.
- `test_citation_promotes_citing_author()` — asserts `SemanticScholarScraper.collect_citations()` emits a dead-end `paper_citation` edge (source = cited person, target = citing author, no signals for a founder cohort) from `FakeScholarClient.paper_citations()`, and that `CollaborationExpander.expand()` promotes the citing author into a new `Person` and links the edge's `target_person_id`.
- `test_cited_paper_signal_only_for_discovery_cohort()` — asserts `SemanticScholarScraper.collect_citations()` emits a `cited_paper` signal (with `raw_data["citation_count"]`) when the cited person's cohort is `"discovery"`, but emits no signal for the same paper/citations when the cohort is `"founder"` — the gate that keeps a founder's pre-breakout score, and therefore the backtest reference scale, unaffected by this signal.
- `test_discovery_scoring_compounds_distinct_surfaces_but_founders_do_not()` — asserts `ScoringEngine.connection_signal()` applies a `surface_bonus` of 0.2 for discoveries with 3+ distinct connection edge types but 0.0 for founders, and that a repeated edge (`metadata["repeat"]=2`) yields `best_quality == 1.0`.
- `test_fellowship_seed_loader_is_opt_in_and_idempotent()` — asserts `FellowshipSeedLoader.load()` creates exactly one `fellowship_cohort` edge and one `cohort="seed"` person on first run, and produces no duplicates on a second run.

## tests/test_openalex_discovery.py
Covers OpenAlex curated-lab lead-gen and OpenAlex co-author collaboration promotion, using a fake OpenAlex client (`FakeOpenAlexClient`) against a temp SQLite `Container`.

- `test_lab_lead_gen_gates_early_career_and_skips_senior()` — asserts `OpenAlexLabExpander.expand()` creates a `discovery`/`openalex_lab` `Person` (with a signal) for an early-career co-author on a target lab's paper, skips the paper's established (high works-count) co-author entirely (no `Person` created for them), and still records a `co_author` edge to that skipped co-author as collaboration evidence.
- `test_lab_leads_form_a_school_concentration()` — asserts `ConcentrationDetector.compute()` groups 3 same-school discovery people into one `kind="school"` `Concentration` with `count == 3`.
- `test_openalex_co_author_edge_promotes_via_collaboration_expander()` — asserts a dead-end `co_author` edge with `source="openalex"` is promoted by `CollaborationExpander.expand()` via `_promote_openalex` (not the Semantic Scholar path), creating a `discovery_origin="openalex"` `Person` and linking the edge's `target_person_id`.

## tests/test_phase4_digest.py
Covers subscriber subscribe/upsert semantics, digest delivery (send-once, dry-run preview, HTML/plain-text via `ResendSender`), and the digest-related API routes (test-send, signup, feedback, unsubscribe, cron auth) via a `TestClient` against `build_router`.

- `test_subscribe_upserts_without_changing_token()` — asserts re-subscribing the same (case-insensitive) email keeps the same id/unsubscribe token but updates frequency and preferences.
- `test_successful_delivery_records_and_never_repeats()` — asserts `deliver()` sends once (status `"sent"`), records the send so a second call returns `"empty"`, and the message body/footer contain the candidate's evidence text and feedback/unsubscribe links.
- `test_dry_run_does_not_consume_candidate()` — asserts a `dry_run=True` delivery returns `"preview"` status and does not record the candidate as sent.
- `test_resend_sender_uses_html_and_plain_text_transport()` — asserts `ResendSender.send()` posts both HTML and plain-text bodies with a 15s timeout and returns the Resend message id.
- `test_test_digest_endpoint_sends_to_verified_subscriber()` — asserts `POST /api/digest/test` (admin-authorized) sends to a signed-up subscriber and reports the correct candidate count.
- `test_test_digest_endpoint_reports_unconfigured_sender()` — asserts the test-digest endpoint returns 503 with a clear message when no sender is configured, and records no send.
- `test_test_digest_endpoint_rejects_unknown_subscriber()` — asserts requesting a test digest for an unknown email returns 401 and sends nothing.
- `test_test_digest_endpoint_rate_limits_for_24_hours()` — asserts a second test-digest request for the same subscriber within 24 hours returns 429 and only one email is ever sent.
- `test_signup_feedback_unsubscribe_and_cron_auth()` — asserts subscriber signup never returns the raw token, seed accounts parse correctly, `/api/digest/cron` requires bearer auth, and feedback/unsubscribe endpoints require a GET confirmation step before the POST actually records the vote/unsubscribe.

## tests/test_phase5_polish.py
Covers the minimal-field analytics page-view endpoint and the `build_db.py --if-empty` CLI behavior of never overwriting existing seeded data.

- `test_page_view_endpoint_stores_only_minimal_fields()` — asserts `POST /api/analytics/page-view` stores exactly `{id, path, viewed_at, referrer}` and nothing else.
- `test_page_view_rejects_absolute_external_urls()` — asserts posting an absolute external URL as `path` is rejected with 422.
- `test_if_empty_seed_does_not_replace_existing_people()` — runs `scripts/build_db.py --if-empty` as a subprocess twice (inserting a manual row between runs) and asserts the second run leaves the manually inserted row and existing count untouched, only the initial seed count remains constant.

## tests/test_free_source_discovery.py
Covers Phase 3 free-source discovery: generic HTML lead extraction (`extract_leads`), the `ConfigSourceScraper` base class (`FellowshipScraper`/`CompetitionScraper`), and `LeadResolver`'s dedupe-first + paid-lookup-fallback resolution. Reuses `ChainTestBase`/`FakeProvider`/`make_result` from `test_provider_diversification.py` (imported directly, no shared `tests/__init__.py`) so budget/cache setup stays identical to the provider-search tests.

- `ExtractLeadsTests`
  - `test_name_near_linkedin_link_becomes_a_lead()` — asserts a name adjacent to a LinkedIn `href` in raw HTML produces one `RawLead` with the LinkedIn URL captured.
  - `test_name_near_github_link_captures_username()` — asserts a name adjacent to a GitHub `href` captures the GitHub username.
  - `test_name_with_no_nearby_link_is_dropped()` — asserts a name with no link anywhere in its window produces no lead.
  - `test_empty_html_returns_no_leads()` — asserts empty input returns `[]`.
  - `test_duplicate_names_on_one_page_are_deduped()` — asserts the same name appearing twice on one page (even with different nearby links) only produces one lead.
  - `test_max_leads_is_respected()` — asserts `extract_leads(..., max_leads=3)` never returns more than 3 leads even when more qualifying names are present.
- `ConfigScraperTests`
  - `test_fellowship_scraper_is_a_config_source_scraper()` — asserts `FellowshipScraper` subclasses `ConfigSourceScraper` with `name == "fellowship"`.
  - `test_competition_scraper_is_a_config_source_scraper()` — asserts `CompetitionScraper` subclasses `ConfigSourceScraper` with `name == "competition"`.
  - `test_scrape_extracts_leads_from_configured_sources()` — asserts `scrape()` fetches every configured source and runs its HTML through `extract_leads`, using a fake `_Session`/`_Resp`.
  - `test_missing_sources_file_is_fail_soft()` — asserts a nonexistent sources file makes `scrape()` return `[]`, not raise.
  - `test_bad_http_status_is_fail_soft()` — asserts a non-200 response from a source URL is skipped, not raised.
- `LeadResolverTests` (`ChainTestBase`)
  - `test_lead_matching_existing_candidate_by_linkedin_is_skipped()` — asserts a lead whose LinkedIn URL matches an existing person (linked via `identities.link`) is bucketed into `matched`, no provider call is made, and no duplicate person is inserted.
  - `test_lead_matching_existing_candidate_by_name_and_school_is_skipped()` — asserts the name+school fallback ladder tier also skips the paid lookup.
  - `test_unresolved_lead_tries_pdl_identify_before_giving_up()` — asserts a lead with no existing match calls `enricher.run()`, creates a `discovery_source`-tagged `Person` on a match, and inserts exactly one person.
  - `test_pdl_identify_miss_leaves_lead_unresolved_and_uninserted()` — asserts a definitive PDL miss buckets the lead into `unresolved` and inserts nobody (the tentative row is rolled back via `persons.delete`).
  - `test_provider_outage_leaves_lead_unresolved_without_billing()` — asserts a transient provider error also leaves the lead unresolved/uninserted and spends no budget (mirrors `ProviderEnricher`'s "error is not a definitive miss" contract).

## tests/test_producthunt_scraper.py
Covers `ProductHuntScraper`'s pure-parse regex logic (the part that runs on already-rendered HTML) and its fail-soft behavior when Playwright isn't installed. Real Playwright browser rendering is exercised live, not in this suite — the same boundary `test_free_source_discovery.py`'s `ConfigScraperTests` draws around `requests.Session` via a fake `_Session`/`_Resp`, just one level further out since this scraper's fetch step is a full browser render rather than a single GET.

- `ProductUrlsTests`
  - `test_extracts_post_and_product_links()` — asserts both `/posts/<slug>` and `/products/<slug>` link shapes are extracted and resolved to absolute URLs.
  - `test_deduplicates_repeated_links()` — asserts the same product link appearing twice on a leaderboard page only produces one URL.
  - `test_no_product_links_returns_empty()` — asserts HTML with no product links returns `[]`.
- `MakersTests`
  - `test_extracts_maker_name_and_profile_link()` — asserts a `/@username` link with name-like anchor text produces one `RawLead` with `personal_site` set to the absolute PH profile URL and no `linkedin_url`.
  - `test_non_name_link_text_is_skipped()` — asserts a `/@username` link whose anchor text isn't name-like (e.g. "Follow") produces no lead.
  - `test_duplicate_maker_on_one_page_is_deduped()` — asserts the same maker profile link appearing twice on one product page only produces one lead.
  - `test_no_maker_links_returns_empty()` — asserts HTML with no maker profile links returns `[]`.
- `ScrapeFailSoftTests`
  - `test_missing_sources_file_is_fail_soft()` — asserts a nonexistent sources file makes `_sources()` return `[]`, not raise.
  - `test_scrape_without_playwright_installed_returns_empty()` — asserts `scrape()` degrades to `[]` (not an `ImportError`) in this repo's test environment, which has no Playwright installed.

## tests/test_provider_diversification.py
Fixture-driven tests (HTTP layer mocked, providers faked; never spend real credits) covering the PDL->Coresignal enrichment chain (caching, budgets, fallback, fail-soft outages), both provider-search discovery paths — batch `ProviderExpander.expand()` (no-GitHub candidates, cross-provider dedupe/merge, pagination checkpointing, evidence-tier classification) and the recipe layer's `ProviderExpander.run_recipe()` (approval gating, founder vs. student_technical admission, relative-date filter computation, dedupe, hard limits, budget exhaustion, and company-first discovery) — adapter allowlisting/escaping for PDL and Coresignal query builders, raw HTTP response mapping for both adapters (including PDL's `scroll_token` pagination), and an unchanged-founder-backtest regression guard. `ChainTestBase` exposes shared `_filters_file`/`_expander` helpers used by every provider-search test class in this file (and reused by `test_free_source_discovery.py`).

- `test_cache_prevents_repeat_calls_within_ttl()` — asserts a second `enrich()` call for the same person returns no new signals and the provider is called only once (served from cache).
- `test_cached_miss_is_not_refetched()` — asserts a definitive no-match is cached so the provider isn't called again on retry.
- `test_pdl_miss_falls_through_to_coresignal()` — asserts a clean PDL miss falls through to Coresignal and the outcome reports `fallback=True`.
- `test_confident_pdl_match_does_not_reach_coresignal()` — asserts a successful PDL match short-circuits the chain so Coresignal is never called (no double charge).
- `test_provider_outage_is_failsoft_and_not_cached()` — asserts an HTTP-error outage does not raise, is not cached (retryable), and stops the chain without calling the next provider.
- `test_budget_stops_cleanly()` — asserts once the monthly enrich-lane budget is exhausted, further runs return `"skipped"` (not an error) without calling the provider.
- `test_founder_gets_contacts_but_no_scored_signals()` — asserts enriching a founder merges contact info but emits zero scored signals (backtest protection).
- `test_dry_run_spends_nothing()` — asserts `dry_run=True` never calls the provider, never caches, and never records usage.
- `test_sqlite_workers_do_not_share_connection()` — asserts two threads get distinct SQLite connection objects (thread-local connections).
- `test_legacy_provider_identity_backfill_is_idempotent()` — asserts constructing a `PersonRepository` backfills `discovery_origin` for legacy provider-linked people (`provider_search` vs `github`) and is stable across repeated construction.
- `test_search_creates_candidate_without_github()` — asserts `ProviderExpander.expand()` creates a discovery-cohort person with no GitHub username, a resolved LinkedIn URL, `discovery_lane="provider_search"`, and at least one evidence signal.
- `test_duplicate_provider_records_merge()` — asserts a second `expand()` call for an already-completed, checkpointed page creates no new people and makes no extra search calls.
- `test_dedupe_across_providers_by_linkedin()` — asserts the same person returned by two different providers (matched by LinkedIn URL) is merged into one person, counted as a duplicate.
- `test_pagination_resumes_next_page_without_repeating()` — asserts successive `expand()` calls advance the search cursor page-by-page without repeating results, and the checkpoint records total pages/records and exhaustion.
- `test_review_tier_emits_only_dated_education()` — asserts a record with no positions/undated profile is classified `evidence_tier="review"`, flagged `review_required`, and only an `education_signal` is emitted.
- `test_verified_tier_requires_dated_movement()` — asserts a record with dated position/education movement is classified `evidence_tier="verified"` with `review_required=False`.
- `test_recent_technical_education_is_selected_over_undated_entry()` — asserts the person's `school` field picks a recent, dated technical-education entry over an undated business-school entry.
- `test_undated_technical_education_is_rejected()` — asserts a record whose only education entry is undated is rejected with reason `undated_or_stale_education`.
- `test_search_error_is_audited_without_advancing_or_spending()` — asserts a provider search HTTP error is recorded in the checkpoint (`error_count`, `last_outcome`) without advancing the page or consuming search-lane usage.
- `RecipeTests` (recipe-layer engine tests, via `ProviderExpander.run_recipe`)
  - `test_recipe_run_requires_approval()` — asserts a real (non-dry) run of a `pending`-approval recipe raises `PermissionError` before ever calling the provider.
  - `test_recipe_run_requires_approval_even_when_provider_unconfigured()` — regression test for an ordering bug caught during live testing: asserts the approval check runs *before* the provider lookup, so an unapproved recipe still raises `PermissionError` even when its provider isn't in `self.providers` (previously the provider-lookup short-circuit skipped the approval check entirely).
  - `test_recipe_dry_run_allowed_without_approval_and_spends_nothing()` — asserts a `pending`-approval recipe's dry run still returns a result, never calls the provider, inserts nobody, and spends no budget.
  - `test_founder_admission_admits_without_technical_education()` — asserts a `query_type="founder"` recipe admits a record with an MBA (non-technical) education as long as it has a founder-titled position, tagging the created person's `discovery_source` as `"pdl_discovery"`.
  - `test_student_technical_admission_rejects_same_record_without_technical_education()` — the contrasting case: the identical record run through a `query_type="student_technical"` recipe is rejected with reason `"nontechnical_or_missing_education"` — proves `query_type` actually changes the admission outcome, not just cosmetically.
  - `test_relative_filters_computed_at_run_time()` — asserts a recipe's `relative_filters={"job_start_date_gte": 30}` reaches the provider as an absolute date (today minus 30 days), recomputed fresh on every run.
  - `test_recipe_dedupes_against_existing_github_candidate_by_name_and_school()` — asserts a recipe run against a name+school match to an existing GitHub-origin person merges into that person (not a new insert) and marks it provider-enriched.
  - `test_recipe_hard_caps_results_at_default_limit()` — asserts a recipe never creates more people than its `default_limit`, even when the provider returns more.
  - `test_recipe_budget_exhausted_blocks_real_run()` — asserts a recipe with `pdl_monthly_cap=0` creates nobody and never calls the provider (fail-soft budget stop, not an error).
- `FakeCompanyFirstProvider` — `FakeProvider` subclass adding `search_companies`/`search_company_employees` (with call-count tracking) for testing `_run_company_first`.
- `CompanyFirstRecipeTests` (`ChainTestBase`)
  - `test_company_first_creates_candidates_via_shared_ingest()` — asserts a `company_first` recipe run calls `search_companies` then `search_company_employees` per company, creating a person via the same `_ingest` path (`discovery_source="coresignal_discovery"`).
  - `test_company_first_dry_run_spends_nothing()` — asserts dry-run mode calls `search_companies` (to plan) but never `search_company_employees` (step 2 never runs), and spends no budget.
  - `test_company_first_requires_approval()` — asserts the same approval gate `run_recipe` enforces for filter-set recipes also applies to `company_first` recipes.
  - `test_company_first_dedupes_across_companies()` — asserts the same person returned by two different companies' employee searches is deduped (one created, one counted as a duplicate).
  - `test_search_and_collect_credits_tracked_separately()` — asserts `recipe_checkpoint(recipe)` reports non-zero `search_credit_units` and `collect_credit_units` after a company-first run.
- `test_queue_prioritizes_high_scoring_github_only_candidates()` — asserts `ProviderEnricher.prioritize()` orders pending GitHub-only candidates by descending score.
- `test_enrichment_statuses_cover_match_miss_error_and_budget()` — asserts `enrichment_status` is set correctly for matched, no-match, error, and budget-exhausted (`pending_budget`) outcomes.
- `test_candidate_payload_exposes_rebalance_metadata()` — asserts `CandidateService.list_candidates()` payload surfaces `discovery_origin`, `evidence_status`/`evidence_tier`, `review_required`, and `enrichment_status`.
- `test_low_confidence_records_rejected()` — asserts an ambiguous record (no LinkedIn, single-token name, no evidence) is rejected and creates no person.
- `test_search_dry_run_spends_nothing()` — asserts a dry-run search expansion creates no people, makes no provider calls, writes no checkpoints, and records no usage.
- `test_search_budget_limits_records()` — asserts the number of created people from a search run never exceeds the computed search-lane budget cap.
- `test_pdl_search_where_is_allowlisted_and_escaped()` — asserts `PdlProvider._build_where()` escapes single quotes, converts list filters to `IN (...)`, and silently drops unknown/unallowlisted filter keys (e.g. SQL-injection-shaped values).
- `test_pdl_escape_strips_control_chars()` — asserts `PdlProvider._escape()` correctly escapes quotes and strips newlines.
- `test_coresignal_filters_are_allowlisted()` — asserts `CoresignalProvider._build_filters()` maps only allowlisted keys (school/location) and drops unknown ones.
- `test_pdl_enrich_maps_200_and_handles_404_and_error()` — asserts `PdlProvider.enrich_person()` maps a 200 response to a populated `EnrichmentResult`, treats 404 as a clean cacheable miss (`last_error=None`), and treats 401 as a non-cacheable error (`last_error="HTTP 401"`).
- `test_pdl_search_page_first_request_omits_scroll_token()` — asserts the first `search_page()` call (no cursor) sends neither `scroll_token` nor the deprecated `from` param, and that a `scroll_token` present in the response with a full page of results yields `exhausted=False`/`next_cursor` set to that token.
- `test_pdl_search_page_resumes_with_scroll_token()` — asserts a resumed call sends `scroll_token` (not `from`) equal to the given cursor, and that a response with fewer records than requested and no `scroll_token` yields `exhausted=True`.
- `test_coresignal_search_page_offsets_collects_and_counts_requests()` — asserts `CoresignalProvider.search_page()` tracks API request/credit counts across a paginated collect-then-detail-fetch flow and resumes correctly from a serialized cursor.
- `test_founder_backtest_unchanged()` — regression guard asserting the founder backtest recall (`70.0%`) and false-positive rate (`1.7%`) are unchanged against the real seeded `signal_scout.db` (skipped if that DB or founders aren't present). Currently failing against the live `signal_scout.db` in this repo (`56.7%` recall) — pre-existing, unrelated to the provider-discovery work in this doc revision; confirmed present on an unmodified checkout too.

## tests/test_public_release_security.py
Covers that product routes are open, that cron delivery still requires a bearer secret, that preview does not record a real send, that one-click approve flips `approval_state`, that public signup never leaks the subscriber's action token, and that production config fails closed without `CRON_SECRET`.

- `test_candidate_and_operator_routes_are_open()` — asserts `/api/candidates`, `/api/overview`, discovery status/recipes/cost-summary, and candidate-reviews all return 200 without auth.
- `test_cron_route_still_requires_secret()` — asserts `POST /api/digest/cron` returns 401 without bearer and 200 with the configured cron secret.
- `test_preview_works_without_bearer()` — asserts `/api/digest/preview` returns the expected approved candidate but does not record it as sent to the subscriber.
- `test_one_click_approve_updates_state()` — asserts `PUT /api/candidate-reviews/{id}` with `{state:"approved"}` persists and surfaces on `/api/candidates`.
- `test_public_signup_does_not_expose_action_token()` — asserts `POST /api/subscribers` response never includes `subscriber_token`.
- `test_production_requires_cron_secret()` — asserts constructing a `Container` with `environment="production"` and empty `cron_secret` raises `RuntimeError` mentioning `CRON_SECRET`.

## tests/test_discovery_cron.py
Covers scheduled discovery: seed auto-approve, `is_due` cadence, `run_due` last_run bookkeeping, and the gated `/api/discovery/cron` endpoint.

- `test_seed_recipes_are_auto_approved()` — asserts every seeded recipe lands as `approval_state == "approved"`.
- `test_is_due_respects_frequency_and_last_run()` — asserts weekly due/not-due windows, and that `manual` / `paused` recipes are never due.
- `test_run_due_marks_last_run_when_provider_missing()` — asserts `run_due` records `last_run` even when providers are unconfigured (no-op expand), and a second tick finds nothing due.
- `test_discovery_cron_requires_secret()` — asserts `POST /api/discovery/cron` returns 401 without bearer and 200 with `CRON_SECRET`.
