"""Free-source lead resolution: dedupe-first matching against existing
candidates, then a bounded paid lookup for anyone unresolved. Source-specific
HTML parsing lives in fellowship_scraper.py / competition_scraper.py — this
module only ever sees the extracted RawLead, never raw HTML.
"""

from dataclasses import dataclass, field

from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.provider_identities import ProviderIdentityRepository
from backend.discovery.entity_resolution import normalize_name
from backend.domain.person import Person
from backend.enrichment.provider_enricher import ProviderEnricher


@dataclass
class RawLead:
    """The strongest identifiers a free-source scraper could extract for one
    person. Everything but name/source is best-effort and may be empty."""

    name: str
    source: str  # e.g. "z_fellows", "usaco"
    source_url: str = ""
    school: str | None = None
    company: str | None = None
    year: int | None = None
    linkedin_url: str | None = None
    personal_site: str | None = None
    github_username: str | None = None


@dataclass
class ResolveResult:
    matched: list[Person] = field(default_factory=list)      # already in the DB
    created: list[Person] = field(default_factory=list)       # newly identified via PDL Identify
    unresolved: list[RawLead] = field(default_factory=list)   # no match, no paid lookup possible


class LeadResolver:
    """Dedupe-first: every lead is checked against existing candidates before
    any paid lookup. Reuses ProviderEnricher.run() — the same PDL/Coresignal
    single-person enrichment path GitHub candidates go through — for the
    "PDL Identify" step, so budget/cache/signal-emission behave identically."""

    def __init__(
        self,
        persons: PersonRepository,
        identities: ProviderIdentityRepository,
        enricher: ProviderEnricher,
    ):
        self.persons = persons
        self.identities = identities
        self.enricher = enricher

    def resolve(self, leads: list[RawLead]) -> ResolveResult:
        result = ResolveResult()
        for lead in leads:
            existing = self._find_existing(lead)
            if existing is not None:
                result.matched.append(existing)
                continue
            person = self._identify(lead)
            if person is not None:
                result.created.append(person)
            else:
                result.unresolved.append(lead)
        return result

    def _find_existing(self, lead: RawLead) -> Person | None:
        """Standard ladder minus provider ID (free sources don't have one):
        LinkedIn URL -> normalized name + school."""
        person_id = self.identities.find_person_by_linkedin(lead.linkedin_url)
        if person_id:
            found = self.persons.get(person_id)
            if found:
                return found
        key = normalize_name(lead.name)
        school = (lead.school or "").strip().lower() or None
        for person in self.persons.all():
            if normalize_name(person.name) != key:
                continue
            if school and person.school and school in person.school.lower():
                return person
            if not school and not person.school:
                return person
        return None

    def _identify(self, lead: RawLead) -> Person | None:
        """Save a tentative row first — ProviderEnricher.run() derives Signal
        rows keyed by person.id, and the signals table FK-references persons —
        then delete it if PDL Identify doesn't confirm a match. Nothing is
        derived (no signals, no cost) on a non-match, so the delete is clean."""
        person = Person(
            name=lead.name,
            cohort="discovery",
            school=lead.school,
            linkedin_url=lead.linkedin_url,
            github_username=lead.github_username,
            personal_site=lead.personal_site,
            discovery_origin=lead.source,
            discovery_source=lead.source,
        )
        self.persons.save(person)
        outcome = self.enricher.run(person)
        if outcome.status != "matched":
            self.persons.delete(person.id)
            return None
        self.persons.save(person)
        return person
