"""Tests for scheduled discovery (run_due + seed auto-approve)."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import build_router
from backend.config import Settings
from backend.container import Container
from backend.domain.discovery_recipe import DiscoveryRecipe


class DiscoveryCronTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = Settings(
            db_path=Path(self.temp_dir.name) / "test.db",
            database_url="",
            out_dir=Path(self.temp_dir.name) / "out",
            cron_secret="test-cron-secret",
            discovery_background=False,
        )
        self.container = Container(settings)
        app = FastAPI()
        app.include_router(build_router(self.container))
        self.client = TestClient(app)

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_seed_recipes_are_auto_approved(self):
        recipes = self.container.discovery_recipe_service.list_recipes()
        self.assertGreaterEqual(len(recipes), 1)
        for recipe in recipes:
            self.assertEqual(recipe["approval_state"], "approved", recipe["id"])

    def test_is_due_respects_frequency_and_last_run(self):
        service = self.container.discovery_recipe_service
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        recipe = DiscoveryRecipe(
            id="due-check",
            name="Due check",
            provider="pdl",
            query_type="founder",
            frequency="weekly",
            status="active",
            approval_state="approved",
            last_run=None,
        )
        self.assertTrue(service.is_due(recipe, now))
        recipe.last_run = (now - timedelta(days=3)).isoformat()
        self.assertFalse(service.is_due(recipe, now))
        recipe.last_run = (now - timedelta(days=8)).isoformat()
        self.assertTrue(service.is_due(recipe, now))
        recipe.frequency = "manual"
        self.assertFalse(service.is_due(recipe, now))
        recipe.frequency = "weekly"
        recipe.status = "paused"
        self.assertFalse(service.is_due(recipe, now))

    def test_run_due_does_not_advance_last_run_when_provider_missing(self):
        # No PDL/Coresignal/Exa keys → the expander never reaches a provider, so
        # last_run must NOT advance; the recipe stays due until a key is added.
        result = self.container.discovery_recipe_service.run_due()
        self.assertGreaterEqual(result["due_count"], 1)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["created_total"], 0)
        after = self.container.discovery_recipe_service.list_recipes()
        for row in after:
            if row["frequency"] != "manual":
                self.assertIsNone(row["last_run"], row["id"])
                self.assertFalse(row["provider_configured"], row["id"])
        # Second tick still finds the same recipes due (schedule not skipped).
        again = self.container.discovery_recipe_service.run_due()
        self.assertEqual(again["due_count"], result["due_count"])

    def test_discovery_cron_requires_secret(self):
        unauthorized = self.client.post("/api/discovery/cron")
        self.assertEqual(unauthorized.status_code, 401)
        authorized = self.client.post(
            "/api/discovery/cron",
            headers={"Authorization": "Bearer test-cron-secret"},
        )
        self.assertEqual(authorized.status_code, 200)
        body = authorized.json()
        self.assertIn("due_count", body)
        self.assertIn("ran_count", body)


if __name__ == "__main__":
    unittest.main()
