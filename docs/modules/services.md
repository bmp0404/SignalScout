# Services

This module holds application-level orchestration classes that sit above repositories/scoring/discovery/enrichment/digest and are what the API layer (backend/api/routes.py) actually calls, wired together in backend/container.py.

## backend/services/__init__.py
Empty package marker file with no exported symbols.

## backend/services/candidate_review.py
Validation and workflow rules for the human launch-approval process on discovery candidates.

- `_public_http_url(value)` — checks whether a string is a well-formed public http(s) URL (used to validate evidence links).
- `CandidateReviewService` — orchestrates recording and validating human review decisions on discovery candidates, backed by `CandidateReviewRepository`, `PersonRepository`, and `SignalRepository`.
  - `CandidateReviewService.review(person_id, state, why_now, notes, source_bucket, contactable, primary_evidence_url, reviewer) -> CandidateReview` — loads the person (must exist and be in the "discovery" cohort), runs approval validation when `state == "approved"`, then upserts the review record via the reviews repository.
  - `CandidateReviewService.list_rows(state=None) -> list[dict]` — fetches reviews (optionally filtered by state), joins each to its person, and returns dicts merging review fields with the person's name and display contacts.
  - `CandidateReviewService.approved_mix() -> dict[str, int]` — counts approved, contactable reviews by `source_bucket`, returned sorted by bucket name.
  - `CandidateReviewService._validate_approval(person_id, name, why_now, source_bucket, contactable, primary_evidence_url, has_contact)` — enforces approval gating rules: a real anchored name, a why-now of 30+ characters, a valid public evidence URL that must already exist among the person's persisted signal `source_url`s, a non-empty source bucket, and at least one contact route; raises `ValueError` on any violation.

## backend/services/candidate_service.py
Scores the live cohort and builds the payloads the UI needs — score receipts, connection context, warm-intro paths, and "why now" lines.

- `CandidateService` — orchestrates scoring and candidate-facing read views, composing `PersonRepository`, `SignalRepository`, `GraphEdgeRepository`, `ScoringEngine`, and (optionally) `CandidateReviewRepository`.
  - `CandidateService.__init__(persons, signals, edges, engine, flag_threshold, reviews=None)` — stores the repositories, scoring engine, the score threshold used to flag standout candidates, and an optional reviews repository for review-state enrichment.
  - `CandidateService.rescore_all() -> dict[str, float]` — for every founder/discovery/demo person, builds their signals plus a synthetic connection signal to founder "seeds", computes a base score via `ScoringEngine.compute`, applies a knownness down-weight, calibrates all scores against a founder reference pack (`founder_reference`) via `ScoringEngine.normalize_calibrated`, persists each score with `persons.update_score`, and returns the normalized score map.
  - `CandidateService._knownness_factor(person) -> float` (static) — down-weights already-famous GitHub accounts (by follower count bands: <=500 → 1.0, <=1000 → 0.7, <=2000 → 0.5, else 0.3) so ranking favors pre-breakout people; founders/seeded profiles without a follower count are unaffected.
  - `CandidateService.list_candidates(cohort="discovery") -> list[dict]` — lists scored people in a cohort (or all non-control people if `cohort` is falsy), sorted by score descending, each rendered via `_summary`.
  - `CandidateService.profile(person_id) -> dict | None` — builds a full candidate profile: signals plus a synthetic founder-connection signal, a score breakdown, a chronological signal timeline, founder connections, and contact info, layered on top of `_summary`.
  - `CandidateService._summary(person, founders_by_id) -> dict` — assembles the compact candidate card: identity/location/school fields, top 3 signals, source counts/diversity, GitHub follower count, founder-connection count/context/warm-intro, why-now text (preferring a human-reviewed one), review/approval state, contact links, evidence coverage, and enrichment status.
  - `CandidateService._connection_list(person, edges, founders_by_id) -> list[dict]` — turns edges linking the person to known founders into human-readable connection descriptions (via `EDGE_VERBS`), sorted by observed date.
  - `CandidateService._seed_edges(person, edges, founders_by_id) -> list[GraphEdge]` — filters a person's edges down to those connecting them to a founder ("seed").
  - `CandidateService._connection_context(seed_edges, founders_by_id) -> str` (static) — summarizes founder connections into one sentence, grouping "follows" edges together and listing up to two other relationship types.
  - `CandidateService._warm_intro(person, seed_edges, founders_by_id) -> str` (static) — picks the highest-quality founder edge (by `EDGE_QUALITY`) and phrases a "reach out via ..." suggestion.
  - `CandidateService._why_now(signals) -> str` (static) — generates a one-line rationale: prefers a signal showing a >=1.8x star-count jump, else describes the most recent signal, phrased differently depending on whether it's within the last year.
  - `CandidateService._source_counts(signals) -> dict[str, int]` (static) — counts signals per source, sorted descending by count.
  - `CandidateService.source_mix(cohort="discovery") -> dict[str, int]` — aggregates signal counts by source across an entire cohort, to show whether GitHub's share of signals is falling.
  - `CandidateService._coverage(signals) -> str` (static) — classifies evidence coverage as HIGH (3+ distinct sources), MED (2), or LOW (0-1).
  - `CandidateService._breakdown_dict(breakdown) -> dict` (static) — converts a `ScoreBreakdown` dataclass into a plain dict (raw, items, diversity multiplier, recency bonus, age factor, adjusted score).

## backend/services/discovery_job.py
`DiscoveryJobManager` runs the live discovery pipeline in a background thread and exposes in-memory stage progress for polling.

- `DiscoveryJobManager` — orchestrates a single global, threaded discovery run through four UI-facing stages (Scrape -> Resolve -> Enrich -> Score), building its own `Container` (own SQLite connection) per run so writes never collide with the API's read connection.
  - `DiscoveryJobManager.__init__(settings, container_factory)` — stores settings, a factory that builds a fresh `Container`, a `threading.Lock`, and initializes idle in-memory state.
  - `DiscoveryJobManager._idle_state() -> dict` (static) — returns the initial/reset job-state dict (job id, state, per-stage status/counts, per-source discovery counts, timestamps, error).
  - `DiscoveryJobManager.status() -> dict` — returns a deep copy of the current in-memory job state (cheap, DB-free, for polling).
  - `DiscoveryJobManager.start() -> str` — guards against a concurrently running job, requires at least one configured discovery lane (GitHub token or a provider chain), then generates a job id, resets state to "running", and launches `_run` on a daemon thread.
  - `DiscoveryJobManager._resolve_seeds(container) -> list[str]` — prefers curated `demo_seeds` from the seed-accounts file that already exist in the DB, else falls back to founder `github_seeds`; optionally appends fellowship-alumni seeds via `FellowshipSeedLoader`; truncates to `discovery_seed_limit`.
  - `DiscoveryJobManager._set_stage(name, status=None, count=None)` — thread-safely updates one pipeline stage's status/count in the shared state.
  - `DiscoveryJobManager._set_source_count(source, count)` — thread-safely updates a per-source discovery count.
  - `DiscoveryJobManager._run_provider_lane(container) -> list` — runs budgeted provider-search discovery (PDL -> Coresignal) via `container.provider_expander.expand`, records per-source counts, logs a summary, and returns newly-created discovery people.
  - `DiscoveryJobManager._run_openalex_lab_lane(container) -> list` — opt-in (`settings.discovery_include_openalex`) curated-lab lead-gen via `container.openalex_lab_expander.expand`, logs a summary, and returns newly-created discovery people; a no-op returning `[]` when the flag is off.
  - `DiscoveryJobManager._run_github_lane(container, token, on_progress) -> tuple[GithubScraper, list]` — resolves seeds, builds a `GithubScraper`/`GraphExpander`, and expands from seeds (capped per-seed/per-repo/per-org limits for a short on-camera run), returning the scraper and discovered people.
  - `DiscoveryJobManager._save_collected(container, signals, edges)` (static) — resolves and persists a batch of signals and edges via `container.resolver` and the signals/edges repositories.
  - `DiscoveryJobManager._run_collaboration_lane(container, github, fresh_people) -> list[Person]` — collects capped Semantic Scholar (up to 8, both co-authorship via `collect()` and citation edges via `collect_citations()`, sharing one resolved author lookup), OpenAlex (up to 8, co-authorship via `collect()`), and Devpost (up to 8) collaboration evidence for fresh plus existing discovery people, then runs `CollaborationExpander.expand` (using the GitHub scraper, Devpost/Scholar/OpenAlex scrapers, and `provider_enricher`) to promote dead-end/collaboration/citation hits into discovery people, recording per-source counts.
  - `DiscoveryJobManager._run(job_id)` — the pipeline entrypoint executed on the background thread: builds a Container, runs the provider lane first (primary source, needs no GitHub account), then the opt-in OpenAlex lab lane (also no GitHub account needed), then the GitHub lane if a token is configured, then the collaboration lane on the combined fresh people; marks scrape/resolve done; enriches each GitHub/collaboration person's contact info and location (scraping fresh signals for GitHub-sourced people) and saves them; runs the provider-enrichment queue (`container.provider_enricher.prioritize`/`.run`) over pending discovery people with a GitHub username; marks enrich done; calls `container.candidate_service.rescore_all()` and marks score done; on any exception, logs it, marks the job "error" with the exception message, and flips any still-active stage to "error"; always closes the container's DB connection in a `finally` block.

## backend/services/subscriber_digest.py
Builds and delivers personalized, never-repeat subscriber digests (email HTML/text) and drives the scheduled digest run.

- `SubscriberDigestService` — orchestrates digest content selection, rendering, and delivery, composing `SubscriberRepository`, `DigestSendRepository`, `CandidateService`, `EmailSender`, and `EmailActionSigner`.
  - `SubscriberDigestService.__init__(subscribers, sends, candidates, sender, public_base_url, action_signer, size=10)` — stores the repositories/services/sender, the public base URL (used for feedback/unsubscribe links), the action signer, and the default digest size (10 picks).
  - `SubscriberDigestService.build(subscriber) -> tuple[EmailMessage, list[str]]` — pulls approved+contactable discovery candidates the subscriber hasn't already been sent (via `sends.sent_person_ids`), sorts by approval recency then a subscriber-preference rank, takes the top `size`, and renders both an `EmailMessage` and the list of picked person ids.
  - `SubscriberDigestService.preview(subscriber) -> dict` — calls `build`, re-hydrates the picked candidates' full dicts, computes a source-bucket mix, and returns subject/html/text plus candidate details for preview purposes (no send, no record).
  - `SubscriberDigestService.deliver(subscriber, dry_run=False) -> dict` — calls `build`; if there are no picks, returns an "empty" status; if `dry_run`, returns a "preview" payload without sending; otherwise sends via `self.sender.send`, records the sent person ids in `sends.record_many` on success, and returns a status of sent/preview/failed based on the sender's receipt.
  - `SubscriberDigestService.run_due(dry_run=False, recipient=None, now=None) -> dict` — rescoring the candidate pool first (`candidates.rescore_all()`), fetches active subscribers (optionally filtered to one recipient for testing), filters to those due today by frequency (daily always, weekly only on Monday) when no specific recipient is given, delivers to each, and returns aggregate run stats (subscriber count, sent count, per-subscriber results). No 24-hour per-subscriber test-digest rate limit is implemented in this file.
  - `SubscriberDigestService._preference_rank(candidate, subscriber) -> tuple[int, float]` (static) — scores how well a candidate matches the subscriber's `signal_interests` preference terms against the candidate's area/why-now/top-signal text, returning (match count, score) as a sort key.
  - `SubscriberDigestService._feedback_url(subscriber, person_id, vote) -> str` — builds a signed feedback URL (via `action_signer.issue`) for a subscriber to vote up/down on a specific candidate in the digest.
  - `SubscriberDigestService._unsubscribe_url(subscriber) -> str` — builds a signed unsubscribe URL for the subscriber.
  - `SubscriberDigestService._render_html(subscriber, picks, today) -> str` — renders the full HTML email body: per-candidate sections (score, name, context, why-now description, triggering signals, contact links, primary-evidence link, feedback up/down links) plus a header and unsubscribe footer.
  - `SubscriberDigestService._render_text(subscriber, picks, today) -> str` — renders the plain-text equivalent of the digest email with the same per-candidate content and an unsubscribe line.
