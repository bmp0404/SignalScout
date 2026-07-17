"""Opt-in lead-gen: curated research-lab targets -> early-career OpenAlex
authors become new discovery people. No HTML scraping of lab "people" pages —
matches authors against recent works by OpenAlex institution id (precise,
where OpenAlex has resolved the lab) or a raw-affiliation-string search
(fallback for labs OpenAlex hasn't resolved, e.g. Stanford SAIL/Berkeley
BAIR). Mirrors the curated opt-in shape of `fellowship_seeds.py`.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.db.repositories.graph_edges import GraphEdgeRepository
from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.signals import SignalRepository
from backend.discovery.entity_resolution import normalize_name
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.domain.signal import Signal
from backend.scrapers.openalex import OpenAlexClient, OpenAlexScraper

logger = logging.getLogger(__name__)

# Only works published this recently count as evidence someone is a current
# (not emeritus/departed) lab member.
LOOKBACK_DAYS = 730


@dataclass
class OpenAlexLabResult:
    created: list[Person] = field(default_factory=list)
    considered: int = 0


class OpenAlexLabExpander:
    def __init__(
        self,
        persons: PersonRepository,
        signals: SignalRepository,
        edges: GraphEdgeRepository,
        client: OpenAlexClient,
        targets_file: Path,
    ):
        self.persons = persons
        self.signals = signals
        self.edges = edges
        self.client = client
        self.targets_file = targets_file

    def expand(
        self, max_new: int = 10, works_per_target: int = 25,
        on_progress: Callable[[str, int], None] | None = None,
    ) -> OpenAlexLabResult:
        result = OpenAlexLabResult()
        targets = self._load_targets()
        from_date = (datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        seen_author_ids: set[str] = set()

        for target in targets:
            if len(result.created) >= max_new:
                break
            works = self.client.works_by_affiliation(
                target.get("affiliation", ""),
                from_date=from_date,
                limit=works_per_target,
                institution_id=target.get("institution_id"),
            )
            for work in works:
                if len(result.created) >= max_new:
                    break
                self._process_work(work, target, seen_author_ids, result, max_new)

            if on_progress:
                on_progress("openalex", len(result.created))
        return result

    def _process_work(
        self, work: dict, target: dict,
        seen_author_ids: set[str], result: OpenAlexLabResult, max_new: int,
    ) -> None:
        authorships = work.get("authorships") or []
        names = [
            a["author"]["display_name"] for a in authorships
            if a.get("author", {}).get("display_name")
        ]
        for authorship in authorships:
            if len(result.created) >= max_new:
                return
            author_ref = authorship.get("author") or {}
            author_id, name = author_ref.get("id"), author_ref.get("display_name")
            if not author_id or not name or author_id in seen_author_ids:
                continue
            seen_author_ids.add(author_id)
            if self.persons.find_by_name(name):
                continue  # already known — no duplicate created

            result.considered += 1
            author = self.client.author(author_id)
            if not author or not OpenAlexScraper.is_early_career(author):
                continue  # established researcher, not the pre-breakout person we want

            person = self._build_person(name, target, work)
            self.persons.save(person)
            new_signals = self._build_signals(person, work, target)
            for signal in new_signals:
                signal.person_id = person.id
            self.signals.save_many(new_signals)
            self.edges.save_many(self._build_coauthor_edges(person, names, work))
            result.created.append(person)
            logger.info("openalex lab lead: %s (%s)", person.name, target.get("school"))

    @staticmethod
    def _build_person(name: str, target: dict, work: dict) -> Person:
        return Person(
            name=name,
            cohort="discovery",
            discovery_origin="openalex_lab",
            school=target.get("school"),
            area=target.get("area"),
        )

    @staticmethod
    def _work_date(work: dict) -> str:
        year = work.get("publication_year")
        today = datetime.now(timezone.utc).date().isoformat()
        return work.get("publication_date") or (f"{year}-01-01" if year else today)

    def _build_signals(self, person: Person, work: dict, target: dict) -> list[Signal]:
        title = (work.get("title") or "").strip()
        if not title:
            return []
        date = self._work_date(work)
        lab = target.get("affiliation") or target.get("school") or "a research lab"
        return [
            Signal(
                person_name=person.name, signal_type="research_paper",
                signal_category="research", signal_date=date,
                signal_strength=0.6, source="openalex",
                source_url=work.get("id") or "",
                summary=f'Published "{title[:80]}" ({lab})',
                raw_data={"school": target.get("school"), "area": target.get("area")},
            )
        ]

    def _build_coauthor_edges(self, person: Person, names: list[str], work: dict) -> list[GraphEdge]:
        title = (work.get("title") or "").strip()
        date = self._work_date(work)
        person_key = normalize_name(person.name)
        edges: list[GraphEdge] = []
        for coauthor in names:
            if normalize_name(coauthor) == person_key:
                continue
            edges.append(
                GraphEdge(
                    source_name=person.name, target_name=coauthor,
                    edge_type="co_author", observed_date=date,
                    source="openalex", metadata={"paper": title[:120]},
                )
            )
        return edges

    def _load_targets(self) -> list[dict]:
        if not self.targets_file.exists():
            return []
        try:
            return json.loads(self.targets_file.read_text()).get("targets", [])
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("could not read openalex targets file %s: %s", self.targets_file, exc)
            return []
