"""API routes (spec §16). Thin: every handler delegates to a service from the Container."""

import hmac
import html
import re
import time
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.container import Container

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubscriberSignup(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    frequency: str = "every_3_days"
    signal_interests: str = Field(default="", max_length=1000)
    seed_accounts: str = Field(default="", max_length=4000)


class TestDigestRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PageViewEvent(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    referrer: str | None = Field(default=None, max_length=500)


class DigestSettingsRequest(BaseModel):
    min_score: float = Field(ge=0, le=100)


class CandidateReviewRequest(BaseModel):
    state: str
    why_now: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=4000)
    source_bucket: str = Field(default="", max_length=100)
    contactable: bool = False
    primary_evidence_url: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="", max_length=200)


def build_router(container: Container) -> APIRouter:
    router = APIRouter(prefix="/api")
    attempts: dict[str, deque[float]] = defaultdict(deque)

    def rate_limit(request: Request, key: str, limit: int, window: int) -> None:
        """Best-effort per-IP sliding-window limiter. State lives in `attempts`,
        an in-memory dict, so counters are per-process and reset on restart — fine
        for the single-instance Railway deploy, but would need shared storage
        (e.g. Redis) if the API is ever scaled horizontally."""
        now = time.monotonic()
        identity = request.client.host if request.client else "unknown"
        bucket = attempts[f"{key}:{identity}"]
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
        bucket.append(now)

    backtest_cache: dict[str, object] = {}

    def cached_backtest() -> dict:
        """Backtest depends only on persons/signals/edges (not on live scores), so
        cache the (fairly expensive) full run and recompute only when any of those
        row counts change — which covers every add/remove in the deployed app,
        where founder/control history is seeded and immutable."""
        conn = container.db.conn
        key = "|".join(
            str(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
            for table in ("persons", "signals", "graph_edges")
        )
        if backtest_cache.get("key") != key:
            backtest_cache["key"] = key
            backtest_cache["value"] = container.backtest.run()
        return backtest_cache["value"]  # type: ignore[return-value]

    @router.get("/health")
    def health():
        try:
            container.db.conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # noqa: BLE001 — health must degrade, not 500
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "db": container.db.backend, "detail": str(exc)},
            )
        return {"status": "ok", "db": container.db.backend}

    @router.post("/subscribers")
    def subscribe(payload: SubscriberSignup, request: Request):
        rate_limit(request, "subscribe", 10, 60 * 60)
        email = payload.email.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise HTTPException(status_code=422, detail="Enter a valid email address.")
        if payload.frequency not in {"daily", "every_3_days", "weekly"}:
            raise HTTPException(
                status_code=422,
                detail="Frequency must be daily, every_3_days, or weekly.",
            )
        seed_accounts = [
            account.strip()
            for account in payload.seed_accounts.split(",")
            if account.strip()
        ]
        subscriber = container.subscribers.subscribe(
            email,
            payload.frequency,
            {
                "signal_interests": payload.signal_interests.strip(),
                "seed_accounts": seed_accounts,
            },
        )
        return {
            "subscribed": True,
            "email": subscriber.email,
            "frequency": subscriber.frequency,
            "message": f"You're signed up for the {subscriber.frequency} Signal Scout digest.",
        }

    @router.post("/digest/test")
    def send_test_digest(
        payload: TestDigestRequest,
        request: Request,
    ):
        rate_limit(request, "test-digest", 3, 24 * 60 * 60)
        email = payload.email.strip().lower()
        subscriber = container.subscribers.get_by_email(email)
        if not EMAIL_RE.fullmatch(email) or not subscriber or not subscriber.active:
            raise HTTPException(
                status_code=401,
                detail="That active subscriber is not available.",
            )
        configured_owner = container.settings.owner_test_email.strip().lower()
        if container.settings.is_production and (
            not configured_owner or not hmac.compare_digest(email, configured_owner)
        ):
            raise HTTPException(status_code=403, detail="Test delivery is restricted to the owner.")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        if container.digest_sends.sent_since(subscriber.id, cutoff):
            raise HTTPException(
                status_code=429,
                detail="A test digest was already sent recently. Please try again after 24 hours.",
            )

        result = container.subscriber_digest.deliver(subscriber, dry_run=False)
        if result["status"] == "sent":
            return {
                "sent": True,
                "email": subscriber.email,
                "candidate_count": result["candidate_count"],
                "message": "Your test digest is on its way.",
            }
        if result["status"] == "preview":
            raise HTTPException(
                status_code=503,
                detail="Email delivery isn't configured yet.",
            )
        if result["status"] == "empty":
            raise HTTPException(
                status_code=409,
                detail="There are no new candidates available for your test digest yet.",
            )
        raise HTTPException(
            status_code=502,
            detail="We couldn't send your test digest right now. Please try again later.",
        )

    @router.post("/analytics/page-view", status_code=202)
    def record_page_view(payload: PageViewEvent, request: Request):
        rate_limit(request, "page-view", 120, 60)
        path = payload.path.strip()
        if not path.startswith("/") or "://" in path:
            raise HTTPException(status_code=422, detail="Page path must be relative to this site.")
        referrer = payload.referrer.strip() if payload.referrer else None
        container.page_views.record(path, referrer or None)
        return {"accepted": True}

    @router.get("/overview")
    def overview():
        backtest = cached_backtest()
        discoveries = container.candidate_service.list_candidates("discovery")
        flagged = [d for d in discoveries if d["flagged"]]
        provider_discoveries = [
            discovery
            for discovery in discoveries
            if discovery["discovery_origin"] == "provider_search"
        ]
        return {
            "backtest_recall_pct": backtest["recall_pct"],
            "backtest_avg_lead_months": backtest["avg_lead_months"],
            "backtest_false_positive_pct": backtest["false_positive_pct"],
            "founders_total": backtest["founders_total"],
            "controls_total": backtest["controls_total"],
            "discoveries_total": len(discoveries),
            "discoveries_flagged": len(flagged),
            "threshold": container.settings.flag_threshold,
            "concentrations": len(container.concentrations.all()),
            "source_mix": _candidate_source_mix(discoveries),
            "provider_discoveries_total": len(provider_discoveries),
            "provider_verified_total": sum(
                discovery["evidence_tier"] == "verified"
                for discovery in provider_discoveries
            ),
            "provider_review_total": sum(
                discovery["review_required"]
                for discovery in provider_discoveries
            ),
            "digest_eligible_total": len(
                container.subscriber_digest.eligible_candidates(discoveries)
            ),
        }

    @router.get("/candidates")
    def candidates(cohort: str = "discovery"):
        cohort_arg = None if cohort == "all" else cohort
        rows = container.candidate_service.list_candidates(cohort_arg)
        return {"candidates": rows}

    @router.get("/candidates/{person_id}")
    def candidate(person_id: str):
        profile = container.candidate_service.profile(person_id)
        if not profile:
            raise HTTPException(status_code=404, detail="That candidate is no longer available.")
        return profile

    @router.get("/backtest")
    def backtest():
        return cached_backtest()

    @router.get("/concentrations")
    def concentrations():
        return {"concentrations": [asdict(c) for c in container.concentrations.all()]}

    @router.get("/digests/latest")
    def latest_digest():
        digest = container.digests.latest()
        if not digest:
            return {"digest": None}
        return {"digest": _digest_dict(digest)}

    @router.post("/digests/generate")
    def generate_digest(request: Request, x_admin_secret: str | None = Header(default=None)):
        _require_admin(container, x_admin_secret)
        rate_limit(request, "generate-digest", 10, 60 * 60)
        digest = container.digest_generator.generate()
        return {"digest": _digest_dict(digest)}

    @router.post("/discovery/run")
    def run_discovery(request: Request):
        rate_limit(request, "discovery", 2, 60 * 60)
        try:
            job_id = container.discovery_job.start()
        except RuntimeError as exc:  # already running
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:  # missing GITHUB_TOKEN
            raise HTTPException(status_code=400, detail=str(exc))
        return {"job_id": job_id, "status": container.discovery_job.status()}

    @router.get("/discovery/status")
    def discovery_status():
        return container.discovery_job.status()

    @router.get("/discovery/recipes")
    def list_discovery_recipes():
        return {"recipes": container.discovery_recipe_service.list_recipes()}

    @router.post("/discovery/recipes/{recipe_id}/approve")
    def approve_discovery_recipe(
        recipe_id: str,
        request: Request,
        x_admin_secret: str | None = Header(default=None),
    ):
        _require_admin(container, x_admin_secret)
        rate_limit(request, "recipe-approve", 30, 60 * 60)
        try:
            return container.discovery_recipe_service.approve(recipe_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/discovery/recipes/{recipe_id}/run")
    def run_discovery_recipe(
        recipe_id: str,
        request: Request,
        limit: int | None = Query(default=None),
        x_admin_secret: str | None = Header(default=None),
    ):
        _require_admin(container, x_admin_secret)
        rate_limit(request, "recipe-run", 12, 60 * 60)
        try:
            return container.discovery_recipe_service.run(recipe_id, override_limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post("/discovery/recipes/{recipe_id}/dry-run")
    def dry_run_discovery_recipe(
        recipe_id: str,
        request: Request,
        limit: int | None = Query(default=None),
        x_admin_secret: str | None = Header(default=None),
    ):
        _require_admin(container, x_admin_secret)
        rate_limit(request, "recipe-dry-run", 30, 60 * 60)
        try:
            return container.discovery_recipe_service.dry_run(recipe_id, override_limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/discovery/cost-summary")
    def discovery_cost_summary():
        return container.discovery_recipe_service.cost_summary()

    @router.post("/digests/send")
    def send_digest(request: Request, x_admin_secret: str | None = Header(default=None)):
        """Send the current curated (approved + contactable) picks to every
        active subscriber right now via Resend. Falls back to preview receipts
        when Resend is unconfigured. Re-sends are deduped per subscriber."""
        _require_admin(container, x_admin_secret)
        rate_limit(request, "send-digest", 3, 60 * 60)
        return {"summary": container.subscriber_digest.send_to_active()}

    @router.get("/digest/upcoming")
    def upcoming_digest(offset: int = Query(default=0, ge=0)):
        """The digest lineup subscribers receive: approved + contactable picks
        (verified-tier backfill), ordered with not-yet-featured people first and
        paginated by `offset` so each Refresh cycles to a fresh batch. Also
        returns auto-send status. Public/read-only."""
        return container.subscriber_digest.upcoming(offset=offset)

    @router.get("/digest/settings")
    def get_digest_settings():
        return {"min_score": container.digest_settings.get_min_score()}

    @router.put("/digest/settings")
    def update_digest_settings(
        payload: DigestSettingsRequest,
        request: Request,
        x_admin_secret: str | None = Header(default=None),
    ):
        _require_admin(container, x_admin_secret)
        rate_limit(request, "digest-settings", 30, 60 * 60)
        container.digest_settings.set_min_score(payload.min_score)
        return {"min_score": container.digest_settings.get_min_score()}

    @router.get("/digest/preview")
    def preview_digest(email: str = Query(default="")):
        target_email = email.strip().lower() or container.settings.owner_test_email.strip().lower()
        if not target_email:
            raise HTTPException(
                status_code=422,
                detail="Choose an existing subscriber email for the exact preview.",
            )
        subscriber = container.subscribers.get_by_email(target_email)
        if not subscriber or not subscriber.active:
            raise HTTPException(status_code=404, detail="Active subscriber not found.")
        return container.subscriber_digest.preview(subscriber)

    @router.get("/candidate-reviews")
    def candidate_reviews(state: str | None = Query(default=None)):
        return {
            "reviews": container.candidate_review_service.list_rows(state),
            "source_mix": container.candidate_review_service.approved_mix(),
        }

    @router.put("/candidate-reviews/{person_id}")
    def review_candidate(
        person_id: str,
        payload: CandidateReviewRequest,
        request: Request,
    ):
        rate_limit(request, "review-candidate", 120, 60)
        try:
            review = container.candidate_review_service.review(
                person_id=person_id,
                state=payload.state,
                why_now=payload.why_now,
                notes=payload.notes,
                source_bucket=payload.source_bucket,
                contactable=payload.contactable,
                primary_evidence_url=payload.primary_evidence_url,
                reviewer=payload.reviewer,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return review.__dict__

    @router.post("/digest/cron")
    def run_digest_cron(
        dry_run: bool = Query(default=False),
        recipient: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
    ):
        _require_cron_secret(container, authorization)
        if recipient and not EMAIL_RE.fullmatch(recipient.strip().lower()):
            raise HTTPException(status_code=422, detail="Recipient must be a valid email address.")
        return container.subscriber_digest.run_due(
            dry_run=dry_run,
            recipient=recipient.strip().lower() if recipient else None,
        )

    @router.post("/discovery/cron")
    def run_discovery_cron(authorization: str | None = Header(default=None)):
        _require_cron_secret(container, authorization)
        return container.discovery_recipe_service.run_due()

    @router.get("/digest/feedback", response_class=HTMLResponse)
    def digest_feedback(token: str, person_id: str, vote: str):
        if vote not in {"up", "down"}:
            return _confirmation_page("That feedback link is not valid.", success=False)
        subscriber_id = container.email_action_signer.verify(
            token, "feedback", person_id, vote
        )
        subscriber = container.subscribers.get(subscriber_id) if subscriber_id else None
        if not subscriber or not subscriber.active:
            return _confirmation_page("This feedback link has expired.", success=False)
        person = container.persons.get(person_id)
        if not person:
            return _confirmation_page("That candidate is no longer available.", success=False)
        label = "useful" if vote == "up" else "not a fit"
        return _action_confirmation_page(
            f"Mark {person.name} as {label}?",
            f"/api/digest/feedback?token={token}&person_id={person_id}&vote={vote}",
            "Save feedback",
        )

    @router.post("/digest/feedback", response_class=HTMLResponse)
    def save_digest_feedback(token: str, person_id: str, vote: str):
        if vote not in {"up", "down"}:
            return _confirmation_page("That feedback link is not valid.", success=False)
        subscriber_id = container.email_action_signer.verify(
            token, "feedback", person_id, vote
        )
        subscriber = container.subscribers.get(subscriber_id) if subscriber_id else None
        person = container.persons.get(person_id)
        if not subscriber or not subscriber.active or not person:
            return _confirmation_page("This feedback link has expired.", success=False)
        container.feedback.upsert(subscriber.id, person_id, vote)
        label = "useful" if vote == "up" else "not a fit"
        return _confirmation_page(f"Thanks — you marked {person.name} as {label}.")

    @router.get("/digest/unsubscribe", response_class=HTMLResponse)
    def digest_unsubscribe(token: str):
        subscriber_id = container.email_action_signer.verify(token, "unsubscribe")
        subscriber = container.subscribers.get(subscriber_id) if subscriber_id else None
        if not subscriber:
            return _confirmation_page("This unsubscribe link is not valid.", success=False)
        if not subscriber.active:
            return _confirmation_page(f"{subscriber.email} is already unsubscribed.")
        return _action_confirmation_page(
            f"Unsubscribe {subscriber.email} from Signal Scout?",
            f"/api/digest/unsubscribe?token={token}",
            "Unsubscribe",
        )

    @router.post("/digest/unsubscribe", response_class=HTMLResponse)
    def confirm_digest_unsubscribe(token: str):
        subscriber_id = container.email_action_signer.verify(token, "unsubscribe")
        subscriber = container.subscribers.get(subscriber_id) if subscriber_id else None
        if not subscriber:
            return _confirmation_page("This unsubscribe link is not valid.", success=False)
        changed = container.subscribers.deactivate(subscriber.unsubscribe_token)
        message = (
            f"{subscriber.email} has been unsubscribed."
            if changed
            else f"{subscriber.email} is already unsubscribed."
        )
        return _confirmation_page(message)

    return router


def _digest_dict(digest) -> dict:
    return {
        "id": digest.id,
        "generated_at": digest.generated_at,
        "subject": digest.subject,
        "entries": [asdict(e) for e in digest.entries],
        "html": digest.html,
    }


def _candidate_source_mix(candidates: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        for source, count in candidate.get("source_counts", {}).items():
            counts[source] = counts.get(source, 0) + int(count)
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def _require_admin(container: Container, supplied: str | None) -> None:
    """Gate operator-only actions (recipe approve/run, digest send/generate)
    behind ADMIN_SECRET (X-Admin-Secret header). When no secret is configured
    (local dev / tests) the gate is open; once configured, it must match."""
    configured = container.settings.admin_secret
    if not configured:
        return
    value = (supplied or "").strip()
    if not value or not hmac.compare_digest(value, configured):
        raise HTTPException(status_code=401, detail="Operator action requires a valid admin secret.")


def _require_cron_secret(container: Container, authorization: str | None) -> None:
    configured = container.settings.cron_secret
    if not configured:
        raise HTTPException(status_code=503, detail="Cron scheduling is not configured.")
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="Invalid cron authorization.")

def _confirmation_page(message: str, success: bool = True) -> HTMLResponse:
    title = "All set" if success else "Link unavailable"
    safe_message = html.escape(message)
    return HTMLResponse(
        content=f"""<!doctype html><html><head><meta name="viewport" content="width=device-width">
<title>{title} · Signal Scout</title></head>
<body style="margin:0;background:#f5f3ec;color:#1c1b16;font-family:Georgia,serif">
<main style="max-width:520px;margin:12vh auto;padding:28px">
<p style="color:#60652b;font:12px ui-monospace,monospace;text-transform:uppercase">Signal Scout</p>
<h1>{title}</h1><p style="font-size:18px;line-height:1.5">{safe_message}</p>
</main></body></html>""",
        status_code=200 if success else 400,
    )


def _action_confirmation_page(message: str, action: str, button: str) -> HTMLResponse:
    safe_message = html.escape(message)
    safe_action = html.escape(action, quote=True)
    safe_button = html.escape(button)
    return HTMLResponse(
        content=f"""<!doctype html><html><head><meta name="viewport" content="width=device-width">
<title>Confirm · Signal Scout</title></head>
<body style="margin:0;background:#f5f3ec;color:#1c1b16;font-family:Georgia,serif">
<main style="max-width:520px;margin:12vh auto;padding:28px">
<p style="color:#60652b;font:12px ui-monospace,monospace;text-transform:uppercase">Signal Scout</p>
<h1>Confirm action</h1><p style="font-size:18px;line-height:1.5">{safe_message}</p>
<form method="post" action="{safe_action}">
<button type="submit" style="background:#60652b;color:#fff;border:0;padding:11px 18px">{safe_button}</button>
</form></main></body></html>"""
    )
