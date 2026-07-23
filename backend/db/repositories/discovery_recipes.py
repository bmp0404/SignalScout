"""Repository for discovery recipes: named, scheduled, approvable provider-
search queries layered on top of ProviderExpander. Self-creates its table
(CREATE IF NOT EXISTS), same convention as provider_identities.py.
"""

import sqlite3

from backend.db.database import Database
from backend.db.repositories.base import BaseRepository
from backend.domain.discovery_recipe import DiscoveryRecipe

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS discovery_recipes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    query_type TEXT NOT NULL,
    filters_json TEXT NOT NULL DEFAULT '{}',
    relative_filters_json TEXT NOT NULL DEFAULT '{}',
    default_limit INTEGER NOT NULL DEFAULT 25,
    frequency TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'active',
    approval_state TEXT NOT NULL DEFAULT 'pending',
    last_run TEXT
);
"""


class DiscoveryRecipeRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)
        self.conn.executescript(TABLE_SQL)
        self.conn.commit()

    def seed(self, recipes: list[DiscoveryRecipe]) -> None:
        """Insert recipes that don't already exist. Never overwrites an existing
        row (preserves operator edits to status/approval_state/last_run)."""
        for recipe in recipes:
            if self.get(recipe.id) is not None:
                continue
            self.upsert(recipe)

    def get(self, recipe_id: str) -> DiscoveryRecipe | None:
        row = self.conn.execute(
            "SELECT * FROM discovery_recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def all(self) -> list[DiscoveryRecipe]:
        rows = self.conn.execute("SELECT * FROM discovery_recipes ORDER BY name").fetchall()
        return [self._to_model(row) for row in rows]

    def upsert(self, recipe: DiscoveryRecipe) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO discovery_recipes
               (id, name, provider, query_type, filters_json, relative_filters_json,
                default_limit, frequency, status, approval_state, last_run)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                recipe.id, recipe.name, recipe.provider, recipe.query_type,
                self.dumps(recipe.filters), self.dumps(recipe.relative_filters),
                recipe.default_limit, recipe.frequency, recipe.status,
                recipe.approval_state, recipe.last_run,
            ),
        )
        self.conn.commit()

    def set_last_run(self, recipe_id: str, when: str) -> None:
        self.conn.execute(
            "UPDATE discovery_recipes SET last_run = ? WHERE id = ?", (when, recipe_id)
        )
        self.conn.commit()

    def set_approval_state(self, recipe_id: str, approval_state: str) -> None:
        self.conn.execute(
            "UPDATE discovery_recipes SET approval_state = ? WHERE id = ?",
            (approval_state, recipe_id),
        )
        self.conn.commit()

    def set_status(self, recipe_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE discovery_recipes SET status = ? WHERE id = ?", (status, recipe_id)
        )
        self.conn.commit()

    def approve_pending_seeds(self, seed_ids: set[str]) -> int:
        """One-time migrate: flip seeded recipes from pending → approved so the
        background scheduler can run them without a manual Pipeline APPROVE."""
        updated = 0
        for recipe_id in seed_ids:
            recipe = self.get(recipe_id)
            if recipe is None or recipe.approval_state != "pending":
                continue
            self.set_approval_state(recipe_id, "approved")
            updated += 1
        return updated

    def _to_model(self, row: sqlite3.Row) -> DiscoveryRecipe:
        return DiscoveryRecipe(
            id=row["id"], name=row["name"], provider=row["provider"],
            query_type=row["query_type"],
            filters=self.loads(row["filters_json"], {}),
            relative_filters=self.loads(row["relative_filters_json"], {}),
            default_limit=row["default_limit"], frequency=row["frequency"],
            status=row["status"], approval_state=row["approval_state"],
            last_run=row["last_run"],
        )
