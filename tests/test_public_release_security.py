import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import build_router
from backend.config import Settings
from backend.container import Container
from backend.domain.person import Person
from backend.domain.signal import Signal


class PublicReleaseSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = Settings(
            db_path=Path(self.temp_dir.name) / "test.db",
            database_url="",
            out_dir=Path(self.temp_dir.name) / "out",
            environment="production",
            cron_secret="test-cron-secret",
            public_base_url="https://testserver",
        )
        self.container = Container(settings)
        app = FastAPI()
        app.include_router(build_router(self.container))
        self.client = TestClient(app)

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_candidate_and_operator_routes_are_open(self):
        self.assertEqual(self.client.get("/api/candidates").status_code, 200)
        self.assertEqual(self.client.get("/api/overview").status_code, 200)
        for method, path in (
            ("get", "/api/discovery/status"),
            ("get", "/api/candidate-reviews"),
            ("get", "/api/discovery/recipes"),
            ("get", "/api/discovery/cost-summary"),
        ):
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 200, path)

    def test_cron_route_still_requires_secret(self):
        unauthorized = self.client.post("/api/digest/cron")
        self.assertEqual(unauthorized.status_code, 401)
        authorized = self.client.post(
            "/api/digest/cron",
            headers={"Authorization": "Bearer test-cron-secret"},
        )
        self.assertEqual(authorized.status_code, 200)

    def test_preview_works_without_bearer(self):
        person = Person(
            name="Reviewed Candidate",
            cohort="discovery",
            score=50,
            github_username="reviewed",
            evidence_tier="verified",
        )
        person.email = "reviewed@example.com"
        self.container.persons.save(person)
        self.container.signals.save(
            Signal(
                person_id=person.id,
                person_name=person.name,
                signal_type="competition_win",
                signal_category="competition",
                signal_date="2026-01-01",
                signal_strength=0.9,
                source="public_web",
                source_url="https://example.com/evidence",
                summary="Won a documented public competition.",
            )
        )
        self.container.candidate_review_service.review(person.id, "approved")
        subscriber = self.container.subscribers.subscribe(
            "owner@example.com", "weekly", {}
        )
        response = self.client.get("/api/digest/preview?email=owner@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [candidate["id"] for candidate in response.json()["candidates"]],
            [person.id],
        )
        self.assertEqual(self.container.digest_sends.sent_person_ids(subscriber.id), set())

    def test_one_click_approve_updates_state(self):
        person = Person(
            name="Quick Approve",
            cohort="discovery",
            score=40,
            github_username="quick",
        )
        self.container.persons.save(person)
        response = self.client.put(
            f"/api/candidate-reviews/{person.id}",
            json={"state": "approved"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "approved")
        listed = self.client.get("/api/candidates?cohort=discovery")
        self.assertEqual(listed.status_code, 200)
        match = next(c for c in listed.json()["candidates"] if c["id"] == person.id)
        self.assertEqual(match["approval_state"], "approved")

    def test_public_signup_does_not_expose_action_token(self):
        response = self.client.post(
            "/api/subscribers",
            json={"email": "reader@example.com", "frequency": "weekly"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("subscriber_token", response.json())

    def test_production_requires_cron_secret(self):
        with self.assertRaisesRegex(RuntimeError, "CRON_SECRET"):
            Container(
                Settings(
                    db_path=Path(self.temp_dir.name) / "bad.db",
                    database_url="",
                    environment="production",
                    cron_secret="",
                )
            )


if __name__ == "__main__":
    unittest.main()
