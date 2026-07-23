# api

The `api` module exposes the backend's public HTTP surface via a FastAPI router; it sits at the very end of the pipeline (domain -> repositories -> scrapers -> scoring/backtest -> discovery/enrichment -> digest -> api), translating HTTP requests into calls on the services wired up by `backend/container.py` and never containing business logic itself.

## backend/api/__init__.py
Empty file; the `api` package has no re-exports.

## backend/api/routes.py
Defines the `build_router(container)` factory that assembles every `/api/*` FastAPI route as a thin delegate to `Container` services, plus request models, rate limiting, the cron auth guard, and small HTML response helpers. Product routes are open (single-operator tool); only `POST /api/digest/cron` requires a bearer secret.

- `EMAIL_RE` — module-level compiled regex used to validate email address format across handlers.
- `SubscriberSignup` — Pydantic request model for new digest subscriber signups (email, frequency, signal interests, seed accounts).
- `TestDigestRequest` — Pydantic request model holding the target email for an operator-triggered test digest send.
- `PageViewEvent` — Pydantic request model for client-side analytics page-view beacons (path, optional referrer).
- `CandidateReviewRequest` — Pydantic request model for a review decision on a discovery candidate (state, optional notes/evidence fields).
- `build_router(container: Container) -> APIRouter` — constructs and returns the `/api`-prefixed router, defining a per-process in-memory rate-limit bucket and every route handler as a closure over `container`.
  - `rate_limit(request, key, limit, window) -> None` (nested) — sliding-window rate limiter keyed by client IP and an action key, using an in-memory `deque`; raises HTTP 429 once the limit is exceeded within the window.
  - `health()` — `GET /api/health` — runs a trivial `SELECT 1` against the database and returns `{"status": "ok", "db": ...}` as a liveness/DB-connectivity check.
  - `subscribe(payload, request)` — `POST /api/subscribers` — rate-limits signups, validates and normalizes the email/frequency, parses comma-separated seed accounts, and delegates persistence to `container.subscribers.subscribe(...)`.
  - `send_test_digest(payload, request)` — `POST /api/digest/test` — rate-limits per subscriber, enforces production-only owner-email restriction and a 24-hour resend cooldown (via `container.digest_sends`), then delegates actual delivery to `container.subscriber_digest.deliver(...)`.
  - `record_page_view(payload, request)` — `POST /api/analytics/page-view` (202) — rate-limits, validates the path is relative, and records the event via `container.page_views.record(...)`.
  - `overview()` — `GET /api/overview` — aggregates backtest metrics (`container.backtest.run()`), discovery counts/flags (all approval states), and provider-search verification stats into a single dashboard summary payload.
  - `candidates(cohort)` — `GET /api/candidates` — lists candidates for a cohort via `container.candidate_service.list_candidates(...)` with no approval-state filter.
  - `candidate(person_id)` — `GET /api/candidates/{person_id}` — fetches a single candidate profile via `container.candidate_service.profile(...)`, 404ing if missing.
  - `backtest()` — `GET /api/backtest` — returns raw backtest results from `container.backtest.run()`.
  - `concentrations()` — `GET /api/concentrations` — returns all detected signal concentrations via `container.concentrations.all()`.
  - `latest_digest()` — `GET /api/digests/latest` — returns the most recently generated digest via `container.digests.latest()`.
  - `generate_digest()` — `POST /api/digests/generate` — triggers `container.digest_generator.generate()` and returns the new digest.
  - `run_discovery(request)` — `POST /api/discovery/run` — rate-limited (2/hour); starts an async discovery job via `container.discovery_job.start()`, mapping a running-job or missing-token error to 409/400. This is the "OG" batch pipeline (see `services.md`'s `DiscoveryJobManager`) — separate from the recipe endpoints below, though both share the same `ProviderBudget`.
  - `discovery_status()` — `GET /api/discovery/status` — returns `container.discovery_job.status()`.
  - `list_discovery_recipes()` — `GET /api/discovery/recipes` — returns `container.discovery_recipe_service.list_recipes()`.
  - `approve_discovery_recipe(recipe_id)` — `POST /api/discovery/recipes/{recipe_id}/approve` — calls `container.discovery_recipe_service.approve(...)`, mapping an unknown recipe id to HTTP 404.
  - `run_discovery_recipe(recipe_id, limit)` — `POST /api/discovery/recipes/{recipe_id}/run` — calls `container.discovery_recipe_service.run(recipe_id, override_limit=limit)`, mapping an unknown recipe id to 404 and an unapproved-recipe `PermissionError` to 403. Recipe-level approve-before-spend remains as a credit safety gate.
  - `dry_run_discovery_recipe(recipe_id, limit)` — `POST /api/discovery/recipes/{recipe_id}/dry-run` — calls `container.discovery_recipe_service.dry_run(recipe_id, override_limit=limit)`, mapping an unknown recipe id to 404. Always allowed regardless of approval state (never spends credits or writes).
  - `discovery_cost_summary()` — `GET /api/discovery/cost-summary` — returns `container.discovery_recipe_service.cost_summary()`.
  - `send_digest()` — `POST /api/digests/send` — sends the current approved+contactable picks to every active subscriber right now via `SubscriberDigestService.send_to_active` (real Resend send, preview-only fallback when Resend is unconfigured, deduped per subscriber); returns `{summary}` with subscriber/sent/empty counts.
  - `preview_digest(email)` — `GET /api/digest/preview` — resolves the target subscriber (falling back to the configured owner test email) and returns `container.subscriber_digest.preview(...)`.
  - `candidate_reviews(state)` — `GET /api/candidate-reviews` — returns filtered review rows and the approved source mix via `container.candidate_review_service`.
  - `review_candidate(person_id, payload)` — `PUT /api/candidate-reviews/{person_id}` — records a one-click review decision via `container.candidate_review_service.review(...)`, mapping validation errors to HTTP 422.
  - `run_digest_cron(dry_run, recipient, authorization)` — `POST /api/digest/cron` — cron-secret-gated; validates an optional recipient email and delegates to `container.subscriber_digest.run_due(...)` to send all due digests.
  - `run_discovery_cron(authorization)` — `POST /api/discovery/cron` — cron-secret-gated; runs every due discovery recipe via `container.discovery_recipe_service.run_due()`.
  - `digest_feedback(token, person_id, vote)` — `GET /api/digest/feedback` (HTML) — verifies a signed feedback token via `container.email_action_signer.verify(...)` and, if valid, renders a confirmation page asking the user to confirm the up/down vote.
  - `save_digest_feedback(token, person_id, vote)` — `POST /api/digest/feedback` (HTML) — re-verifies the token and persists the vote via `container.feedback.upsert(...)`, returning a thank-you confirmation page.
  - `digest_unsubscribe(token)` — `GET /api/digest/unsubscribe` (HTML) — verifies an unsubscribe token and renders a confirmation prompt (or an "already unsubscribed" message).
  - `confirm_digest_unsubscribe(token)` — `POST /api/digest/unsubscribe` (HTML) — re-verifies the token and deactivates the subscriber via `container.subscribers.deactivate(...)`.
- `_digest_dict(digest) -> dict` — serializes a `Digest` domain object (including its entries) into a JSON-friendly dict shared by several route handlers.
- `_candidate_source_mix(candidates: list[dict]) -> dict[str, int]` — tallies and sorts (descending) the discovery-source counts across a list of candidate rows.
- `_require_cron_secret(container, authorization) -> None` — validates the `Authorization: Bearer` header against `container.settings.cron_secret`, raising 503/401 as appropriate. Sole remaining HTTP auth gate (automated digest delivery).
- `_confirmation_page(message, success=True) -> HTMLResponse` — renders a minimal styled HTML confirmation/error page with an escaped message.
- `_action_confirmation_page(message, action, button) -> HTMLResponse` — renders a minimal styled HTML page with a POST form used to confirm a destructive/consequential action (e.g. unsubscribe, feedback vote) before it is applied.
