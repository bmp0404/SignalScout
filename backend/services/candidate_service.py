"""CandidateService: scores the live cohort and builds the payloads the UI needs —
score receipts, connection context, warm-intro paths, and "why now" lines."""

from datetime import date, datetime

from backend.db.repositories.candidate_reviews import CandidateReviewRepository
from backend.db.repositories.graph_edges import GraphEdgeRepository
from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.signals import SignalRepository
from backend.domain.graph_edge import EDGE_QUALITY, GraphEdge
from backend.domain.person import Person
from backend.scoring.engine import ScoreBreakdown, ScoringEngine
from backend.scoring.reference import founder_reference

EDGE_VERBS = {
    "github_follows": "follows them on GitHub",
    "mutual_star": "starred their work on GitHub",
    "starred_repo": "had a repository starred by this candidate",
    "forked_repo": "had a repository forked by this candidate",
    "issue_pr_interaction": "received an issue or pull request from this candidate",
    "co_author": "co-authored a paper with them",
    "co_contributor": "contributes to the same repo as them",
    "org_mate": "shares a GitHub org with them",
    "hackathon_teammate": "was their hackathon teammate",
    "fellowship_cohort": "shared a fellowship cohort with them",
    "twitter_follows": "follows them on X",
}


class CandidateService:
    def __init__(
        self,
        persons: PersonRepository,
        signals: SignalRepository,
        edges: GraphEdgeRepository,
        engine: ScoringEngine,
        flag_threshold: float,
        reviews: CandidateReviewRepository | None = None,
    ):
        self.persons = persons
        self.signals = signals
        self.edges = edges
        self.reviews = reviews
        self.engine = engine
        self.flag_threshold = flag_threshold

    def rescore_all(self) -> dict[str, float]:
        """Score every non-control person as of today and persist, calibrated
        against the strong-founder pack so a discovery's 0-100 score means
        'how founder-like' on the same scale the backtest uses."""
        today = date.today()
        cohort = [p for p in self.persons.all() if p.cohort in ("founder", "discovery", "demo")]
        seed_ids = {p.id for p in cohort if p.cohort == "founder"}
        adjusted: dict[str, float] = {}
        for person in cohort:
            sigs = self.signals.for_person(person.id)
            person_edges = self.edges.for_person(person.id)
            conn = self.engine.connection_signal(person, person_edges, seed_ids - {person.id}, today)
            if conn:
                sigs = sigs + [conn]
            base = self.engine.compute(person, sigs, today).adjusted
            adjusted[person.id] = base * self._knownness_factor(person)
        reference = founder_reference(self.persons, self.signals, self.edges, self.engine)
        normalized = self.engine.normalize_calibrated(adjusted, reference)
        for person_id, score in normalized.items():
            self.persons.update_score(person_id, score)
        return normalized

    @staticmethod
    def _knownness_factor(person: Person) -> float:
        """Down-weight already-famous accounts so ranking favors pre-breakout people.

        Only applies when we have a GitHub follower count (live discoveries);
        founders and seeded profiles are unaffected.
        """
        followers = (person.contact_info or {}).get("github_followers")
        if followers is None:
            return 1.0
        if followers <= 500:
            return 1.0
        if followers <= 1000:
            return 0.7
        if followers <= 2000:
            return 0.5
        return 0.3

    def list_candidates(self, cohort: str | None = "discovery") -> list[dict]:
        people = self.persons.all(cohort) if cohort else [
            p for p in self.persons.all() if p.cohort != "control"
        ]
        people = [p for p in people if p.score is not None]
        people.sort(key=lambda p: -(p.score or 0))
        founders_by_id = {p.id: p for p in self.persons.all("founder")}
        return [self._summary(p, founders_by_id) for p in people]

    def profile(self, person_id: str) -> dict | None:
        person = self.persons.get(person_id)
        if not person:
            return None
        founders_by_id = {p.id: p for p in self.persons.all("founder")}
        seed_ids = set(founders_by_id) - {person.id}
        today = date.today()
        sigs = self.signals.for_person(person.id)
        person_edges = self.edges.for_person(person.id)
        conn = self.engine.connection_signal(person, person_edges, seed_ids, today)
        all_sigs = sigs + [conn] if conn else sigs
        breakdown = self.engine.compute(person, all_sigs, today)
        summary = self._summary(person, founders_by_id)
        summary.update({
            "breakdown": self._breakdown_dict(breakdown),
            "timeline": [
                {"date": s.signal_date, "type": s.signal_type, "category": s.signal_category,
                 "summary": s.summary, "strength": s.signal_strength, "source": s.source,
                 "source_url": s.source_url}
                for s in sorted(all_sigs, key=lambda s: s.signal_date)
            ],
            "connections": self._connection_list(person, person_edges, founders_by_id),
            "contact_links": person.display_contacts(),
            "contact_info": person.contact_info,
        })
        return summary

    def _summary(self, person: Person, founders_by_id: dict[str, Person]) -> dict:
        sigs = self.signals.for_person(person.id)
        review = self.reviews.get(person.id) if self.reviews else None
        person_edges = self.edges.for_person(person.id)
        top = sorted(sigs, key=lambda s: -(s.signal_strength * 1.0))[:3]
        seed_edges = self._seed_edges(person, person_edges, founders_by_id)
        source_counts = self._source_counts(sigs)
        return {
            "id": person.id, "name": person.name, "cohort": person.cohort,
            "score": person.score, "flagged": (person.score or 0) >= self.flag_threshold,
            "area": person.area, "school": person.school,
            "graduation_year": person.graduation_year,
            "origin_location": person.origin_location,
            "current_location": person.current_location,
            "region": person.region, "fellowship": person.fellowship,
            "breakout_date": person.breakout_date, "thesis": person.thesis,
            "top_signals": [
                {"type": s.signal_type, "category": s.signal_category, "summary": s.summary,
                 "date": s.signal_date, "strength": s.signal_strength,
                 "source": s.source, "source_url": s.source_url}
                for s in top
            ],
            "signal_count": len(sigs),
            "source_counts": source_counts,
            "source_diversity": len(source_counts),
            "github_username": person.github_username,
            "github_followers": (person.contact_info or {}).get("github_followers"),
            "connection_count": len({e.id for e in seed_edges}),
            "connection_context": self._connection_context(seed_edges, founders_by_id),
            "warm_intro": self._warm_intro(person, seed_edges, founders_by_id),
            "why_now": review.why_now if review and review.why_now else self._why_now(sigs),
            "reviewed_why_now": review.why_now if review else "",
            "approval_state": review.state if review else "unreviewed",
            "approved_at": review.approved_at if review else None,
            "source_bucket": review.source_bucket if review else "",
            "contactable": review.contactable if review else False,
            "primary_evidence_url": review.primary_evidence_url if review else "",
            "contact_links": person.display_contacts(),
            "coverage": self._coverage(sigs),
            "discovery_origin": person.discovery_origin,
            "evidence_status": person.evidence_tier or "not_applicable",
            "evidence_tier": person.evidence_tier,
            "review_required": person.review_required,
            "enrichment_status": person.enrichment_status,
            "enrichment_provider": person.enrichment_provider,
            "enrichment_updated_at": person.enrichment_updated_at,
        }

    def _connection_list(self, person: Person, edges: list[GraphEdge], founders_by_id: dict[str, Person]) -> list[dict]:
        result = []
        for edge in self._seed_edges(person, edges, founders_by_id):
            founder = founders_by_id.get(edge.source_person_id) or founders_by_id.get(edge.target_person_id)
            if not founder:
                continue
            verb = EDGE_VERBS.get(edge.edge_type, "is connected to them")
            result.append({
                "edge_type": edge.edge_type,
                "founder": founder.name,
                "description": f"{founder.name} {verb}",
                "observed_date": edge.observed_date,
                "source": edge.source,
            })
        result.sort(key=lambda c: c["observed_date"])
        return result

    def _seed_edges(self, person: Person, edges: list[GraphEdge], founders_by_id: dict[str, Person]) -> list[GraphEdge]:
        result = []
        for edge in edges:
            other = edge.source_person_id if edge.target_person_id == person.id else edge.target_person_id
            if other in founders_by_id and other != person.id:
                result.append(edge)
        return result

    @staticmethod
    def _connection_context(seed_edges: list[GraphEdge], founders_by_id: dict[str, Person]) -> str:
        if not seed_edges:
            return ""
        follows = set()
        other_parts = []
        for edge in seed_edges:
            founder = founders_by_id.get(edge.source_person_id) or founders_by_id.get(edge.target_person_id)
            if not founder:
                continue
            if edge.edge_type in ("github_follows", "twitter_follows"):
                follows.add(founder.name)
            else:
                verb = EDGE_VERBS.get(edge.edge_type, "is connected to them")
                other_parts.append(f"{founder.name} {verb}")
        parts = []
        if follows:
            parts.append(f"followed by {len(follows)} known founder{'s' if len(follows) != 1 else ''} on GitHub")
        parts.extend(other_parts[:2])
        if not parts:
            return ""
        text = "; ".join(parts)
        return text[0].upper() + text[1:]

    @staticmethod
    def _warm_intro(person: Person, seed_edges: list[GraphEdge], founders_by_id: dict[str, Person]) -> str:
        if not seed_edges:
            return ""
        best = max(seed_edges, key=lambda e: EDGE_QUALITY.get(e.edge_type, 0))
        founder = founders_by_id.get(best.source_person_id) or founders_by_id.get(best.target_person_id)
        if not founder:
            return ""
        verb = EDGE_VERBS.get(best.edge_type, "knows them")
        return f"Reach out via {founder.name}, who {verb}."

    @staticmethod
    def _why_now(signals: list[dict] | list) -> str:
        if not signals:
            return ""
        recent = sorted(signals, key=lambda s: s.signal_date, reverse=True)
        newest = recent[0]
        for s in recent:
            meta = s.metadata if isinstance(s.metadata, dict) else {}
            stars, prev = meta.get("stars"), meta.get("stars_prev")
            if stars and prev and prev > 0 and stars / prev >= 1.8:
                return f"{s.summary.split(',')[0]} — stars up {stars / prev:.1f}x since {meta.get('stars_prev_date', 'last check')[:7]}."
        age_days = (date.today() - datetime.strptime(newest.signal_date[:10], "%Y-%m-%d").date()).days
        if age_days <= 365:
            return f"Latest signal {newest.signal_date[:7]}: {newest.summary}."
        return f"Most recent signal: {newest.summary} ({newest.signal_date[:7]})."

    @staticmethod
    def _source_counts(signals: list) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in signals:
            counts[s.source] = counts.get(s.source, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def source_mix(self, cohort: str = "discovery") -> dict[str, int]:
        """Signal counts by source across a cohort — shows whether GitHub's share
        is actually falling rather than inferring it from a few candidates."""
        counts: dict[str, int] = {}
        for person in self.persons.all(cohort):
            for s in self.signals.for_person(person.id):
                counts[s.source] = counts.get(s.source, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    @staticmethod
    def _coverage(signals: list) -> str:
        sources = {s.source for s in signals}
        if len(sources) >= 3:
            return "HIGH"
        if len(sources) == 2:
            return "MED"
        return "LOW"

    @staticmethod
    def _breakdown_dict(breakdown: ScoreBreakdown) -> dict:
        return {
            "raw": breakdown.raw, "items": breakdown.items,
            "diversity_multiplier": breakdown.diversity_multiplier,
            "recency_bonus": breakdown.recency_bonus,
            "age_factor": breakdown.age_factor, "adjusted": breakdown.adjusted,
        }
