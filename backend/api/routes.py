"""API routes (spec §16). Thin: every handler delegates to a service from the Container."""

import hmac
import html
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.container import Container
from backend.digest.sender import EmailMessage, NoopSender

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubscriberSignup(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    frequency: str = "daily"
    signal_interests: str = Field(default="", max_length=1000)
    seed_accounts: str = Field(default="", max_length=4000)


class TestDigestRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    token: str = Field(min_length=1, max_length=200)


class PageViewEvent(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    referrer: str | None = Field(default=None, max_length=500)


def build_router(container: Container) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health():
        container.db.conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": container.db.backend}

    @router.post("/subscribers")
    def subscribe(payload: SubscriberSignup):
        email = payload.email.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise HTTPException(status_code=422, detail="Enter a valid email address.")
        if payload.frequency not in {"daily", "weekly"}:
            raise HTTPException(status_code=422, detail="Frequency must be daily or weekly.")
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
            "subscriber_token": subscriber.unsubscribe_token,
            "message": f"You're signed up for the {subscriber.frequency} Signal Scout digest.",
        }

    @router.post("/digest/test")
    def send_test_digest(payload: TestDigestRequest):
        email = payload.email.strip().lower()
        subscriber = container.subscribers.get_by_token(payload.token)
        if (
            not EMAIL_RE.fullmatch(email)
            or not subscriber
            or not subscriber.active
            or not hmac.compare_digest(subscriber.email, email)
        ):
            raise HTTPException(
                status_code=401,
                detail="That test-digest link is invalid or has expired. Please subscribe again.",
            )

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
    def record_page_view(payload: PageViewEvent):
        path = payload.path.strip()
        if not path.startswith("/") or "://" in path:
            raise HTTPException(status_code=422, detail="Page path must be relative to this site.")
        referrer = payload.referrer.strip() if payload.referrer else None
        container.page_views.record(path, referrer or None)
        return {"accepted": True}

    @router.get("/overview")
    def overview():
        backtest = container.backtest.run()
        discoveries = container.candidate_service.list_candidates("discovery")
        flagged = [d for d in discoveries if d["flagged"]]
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
        }

    @router.get("/candidates")
    def candidates(cohort: str = "discovery"):
        cohort_arg = None if cohort == "all" else cohort
        return {"candidates": container.candidate_service.list_candidates(cohort_arg)}

    @router.get("/candidates/{person_id}")
    def candidate(person_id: str):
        profile = container.candidate_service.profile(person_id)
        if not profile:
            raise HTTPException(status_code=404, detail="That candidate is no longer available.")
        return profile

    @router.get("/backtest")
    def backtest():
        return container.backtest.run()

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
    def generate_digest():
        digest = container.digest_generator.generate()
        return {"digest": _digest_dict(digest)}

    @router.post("/discovery/run")
    def run_discovery():
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

    @router.post("/digests/send")
    def send_digest():
        digest = container.digests.latest()
        if not digest:
            raise HTTPException(status_code=400, detail="generate a digest first")
        receipt = NoopSender().send(
            EmailMessage(
                subject=digest.subject,
                html=digest.html,
                text="\n".join([digest.subject, *[entry.name for entry in digest.entries]]),
            ),
            to="preview@local.invalid",
        )
        return {"receipt": receipt, "digest": _digest_dict(digest)}

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

    @router.get("/digest/feedback", response_class=HTMLResponse)
    def digest_feedback(token: str, person_id: str, vote: str):
        if vote not in {"up", "down"}:
            return _confirmation_page("That feedback link is not valid.", success=False)
        subscriber = container.subscribers.get_by_token(token)
        if not subscriber or not subscriber.active:
            return _confirmation_page("This feedback link has expired.", success=False)
        person = container.persons.get(person_id)
        if not person:
            return _confirmation_page("That candidate is no longer available.", success=False)
        container.feedback.upsert(subscriber.id, person_id, vote)
        label = "useful" if vote == "up" else "not a fit"
        return _confirmation_page(f"Thanks — you marked {person.name} as {label}.")

    @router.get("/digest/unsubscribe", response_class=HTMLResponse)
    def digest_unsubscribe(token: str):
        subscriber = container.subscribers.get_by_token(token)
        if not subscriber:
            return _confirmation_page("This unsubscribe link is not valid.", success=False)
        changed = container.subscribers.deactivate(token)
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


def _require_cron_secret(container: Container, authorization: str | None) -> None:
    configured = container.settings.cron_secret
    if not configured:
        raise HTTPException(status_code=503, detail="Digest scheduling is not configured.")
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
