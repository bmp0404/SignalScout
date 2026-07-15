"""Repository for provider-search identities.

Self-creates its table (CREATE IF NOT EXISTS) because the live signal_scout.db
predates it and is never reset/rebuilt. Backs the dedupe ladder in
provider_expansion: (provider, provider_person_id) -> canonical LinkedIn URL.
"""

import sqlite3
from dataclasses import dataclass, field

from backend.db.database import Database
from backend.db.repositories.base import BaseRepository

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS provider_identities (
    provider TEXT NOT NULL,
    provider_person_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    canonical_linkedin TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (provider, provider_person_id)
);
CREATE INDEX IF NOT EXISTS idx_provider_identities_person ON provider_identities(person_id);
CREATE INDEX IF NOT EXISTS idx_provider_identities_linkedin ON provider_identities(canonical_linkedin);
CREATE TABLE IF NOT EXISTS provider_search_checkpoints (
    provider TEXT NOT NULL,
    filter_identity TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    cursor TEXT,
    next_page INTEGER NOT NULL DEFAULT 0,
    exhausted INTEGER NOT NULL DEFAULT 0,
    requested_pages INTEGER NOT NULL DEFAULT 0,
    api_requests INTEGER NOT NULL DEFAULT 0,
    returned_records INTEGER NOT NULL DEFAULT 0,
    credit_units INTEGER NOT NULL DEFAULT 0,
    verified_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    merged_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    rejection_reasons TEXT NOT NULL DEFAULT '{}',
    last_outcome TEXT NOT NULL DEFAULT 'never_run',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, filter_identity)
);
"""


@dataclass
class ProviderSearchCheckpoint:
    provider: str
    filter_identity: str
    filters: dict
    cursor: str | None = None
    next_page: int = 0
    exhausted: bool = False
    requested_pages: int = 0
    api_requests: int = 0
    returned_records: int = 0
    credit_units: int = 0
    verified_count: int = 0
    review_count: int = 0
    merged_count: int = 0
    duplicate_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    last_outcome: str = "never_run"
    updated_at: str = ""


def canonical_linkedin(url: str | None) -> str | None:
    """Normalize a LinkedIn URL to a stable dedupe key: scheme/host/query
    stripped, trailing slash removed, lower-cased '/in/<slug>' shorthand."""
    if not url:
        return None
    text = url.strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.split("?")[0].split("#")[0]
    if text.startswith("www."):
        text = text[4:]
    text = text.rstrip("/")
    return text or None


class ProviderIdentityRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)
        self.conn.executescript(TABLE_SQL)
        self._ensure_checkpoint_columns()
        self.conn.commit()

    def find_person_by_provider_id(self, provider: str, provider_person_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT person_id FROM provider_identities WHERE provider = ? AND provider_person_id = ?",
            (provider, provider_person_id),
        ).fetchone()
        return row["person_id"] if row else None

    def find_person_by_linkedin(self, url: str | None) -> str | None:
        key = canonical_linkedin(url)
        if not key:
            return None
        row = self.conn.execute(
            "SELECT person_id FROM provider_identities WHERE canonical_linkedin = ?",
            (key,),
        ).fetchone()
        return row["person_id"] if row else None

    def link(self, provider: str, provider_person_id: str, person_id: str,
             linkedin_url: str | None, observed_at: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO provider_identities
               (provider, provider_person_id, person_id, canonical_linkedin, observed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (provider, provider_person_id, person_id, canonical_linkedin(linkedin_url), observed_at),
        )
        self.conn.commit()

    def checkpoint(
        self,
        provider: str,
        filter_identity: str,
    ) -> ProviderSearchCheckpoint | None:
        row = self.conn.execute(
            """SELECT * FROM provider_search_checkpoints
               WHERE provider = ? AND filter_identity = ?""",
            (provider, filter_identity),
        ).fetchone()
        if not row:
            return None
        return ProviderSearchCheckpoint(
            provider=row["provider"],
            filter_identity=row["filter_identity"],
            filters=self.loads(row["filters_json"], {}),
            cursor=row["cursor"],
            next_page=row["next_page"],
            exhausted=bool(row["exhausted"]),
            requested_pages=row["requested_pages"],
            api_requests=row["api_requests"],
            returned_records=row["returned_records"],
            credit_units=row["credit_units"],
            verified_count=row["verified_count"],
            review_count=row["review_count"],
            merged_count=row["merged_count"],
            duplicate_count=row["duplicate_count"],
            rejected_count=row["rejected_count"],
            error_count=row["error_count"],
            rejection_reasons=self.loads(row["rejection_reasons"], {}),
            last_outcome=row["last_outcome"],
            updated_at=row["updated_at"],
        )

    def record_search_page(
        self,
        checkpoint: ProviderSearchCheckpoint,
        *,
        next_cursor: str | None,
        exhausted: bool,
        api_requests: int,
        returned_records: int,
        credit_units: int,
        outcomes: dict[str, int],
        rejection_reasons: dict[str, int],
        last_outcome: str,
        updated_at: str,
        advance: bool = True,
    ) -> ProviderSearchCheckpoint:
        reasons = dict(checkpoint.rejection_reasons)
        for reason, count in rejection_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count
        updated = ProviderSearchCheckpoint(
            provider=checkpoint.provider,
            filter_identity=checkpoint.filter_identity,
            filters=checkpoint.filters,
            cursor=next_cursor if advance else checkpoint.cursor,
            next_page=checkpoint.next_page + int(advance),
            exhausted=exhausted if advance else checkpoint.exhausted,
            requested_pages=checkpoint.requested_pages + 1,
            api_requests=checkpoint.api_requests + api_requests,
            returned_records=checkpoint.returned_records + returned_records,
            credit_units=checkpoint.credit_units + credit_units,
            verified_count=checkpoint.verified_count + outcomes.get("verified", 0),
            review_count=checkpoint.review_count + outcomes.get("review", 0),
            merged_count=checkpoint.merged_count + outcomes.get("merged", 0),
            duplicate_count=checkpoint.duplicate_count + outcomes.get("duplicate", 0),
            rejected_count=checkpoint.rejected_count + outcomes.get("rejected", 0),
            error_count=checkpoint.error_count + int(last_outcome.startswith("error:")),
            rejection_reasons=reasons,
            last_outcome=last_outcome,
            updated_at=updated_at,
        )
        self.conn.execute(
            """INSERT OR REPLACE INTO provider_search_checkpoints
               (provider, filter_identity, filters_json, cursor, next_page, exhausted,
                requested_pages, api_requests, returned_records, credit_units,
                verified_count, review_count, merged_count, duplicate_count,
                rejected_count, error_count, rejection_reasons, last_outcome, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                updated.provider,
                updated.filter_identity,
                self.dumps(updated.filters),
                updated.cursor,
                updated.next_page,
                int(updated.exhausted),
                updated.requested_pages,
                updated.api_requests,
                updated.returned_records,
                updated.credit_units,
                updated.verified_count,
                updated.review_count,
                updated.merged_count,
                updated.duplicate_count,
                updated.rejected_count,
                updated.error_count,
                self.dumps(updated.rejection_reasons),
                updated.last_outcome,
                updated.updated_at,
            ),
        )
        self.conn.commit()
        return updated

    def _ensure_checkpoint_columns(self) -> None:
        if self.db.backend == "postgres":
            rows = self.conn.execute(
                """SELECT column_name AS name FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'provider_search_checkpoints'"""
            ).fetchall()
        else:
            rows = self.conn.execute(
                "PRAGMA table_info(provider_search_checkpoints)"
            ).fetchall()
        if "error_count" not in {row["name"] for row in rows}:
            statement = (
                "ALTER TABLE provider_search_checkpoints "
                + ("ADD COLUMN IF NOT EXISTS " if self.db.backend == "postgres" else "ADD COLUMN ")
                + "error_count INTEGER NOT NULL DEFAULT 0"
            )
            try:
                self.conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def ensure_checkpoint(
        self,
        provider: str,
        filter_identity: str,
        filters: dict,
        updated_at: str,
    ) -> ProviderSearchCheckpoint:
        existing = self.checkpoint(provider, filter_identity)
        if existing:
            return existing
        return ProviderSearchCheckpoint(
            provider=provider,
            filter_identity=filter_identity,
            filters=filters,
            updated_at=updated_at,
        )

    def checkpoints(self) -> list[ProviderSearchCheckpoint]:
        rows = self.conn.execute(
            "SELECT provider, filter_identity FROM provider_search_checkpoints "
            "ORDER BY provider, filter_identity"
        ).fetchall()
        return [
            checkpoint
            for row in rows
            if (
                checkpoint := self.checkpoint(
                    row["provider"],
                    row["filter_identity"],
                )
            )
        ]
