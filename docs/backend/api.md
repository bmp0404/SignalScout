# api

The `api` module exposes the backend's public HTTP surface via a FastAPI router; it sits at the very end of the pipeline (domain -> repositories -> scrapers -> scoring/backtest -> discovery/enrichment -> digest -> api), translating HTTP requests into calls on the services wired up by `backend/container.py` and never containing business logic itself.

## backend/api/__init__.py
Empty file; the `api` package has no re-exports.

## backend/api/routes.py
Defines the `build_router(container)` factory that assembles every `/api/*` FastAPI route as a thin delegate to `Container` services, plus request models, rate limiting, auth guards, and small HTML response helpers.

- `EMAIL_RE` — module-level compiled regex used to validate email address format across handlers.
- `SubscriberSignup` — Pydantic request model for new digest subscriber signups (email, frequency, signal interests, seed accounts).
- `TestDigestRequest` — Pydantic request model holding the target email for an operator-triggered test digest send.
- `PageViewEvent` — Pydantic request model for client-side analytics page-view beacons (path, optional referrer).
- `CandidateReviewRequest` — Pydantic request model for an operator's review decision on a discovery candidate (state, notes, evidence, reviewer, etc.).
- `build_router(container: Container) -> APIRouter` — constructs and returns the `/api`-prefixed router, defining a per-process in-memory rate-limit bucket and every route handler as a closure over `container`.
  - `rate_limit(request, key, limit, window) -> None` (nested) — sliding-window rate limiter keyed by client IP and an action key, using an in-memory `deque`; raises HTTP 429 once the limit is exceeded within the window.
  - `health()` — `GET /api/health` — runs a trivial `SELECT 1` against the database and returns `{"status": "ok", "db": ...}` as a liveness/DB-connectivity check.
  - `subscribe(payload, request)` — `POST /api/subscribers` — rate-limits signups, validates and normalizes the email/frequency, parses comma-separated seed accounts, and delegates persistence to `container.subscribers.subscribe(...)`.
  - `send_test_digest(payload, request, authorization)` — `POST /api/digest/test` — requires admin auth, rate-limits per subscriber, enforces production-only owner-email restriction and a 24-hour resend cooldown (via `container.digest_sends`), then delegates actual delivery to `container.subscriber_digest.deliver(...)`.
  - `record_page_view(payload, request)` — `POST /api/analytics/page-view` (202) — rate-limits, validates the path is relative, and records the event via `container.page_views.record(...)`.
  - `overview()` — `GET /api/overview` — aggregates backtest metrics (`container.backtest.run()`), discovery counts/flags, production-only approval filtering, and provider-search verification stats into a single dashboard summary payload.
  - `candidates(cohort)` — `GET /api/candidates` — lists candidates for a cohort via `container.candidate_service.list_candidates(...)`, filtering to approved-only when in production and cohort is "discovery".
  - `candidate(person_id)` — `GET /api/candidates/{person_id}` — fetches a single candidate profile via `container.candidate_service.profile(...)`, 404ing if missing or (in production) not yet approved.
  - `backtest()` — `GET /api/backtest` — returns raw backtest results from `container.backtest.run()`.
  - `concentrations()` — `GET /api/concentrations` — returns all detected signal concentrations via `container.concentrations.all()`.
  - `latest_digest(authorization)` — `GET /api/digests/latest` — admin-only; returns the most recently generated digest via `container.digests.latest()`.
  - `generate_digest(authorization)` — `POST /api/digests/generate` — admin-only; triggers `container.digest_generator.generate()` and returns the new digest.
  - `run_discovery(request, authorization)` — `POST /api/discovery/run` — admin-only, rate-limited (2/hour); starts an async discovery job via `container.discovery_job.start()`, mapping a running-job or missing-token error to 409/400. This is the "OG" batch pipeline (see `services.md`'s `DiscoveryJobManager`) — separate from the recipe endpoints below, though both share the same `ProviderBudget`.
  - `discovery_status(authorization)` — `GET /api/discovery/status` — admin-only; returns `container.discovery_job.status()`.
  - `list_discovery_recipes(authorization)` — `GET /api/discovery/recipes` — admin-only; returns `container.discovery_recipe_service.list_recipes()`.
  - `approve_discovery_recipe(recipe_id, authorization)` — `POST /api/discovery/recipes/{recipe_id}/approve` — admin-only; calls `container.discovery_recipe_service.approve(...)`, mapping an unknown recipe id to HTTP 404.
  - `run_discovery_recipe(recipe_id, limit, authorization)` — `POST /api/discovery/recipes/{recipe_id}/run` — admin-only; calls `container.discovery_recipe_service.run(recipe_id, override_limit=limit)`, mapping an unknown recipe id to 404 and an unapproved-recipe `PermissionError` to 403. No separate rate limit beyond the recipe's own budget/approval gate (unlike `/discovery/run`, which also has an IP rate limit).
  - `dry_run_discovery_recipe(recipe_id, limit, authorization)` — `POST /api/discovery/recipes/{recipe_id}/dry-run` — admin-only; calls `container.discovery_recipe_service.dry_run(recipe_id, override_limit=limit)`, mapping an unknown recipe id to 404. Always allowed regardless of approval state (never spends credits or writes).
  - `discovery_cost_summary(authorization)` — `GET /api/discovery/cost-summary` — admin-only; returns `container.discovery_recipe_service.cost_summary()`.
  - `send_digest(authorization)` — `POST /api/digests/send` — admin-only; sends the latest digest through a `NoopSender` preview send and returns the receipt plus digest payload.
  - `preview_digest(email, authorization)` — `GET /api/digest/preview` — admin-only; resolves the target subscriber (falling back to the configured owner test email) and returns `container.subscriber_digest.preview(...)`.
  - `candidate_reviews(state, authorization)` — `GET /api/candidate-reviews` — admin-only; returns filtered review rows and the approved source mix via `container.candidate_review_service`.
  - `review_candidate(person_id, payload, authorization)` — `PUT /api/candidate-reviews/{person_id}` — admin-only; records an operator review decision via `container.candidate_review_service.review(...)`, mapping validation errors to HTTP 422.
  - `run_digest_cron(dry_run, recipient, authorization)` — `POST /api/digest/cron` — cron-secret-gated; validates an optional recipient email and delegates to `container.subscriber_digest.run_due(...)` to send all due digests.
  - `digest_feedback(token, person_id, vote)` — `GET /api/digest/feedback` (HTML) — verifies a signed feedback token via `container.email_action_signer.verify(...)` and, if valid, renders a confirmation page asking the user to confirm the up/down vote.
  - `save_digest_feedback(token, person_id, vote)` — `POST /api/digest/feedback` (HTML) — re-verifies the token and persists the vote via `container.feedback.upsert(...)`, returning a thank-you confirmation page.
  - `digest_unsubscribe(token)` — `GET /api/digest/unsubscribe` (HTML) — verifies an unsubscribe token and renders a confirmation prompt (or an "already unsubscribed" message).
  - `confirm_digest_unsubscribe(token)` — `POST /api/digest/unsubscribe` (HTML) — re-verifies the token and deactivates the subscriber via `container.subscribers.deactivate(...)`.
- `_digest_dict(digest) -> dict` — serializes a `Digest` domain object (including its entries) into a JSON-friendly dict shared by several route handlers.
- `_candidate_source_mix(candidates: list[dict]) -> dict[str, int]` — tallies and sorts (descending) the discovery-source counts across a list of candidate rows.
- `_require_cron_secret(container, authorization) -> None` — validates the `Authorization: Bearer` header against `container.settings.cron_secret`, raising 503/401 as appropriate.
- `_require_admin_secret(container, authorization) -> None` — validates the `Authorization: Bearer` header against `container.settings.admin_secret` (falling back to `cron_secret`), raising 503/401 as appropriate. Guards every discovery/recipe/digest-admin route, including all `/discovery/recipes/*` endpoints.
- `_confirmation_page(message, success=True) -> HTMLResponse` — renders a minimal styled HTML confirmation/error page with an escaped message.
- `_action_confirmation_page(message, action, button) -> HTMLResponse` — renders a minimal styled HTML page with a POST form used to confirm a destructive/consequential action (e.g. unsubscribe, feedback vote) before it is applied.
