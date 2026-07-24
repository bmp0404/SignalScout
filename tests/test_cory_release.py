"""Tests for the Cory-ready release: interval digest cadence, the rotating
"upcoming" digest preview over the verified+contactable+score-gated pool,
recipe re-scan after the cadence window, run skip reasons, and the operator
(ADMIN_SECRET) gate."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import build_router
from backend.config import Settings
from backend.discovery.provider_expansion import ProviderExpander
from backend.container import Container
from backend.db.repositories.provider_identities import ProviderSearchCheckpoint
from backend.digest.sender import EmailMessage, EmailSender
from backend.domain.person import Person
from backend.domain.signal import Signal


class StubSender(EmailSender):
    def __init__(self):
        self.messages: list[tuple[EmailMessage, str]] = []

    def send(self, message: EmailMessage, to: str) -> dict:
        self.messages.append((message, to))
        return {"sent": True, "id": f"stub-{len(self.messages)}"}


def _approved_person(container: Container, name: str, github: str) -> Person:
    person = Person(
        name=name, cohort="discovery", score=70.0, github_username=github,
        evidence_tier="verified",
    )
    person.email = f"{github}@example.com"
    container.persons.save(person)
    container.signals.save(
        Signal(
            person_id=person.id,
            person_name=person.name,
            signal_type="competition_win",
            signal_category="competition",
            signal_date="2026-06-01",
            signal_strength=0.9,
            source="public_web",
            source_url="https://example.com/evidence",
            summary=f"{name} won a documented public competition.",
        )
    )
    container.candidate_review_service.review(
        person.id,
        "approved",
        contactable=True,
        primary_evidence_url="https://example.com/evidence",
    )
    return person


class DigestCadenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.container = Container(
            Settings(
                db_path=root / "test.db",
                database_url="",
                out_dir=root / "out",
                cron_secret="c",
                discovery_background=False,
                digest_background=False,
            )
        )
        self.person = _approved_person(self.container, "Ada Lovelace", "ada")
        self.container.subscriber_digest.sender = StubSender()

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_is_due_is_interval_based(self):
        service = self.container.subscriber_digest
        subscriber = self.container.subscribers.subscribe("cory@example.com", "every_3_days", {})
        now = datetime.now(timezone.utc)
        self.assertTrue(service._is_due(subscriber, now))  # never sent -> due
        service.deliver(subscriber)
        self.assertFalse(service._is_due(subscriber, now))  # just sent -> not due
        self.assertFalse(service._is_due(subscriber, now + timedelta(days=2)))
        self.assertTrue(service._is_due(subscriber, now + timedelta(days=4)))

    def test_run_due_respects_cadence_window(self):
        service = self.container.subscriber_digest
        self.container.subscribers.subscribe("cory@example.com", "every_3_days", {})
        first = service.run_due()
        self.assertEqual(first["sent_count"], 1)
        # A second tick inside the 3-day window does not re-send.
        again = service.run_due()
        self.assertEqual(again["subscriber_count"], 0)


class UpcomingDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.container = Container(
            Settings(
                db_path=root / "test.db",
                database_url="",
                out_dir=root / "out",
                cron_secret="c",
                discovery_background=False,
                digest_background=False,
            )
        )
        app = FastAPI()
        app.include_router(build_router(self.container))
        self.client = TestClient(app)

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_upcoming_orders_unfeatured_first_but_stays_full(self):
        featured_person = _approved_person(self.container, "Ada Lovelace", "ada")
        fresh_person = _approved_person(self.container, "Katherine Johnson", "katherine")
        # Ada has already gone out in a delivered digest; Katherine hasn't.
        subscriber = self.container.subscribers.subscribe("cory@example.com", "every_3_days", {})
        self.container.digest_sends.record_many(subscriber.id, [featured_person.id], "msg-1")
        body = self.client.get("/api/digest/upcoming").json()
        ids = [e["person_id"] for e in body["entries"]]
        # Both still appear (the tab stays full), with the unfeatured person first.
        self.assertEqual(set(ids), {featured_person.id, fresh_person.id})
        self.assertEqual(ids[0], fresh_person.id)
        self.assertEqual(body["featured_count"], 1)
        self.assertIn("auto_send", body)

    def test_upcoming_offset_paginates_to_a_fresh_batch(self):
        # A pool larger than the digest size (10): the default batch and the
        # next_offset batch are disjoint, cycling through new people each refresh.
        for i in range(14):
            _approved_person(self.container, f"Person {i}", f"user{i}")
        first = self.client.get("/api/digest/upcoming").json()
        self.assertEqual(len(first["entries"]), 10)
        self.assertEqual(first["pool_size"], 14)
        nxt = self.client.get(f"/api/digest/upcoming?offset={first['next_offset']}").json()
        first_ids = {e["person_id"] for e in first["entries"]}
        next_ids = {e["person_id"] for e in nxt["entries"]}
        # The 4 people not shown in batch one lead batch two (fresh people surface).
        self.assertTrue(next_ids - first_ids)

    def test_verified_contactable_candidate_appears_without_review(self):
        # No human review step gates eligibility: a verified-tier, contactable
        # person with a qualifying score appears on its own, never reviewed.
        person = Person(
            name="Grace Hopper", cohort="discovery", score=70.0,
            github_username="grace", evidence_tier="verified",
        )
        person.email = "grace@example.com"
        self.container.persons.save(person)
        self.container.signals.save(
            Signal(
                person_id=person.id,
                person_name=person.name,
                signal_type="competition_win",
                signal_category="competition",
                signal_date="2026-06-01",
                signal_strength=0.9,
                source="public_web",
                source_url="https://example.com/evidence",
                summary="Won a documented public competition.",
            )
        )
        body = self.container.subscriber_digest.upcoming()
        ids = [e["person_id"] for e in body["entries"]]
        self.assertIn(person.id, ids)

    def test_min_score_setting_gates_eligibility(self):
        person = _approved_person(self.container, "Score Gated", "scoregated")
        self.container.digest_settings.set_min_score(1000)
        body = self.container.subscriber_digest.upcoming()
        ids = [e["person_id"] for e in body["entries"]]
        self.assertNotIn(person.id, ids)
        self.container.digest_settings.set_min_score(0)
        body = self.container.subscriber_digest.upcoming()
        ids = [e["person_id"] for e in body["entries"]]
        self.assertIn(person.id, ids)


class RecipeRescanAndSkipTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.container = Container(
            Settings(
                db_path=root / "test.db",
                database_url="",
                out_dir=root / "out",
                cron_secret="c",
                discovery_background=False,
                digest_background=False,
            )
        )

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_due_for_rescan_window(self):
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        recent = ProviderSearchCheckpoint(
            provider="exa",
            filter_identity="x",
            filters={},
            exhausted=True,
            updated_at=(now - timedelta(days=2)).isoformat(timespec="seconds"),
        )
        stale = ProviderSearchCheckpoint(
            provider="exa",
            filter_identity="x",
            filters={},
            exhausted=True,
            updated_at=(now - timedelta(days=9)).isoformat(timespec="seconds"),
        )
        week = timedelta(days=7)
        self.assertFalse(ProviderExpander._due_for_rescan(recent, week, now.isoformat()))
        self.assertTrue(ProviderExpander._due_for_rescan(stale, week, now.isoformat()))
        self.assertFalse(ProviderExpander._due_for_rescan(stale, None, now.isoformat()))

    def test_unconfigured_provider_run_reports_skip_reason(self):
        # No EXA key in this container -> the Exa recipe can't reach a provider.
        result = self.container.discovery_recipe_service.dry_run("exa_young_technical_founders")
        self.assertEqual(result["skip_reason"], "provider_not_configured")


class AdminGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.container = Container(
            Settings(
                db_path=root / "test.db",
                database_url="",
                out_dir=root / "out",
                cron_secret="c",
                admin_secret="operator-secret",
                discovery_background=False,
                digest_background=False,
            )
        )
        app = FastAPI()
        app.include_router(build_router(self.container))
        self.client = TestClient(app)

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_send_requires_admin_secret(self):
        self.assertEqual(self.client.post("/api/digests/send").status_code, 401)
        ok = self.client.post(
            "/api/digests/send", headers={"X-Admin-Secret": "operator-secret"}
        )
        self.assertEqual(ok.status_code, 200)

    def test_recipe_approve_requires_admin_secret(self):
        recipe_id = self.container.discovery_recipe_service.list_recipes()[0]["id"]
        blocked = self.client.post(f"/api/discovery/recipes/{recipe_id}/approve")
        self.assertEqual(blocked.status_code, 401)
        ok = self.client.post(
            f"/api/discovery/recipes/{recipe_id}/approve",
            headers={"X-Admin-Secret": "operator-secret"},
        )
        self.assertEqual(ok.status_code, 200)

    def test_read_only_routes_stay_open(self):
        self.assertEqual(self.client.get("/api/discovery/recipes").status_code, 200)
        self.assertEqual(self.client.get("/api/digest/upcoming").status_code, 200)

    def test_digest_settings_requires_admin_secret(self):
        blocked = self.client.put("/api/digest/settings", json={"min_score": 55})
        self.assertEqual(blocked.status_code, 401)
        ok = self.client.put(
            "/api/digest/settings",
            json={"min_score": 55},
            headers={"X-Admin-Secret": "operator-secret"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["min_score"], 55)


class DigestSettingsRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.container = Container(
            Settings(
                db_path=root / "test.db",
                database_url="",
                out_dir=root / "out",
                cron_secret="c",
                discovery_background=False,
                digest_background=False,
            )
        )

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_default_min_score_is_forty(self):
        self.assertEqual(self.container.digest_settings.get_min_score(), 40.0)

    def test_set_min_score_persists(self):
        self.container.digest_settings.set_min_score(75)
        self.assertEqual(self.container.digest_settings.get_min_score(), 75.0)
        # A fresh repository instance on the same db reads the persisted value.
        from backend.db.repositories.digest_settings import DigestSettingsRepository
        reloaded = DigestSettingsRepository(self.container.db)
        self.assertEqual(reloaded.get_min_score(), 75.0)


if __name__ == "__main__":
    unittest.main()
