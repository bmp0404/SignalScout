"""API routes (spec §16). Thin: every handler delegates to a service from the Container."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from backend.container import Container


def build_router(container: Container) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health():
        container.db.conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": container.db.backend}

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
            raise HTTPException(status_code=404, detail="person not found")
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
        receipt = container.email_sender.send(digest, to="cory@example.com")
        return {"receipt": receipt, "digest": _digest_dict(digest)}

    return router


def _digest_dict(digest) -> dict:
    return {
        "id": digest.id,
        "generated_at": digest.generated_at,
        "subject": digest.subject,
        "entries": [asdict(e) for e in digest.entries],
        "html": digest.html,
    }
