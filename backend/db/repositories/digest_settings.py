"""Repository for the operator-adjustable digest eligibility threshold.

Self-creates its table (CREATE IF NOT EXISTS), same convention as
provider_identities.py / discovery_recipes.py. Single row by convention
(id=1), seeded on first access.
"""

import sqlite3

from backend.db.database import Database
from backend.db.repositories.base import BaseRepository

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS digest_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    min_score REAL NOT NULL DEFAULT 40
);
"""

DEFAULT_MIN_SCORE = 40.0


class DigestSettingsRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)
        self.conn.executescript(TABLE_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO digest_settings (id, min_score) VALUES (1, ?)",
            (DEFAULT_MIN_SCORE,),
        )
        self.conn.commit()

    def get_min_score(self) -> float:
        row: sqlite3.Row = self.conn.execute(
            "SELECT min_score FROM digest_settings WHERE id = 1"
        ).fetchone()
        return float(row["min_score"])

    def set_min_score(self, value: float) -> None:
        self.conn.execute(
            "UPDATE digest_settings SET min_score = ? WHERE id = 1", (value,)
        )
        self.conn.commit()
