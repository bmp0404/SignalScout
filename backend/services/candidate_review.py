"""Workflow rules for one-click candidate review (approve / reject / unreviewed)."""

from backend.db.repositories.candidate_reviews import CandidateReviewRepository
from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.signals import SignalRepository
from backend.domain.candidate_review import CandidateReview


class CandidateReviewService:
    def __init__(
        self,
        reviews: CandidateReviewRepository,
        persons: PersonRepository,
        signals: SignalRepository,
    ):
        self.reviews = reviews
        self.persons = persons
        self.signals = signals

    def review(
        self,
        person_id: str,
        state: str,
        why_now: str = "",
        notes: str = "",
        source_bucket: str = "",
        contactable: bool = False,
        primary_evidence_url: str = "",
        reviewer: str = "",
    ) -> CandidateReview:
        person = self.persons.get(person_id)
        if not person or person.cohort != "discovery":
            raise ValueError("Review target must be an existing discovery candidate.")
        # One-click approve: treat approved people as digest-eligible unless explicitly opted out.
        effective_contactable = contactable
        if state == "approved" and not contactable:
            effective_contactable = True
        return self.reviews.upsert(
            person_id,
            state,
            why_now,
            notes,
            source_bucket,
            effective_contactable,
            primary_evidence_url,
            reviewer,
        )

    def list_rows(self, state: str | None = None) -> list[dict]:
        results = []
        for review in self.reviews.all(state):
            person = self.persons.get(review.person_id)
            if not person:
                continue
            results.append(
                {
                    **review.__dict__,
                    "name": person.name,
                    "contacts": person.display_contacts(),
                }
            )
        return results

    def approved_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for review in self.reviews.approved_contactable():
            mix[review.source_bucket] = mix.get(review.source_bucket, 0) + 1
        return dict(sorted(mix.items()))
