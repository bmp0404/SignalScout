import sqlite3

from backend.db.repositories.base import BaseRepository
from backend.domain.person import Person


class PersonRepository(BaseRepository):
    EXTRA_COLUMNS = {
        "discovery_origin": "TEXT",
        "evidence_tier": "TEXT",
        "review_required": "INTEGER NOT NULL DEFAULT 0",
        "enrichment_status": "TEXT",
        "enrichment_provider": "TEXT",
        "enrichment_updated_at": "TEXT",
        "discovery_source": "TEXT",
    }

    def __init__(self, db):
        super().__init__(db)
        self._ensure_columns()
        self._derive_legacy_discovery_metadata()

    def save(self, person: Person) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO persons
               (id, name, aliases, cohort, github_username, twitter_handle, linkedin_url, email,
                personal_site, contact_info, school, graduation_year, origin_location,
                current_location, region, fellowship, breakout_date, area, thesis, score,
                needs_review, discovery_origin, evidence_tier, review_required,
                enrichment_status, enrichment_provider, enrichment_updated_at, notes,
                discovery_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                person.id, person.name, self.dumps(person.aliases), person.cohort,
                person.github_username, person.twitter_handle, person.linkedin_url, person.email,
                person.personal_site, self.dumps(person.contact_info), person.school,
                person.graduation_year, person.origin_location, person.current_location,
                person.region, person.fellowship, person.breakout_date, person.area,
                person.thesis, person.score, int(person.needs_review),
                person.discovery_origin, person.evidence_tier, int(person.review_required),
                person.enrichment_status, person.enrichment_provider,
                person.enrichment_updated_at, person.notes,
                person.discovery_source,
            ),
        )
        self.conn.commit()

    def save_many(self, persons: list[Person]) -> None:
        for p in persons:
            self.save(p)

    def get(self, person_id: str) -> Person | None:
        row = self.conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        return self._to_model(row) if row else None

    def find_by_name(self, name: str) -> Person | None:
        row = self.conn.execute(
            "SELECT * FROM persons WHERE lower(name) = lower(?)", (name,)
        ).fetchone()
        return self._to_model(row) if row else None

    def find_by_github(self, username: str) -> Person | None:
        row = self.conn.execute(
            "SELECT * FROM persons WHERE lower(github_username) = lower(?)", (username,)
        ).fetchone()
        return self._to_model(row) if row else None

    def all(self, cohort: str | None = None) -> list[Person]:
        if cohort:
            rows = self.conn.execute("SELECT * FROM persons WHERE cohort = ?", (cohort,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM persons").fetchall()
        return [self._to_model(r) for r in rows]

    def update_score(self, person_id: str, score: float) -> None:
        self.conn.execute("UPDATE persons SET score = ? WHERE id = ?", (score, person_id))
        self.conn.commit()

    def delete(self, person_id: str) -> None:
        self.conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        self.conn.commit()

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Person:
        return Person(
            id=row["id"], name=row["name"],
            aliases=BaseRepository.loads(row["aliases"], []),
            cohort=row["cohort"], github_username=row["github_username"],
            twitter_handle=row["twitter_handle"], linkedin_url=row["linkedin_url"],
            email=row["email"], personal_site=row["personal_site"],
            contact_info=BaseRepository.loads(row["contact_info"], {}),
            school=row["school"], graduation_year=row["graduation_year"],
            origin_location=row["origin_location"], current_location=row["current_location"],
            region=row["region"], fellowship=row["fellowship"], breakout_date=row["breakout_date"],
            area=row["area"], thesis=row["thesis"], score=row["score"],
            needs_review=bool(row["needs_review"]),
            discovery_origin=row["discovery_origin"],
            evidence_tier=row["evidence_tier"],
            review_required=bool(row["review_required"]),
            enrichment_status=row["enrichment_status"],
            enrichment_provider=row["enrichment_provider"],
            enrichment_updated_at=row["enrichment_updated_at"],
            notes=row["notes"],
            discovery_source=row["discovery_source"],
        )

    def _ensure_columns(self) -> None:
        existing = self._column_names()
        changed = False
        for name, definition in self.EXTRA_COLUMNS.items():
            if name in existing:
                continue
            self.conn.execute(f"ALTER TABLE persons ADD COLUMN {name} {definition}")
            changed = True
        if changed:
            self.conn.commit()

    def _column_names(self) -> set[str]:
        if self.db.backend == "postgres":
            rows = self.conn.execute(
                """SELECT column_name AS name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'persons'"""
            ).fetchall()
        else:
            rows = self.conn.execute("PRAGMA table_info(persons)").fetchall()
        return {row["name"] for row in rows}

    def _derive_legacy_discovery_metadata(self) -> None:
        """Fill only missing metadata on pre-migration discovery rows."""
        rows = self.conn.execute(
            """SELECT p.id, p.github_username, p.contact_info, p.discovery_origin,
                      p.evidence_tier, p.review_required, p.enrichment_status,
                      p.enrichment_provider, p.discovery_source,
                      EXISTS (
                          SELECT 1 FROM provider_identities pi
                          WHERE pi.person_id = p.id
                      ) AS has_provider_identity
               FROM persons p WHERE p.cohort = 'discovery'"""
        ).fetchall()
        changed = False
        for row in rows:
            contact = self.loads(row["contact_info"], {})
            origin = row["discovery_origin"]
            if not origin:
                provider_discovery = (
                    contact.get("discovery_lane") == "provider_search"
                    or (
                        not row["github_username"]
                        and (
                            bool(row["has_provider_identity"])
                            or contact.get("discovered_via") in ("pdl", "coresignal")
                        )
                    )
                )
                if provider_discovery:
                    origin = "provider_search"
                elif row["github_username"]:
                    origin = "github"
                else:
                    origin = contact.get("discovered_via") or "legacy"

            tier = row["evidence_tier"]
            review_required = bool(row["review_required"])
            if origin == "provider_search" and not tier:
                scored = self.conn.execute(
                    """SELECT signal_type, metadata FROM signals
                       WHERE person_id = ? AND source IN ('pdl', 'coresignal')""",
                    (row["id"],),
                ).fetchall()
                has_dated_movement = any(
                    signal["signal_type"] == "job_change"
                    or (
                        signal["signal_type"] == "linkedin_created_recently"
                        and self.loads(signal["metadata"], {}).get("evidence")
                        == "provider_first_seen"
                    )
                    for signal in scored
                )
                tier = "verified" if has_dated_movement else "review"
                review_required = tier == "review"

            enrichment_status = row["enrichment_status"]
            enrichment_provider = row["enrichment_provider"]
            if origin == "github" and not enrichment_status:
                enrichment_provider = contact.get("enriched_by")
                enrichment_status = (
                    "provider_enriched" if enrichment_provider else "pending_budget"
                )
            elif enrichment_status == "provider_enriched" and not enrichment_provider:
                enrichment_provider = contact.get("enriched_by")

            source = row["discovery_source"]
            if not source and origin == "provider_search":
                provider = contact.get("discovered_via")
                if provider in ("pdl", "coresignal"):
                    source = f"{provider}_discovery"

            if (
                origin == row["discovery_origin"]
                and tier == row["evidence_tier"]
                and review_required == bool(row["review_required"])
                and enrichment_status == row["enrichment_status"]
                and enrichment_provider == row["enrichment_provider"]
                and source == row["discovery_source"]
            ):
                continue
            self.conn.execute(
                """UPDATE persons
                   SET discovery_origin = ?, evidence_tier = ?,
                       review_required = ?, enrichment_status = ?,
                       enrichment_provider = ?, discovery_source = ?
                   WHERE id = ?""",
                (
                    origin,
                    tier,
                    int(review_required),
                    enrichment_status,
                    enrichment_provider,
                    source,
                    row["id"],
                ),
            )
            changed = True
        if changed:
            self.conn.commit()
