"""Promote unresolved public collaboration edges into real discovery people."""

import logging
from dataclasses import dataclass, field

from backend.db.repositories.graph_edges import GraphEdgeRepository
from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.signals import SignalRepository
from backend.discovery.entity_resolution import normalize_name
from backend.discovery.graph_expansion import GraphExpander
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.domain.signal import Signal
from backend.enrichment.provider_enricher import ProviderEnricher
from backend.scrapers.devpost_scraper import DevpostScraper
from backend.scrapers.github_scraper import GithubScraper, parse_grad_year
from backend.scrapers.openalex import OpenAlexScraper
from backend.scrapers.semantic_scholar import SemanticScholarScraper

logger = logging.getLogger(__name__)

COLLAB_EDGE_TYPES = ("hackathon_teammate", "co_author", "paper_citation")


@dataclass
class CollaborationExpansionResult:
    promoted: list[Person] = field(default_factory=list)
    considered: int = 0
    source_counts: dict[str, int] = field(
        default_factory=lambda: {"devpost": 0, "semantic_scholar": 0, "openalex": 0}
    )


class CollaborationExpander:
    """Resolve dead-end teammate/co-author edges using their original public source.

    Devpost promotion requires an unknown GitHub account with independent profile
    evidence. Scholar promotion requires one unambiguous low-paper-count author
    match and at least one paper with a real publication year. The per-run cap
    limits newly created people, not harmless resolution of already-known people.
    """

    def __init__(
        self,
        persons: PersonRepository,
        signals: SignalRepository,
        edges: GraphEdgeRepository,
        github: GithubScraper | None,
        devpost: DevpostScraper,
        scholar: SemanticScholarScraper,
        provider_enricher: ProviderEnricher | None = None,
        openalex: OpenAlexScraper | None = None,
    ):
        self.persons = persons
        self.signals = signals
        self.edges = edges
        self.github = github
        self.devpost = devpost
        self.scholar = scholar
        self.provider_enricher = provider_enricher
        self.openalex = openalex

    def expand(self, max_promotions: int = 15, follower_cap: int = 2000) -> CollaborationExpansionResult:
        result = CollaborationExpansionResult()
        groups = self._unresolved_groups()
        self._mark_repeats(groups)

        for group in groups.values():
            if len(result.promoted) >= max_promotions:
                break
            edge = group[0]
            existing = self.persons.find_by_name(edge.target_name)
            if existing:
                self._attach(group, existing)
                continue

            result.considered += 1
            person: Person | None = None
            new_signals: list[Signal] = []
            if edge.edge_type == "hackathon_teammate":
                person, new_signals = self._promote_devpost(edge, follower_cap)
            elif edge.edge_type in ("co_author", "paper_citation"):
                if edge.source == "openalex" and self.openalex is not None:
                    person, new_signals = self._promote_openalex(edge)
                else:
                    person, new_signals = self._promote_scholar(edge)
            if person is None:
                continue
            if self.persons.get(person.id):
                self._attach(group, person)
                continue

            self.persons.save(person)
            for signal in new_signals:
                signal.person_id = person.id
            self.signals.save_many(new_signals)
            self._attach(group, person)
            if self.provider_enricher is not None:
                self.provider_enricher.enrich(person)
                self.persons.save(person)
            result.promoted.append(person)
            result.source_counts[edge.source] = result.source_counts.get(edge.source, 0) + 1
            logger.info("promoted collaboration target %s from %s", person.name, edge.source)
        return result

    def _unresolved_groups(self) -> dict[tuple[str, str, str], list[GraphEdge]]:
        groups: dict[tuple[str, str, str], list[GraphEdge]] = {}
        for edge in self.edges.all():
            if edge.edge_type not in COLLAB_EDGE_TYPES or edge.target_person_id:
                continue
            source_key = edge.source_person_id or normalize_name(edge.source_name)
            key = (edge.edge_type, source_key, normalize_name(edge.target_name))
            groups.setdefault(key, []).append(edge)
        return groups

    def _mark_repeats(self, groups: dict[tuple[str, str, str], list[GraphEdge]]) -> None:
        changed: list[GraphEdge] = []
        for group in groups.values():
            labels = {
                edge.metadata.get("project") or edge.metadata.get("paper") or edge.id
                for edge in group
            }
            repeat = len(labels)
            for edge in group:
                if edge.metadata.get("repeat") != repeat:
                    edge.metadata["repeat"] = repeat
                    changed.append(edge)
        if changed:
            self.edges.save_many(changed)

    def _promote_devpost(
        self, edge: GraphEdge, follower_cap: int
    ) -> tuple[Person | None, list[Signal]]:
        if self.github is None:
            return None, []
        devpost_username = edge.metadata.get("devpost_username")
        if not devpost_username:
            return None, []
        linked_login = self.devpost.github_username(devpost_username)
        logins = [login for login in (linked_login, devpost_username) if login]
        for login in dict.fromkeys(logins):
            existing = self.persons.find_by_github(login)
            if existing:
                return existing, []
            profile = self.github.client.user(login)
            if not GraphExpander._is_unknown(profile, follower_cap):
                continue
            signals = self.github.scrape_user(login, user=profile)
            if not signals:
                continue
            person = Person(
                name=signals[0].person_name,
                github_username=login,
                cohort="discovery",
                discovery_origin="github",
                enrichment_status="pending_budget",
            )
            person.graduation_year = parse_grad_year(profile.get("bio"))
            person.contact_info.update(
                {
                    "devpost_username": devpost_username,
                    "devpost_url": f"https://devpost.com/{devpost_username}",
                    "github_followers": profile.get("followers", 0),
                    "github_created_at": profile.get("created_at"),
                }
            )
            return person, signals
        return None, []

    def _promote_scholar(self, edge: GraphEdge) -> tuple[Person | None, list[Signal]]:
        author = self.scholar.find_author(edge.target_name)
        if not author or not author.get("authorId"):
            return None, []
        candidate = Person(
            name=author.get("name") or edge.target_name,
            cohort="discovery",
            discovery_origin="semantic_scholar",
        )
        signals, _ = self.scholar.collect(candidate, author=author)
        dated = [signal for signal in signals if signal.raw_data.get("year")]
        if not dated:
            return None, []
        candidate.contact_info.update(
            {
                "semantic_scholar_author_id": author["authorId"],
                "semantic_scholar_url": author.get("url"),
            }
        )
        return candidate, dated

    def _promote_openalex(self, edge: GraphEdge) -> tuple[Person | None, list[Signal]]:
        author = self.openalex.find_author(edge.target_name)
        if not author or not author.get("id"):
            return None, []
        candidate = Person(
            name=author.get("display_name") or edge.target_name,
            cohort="discovery",
            discovery_origin="openalex",
        )
        signals, _ = self.openalex.collect(candidate, author=author)
        dated = [signal for signal in signals if signal.raw_data.get("year")]
        if not dated:
            return None, []
        candidate.contact_info.update({"openalex_author_id": author["id"]})
        return candidate, dated

    def _attach(self, group: list[GraphEdge], person: Person) -> None:
        for edge in group:
            edge.target_person_id = person.id
            edge.target_name = person.name
        self.edges.save_many(group)
