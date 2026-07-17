"""DiscoveryJobManager: runs the live discovery pipeline in a background thread
and exposes in-memory stage progress for polling.

Single global job (a `threading.Lock` guards start + state). The pipeline maps to
four stages the UI animates: Scrape -> Resolve -> Enrich -> Score. The worker
builds its OWN Container (its own SQLite connection) so its writes never collide
with the API's read connection; the status endpoint only ever touches in-memory
state, so polling stays cheap and DB-free.
"""

import copy
import json
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from backend.config import Settings
from backend.discovery.collab_expansion import CollaborationExpander
from backend.discovery.fellowship_seeds import FellowshipSeedLoader
from backend.discovery.graph_expansion import GraphExpander
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.domain.signal import Signal
from backend.enrichment.provider_enricher import build_provider_chain
from backend.scrapers.devpost_scraper import DevpostScraper
from backend.scrapers.github_scraper import GithubClient, GithubScraper
from backend.scrapers.openalex import OpenAlexScraper
from backend.scrapers.semantic_scholar import SemanticScholarScraper

if TYPE_CHECKING:  # avoid a Container <-> DiscoveryJobManager import cycle
    from backend.container import Container

logger = logging.getLogger(__name__)

STAGES = ("scrape", "resolve", "enrich", "score")
SOURCES = ("github", "pdl", "coresignal", "semantic_scholar", "devpost", "openalex")


class DiscoveryJobManager:
    def __init__(self, settings: Settings, container_factory: "Callable[[], Container]"):
        self.settings = settings
        self._container_factory = container_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = self._idle_state()

    @staticmethod
    def _idle_state() -> dict:
        return {
            "job_id": None,
            "state": "idle",  # idle | running | done | error
            "stages": [{"name": name, "status": "pending", "count": 0} for name in STAGES],
            "discovered_count": 0,
            # Per-source discovery counts so the team can watch GitHub's share fall.
            "source_counts": {name: 0 for name in SOURCES},
            "started_at": None,
            "finished_at": None,
            "error": None,
        }

    def status(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def start(self) -> str:
        """Begin a scoped background run. Raises RuntimeError if one is already
        running (-> 409) or ValueError if no lane is configured (-> 400). The
        provider-search lane needs a PDL/Coresignal key; the GitHub lane needs a
        GITHUB_TOKEN. At least one must be present."""
        with self._lock:
            if self._state["state"] == "running":
                raise RuntimeError("a discovery run is already in progress")
            if not self.settings.github_token and not build_provider_chain(self.settings):
                raise ValueError(
                    "No discovery lane configured — set PDL_API_KEY/CORESIGNAL_API_KEY "
                    "for provider search and/or GITHUB_TOKEN for GitHub expansion"
                )
            job_id = uuid.uuid4().hex[:12]
            self._state = self._idle_state()
            self._state.update(
                job_id=job_id,
                state="running",
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            self._thread = threading.Thread(target=self._run, args=(job_id,), daemon=True)
            self._thread.start()
            return job_id

    def _resolve_seeds(self, container: "Container") -> list[str]:
        """Prefer curated `demo_seeds` (young, dense-orbit prior discoveries) that
        actually exist in this DB; fall back to the founder `github_seeds`. Sliced
        to `discovery_seed_limit` to keep an on-camera run short."""
        data = json.loads(self.settings.seed_accounts_file.read_text())
        demo = [s for s in data.get("demo_seeds", []) if container.persons.find_by_github(s)]
        seeds = demo or data["github_seeds"]
        if self.settings.discovery_include_fellowship_seeds:
            fellowship = FellowshipSeedLoader(
                container.persons, container.edges, self.settings.fellowship_alumni_file
            )
            seeds.extend(login for login in fellowship.load() if login not in seeds)
        return seeds[: self.settings.discovery_seed_limit]

    def _set_stage(self, name: str, status: str | None = None, count: int | None = None) -> None:
        with self._lock:
            for stage in self._state["stages"]:
                if stage["name"] == name:
                    if status is not None:
                        stage["status"] = status
                    if count is not None:
                        stage["count"] = count
                    break

    def _set_source_count(self, source: str, count: int) -> None:
        with self._lock:
            if source in self._state["source_counts"]:
                self._state["source_counts"][source] = count

    def _run_provider_lane(self, container: "Container") -> list:
        """Budgeted provider-search discovery (PDL -> Coresignal). Returns the
        newly-created discovery people and records per-source counts."""
        if not container.provider_chain:
            return []
        result = container.provider_expander.expand(on_progress=self._set_source_count)
        for source, count in result.source_counts.items():
            self._set_source_count(source, count)
        logger.info(
            "provider search: created=%d verified=%d review=%d merged=%d "
            "duplicates=%d rejected=%d reasons=%s pages=%d requests=%d "
            "returned=%d credit_units=%d",
            len(result.created),
            result.verified,
            result.review,
            result.merged,
            result.duplicates,
            result.rejected,
            result.rejection_reasons,
            result.requested_pages,
            result.api_requests,
            result.returned_records,
            result.credit_units,
        )
        return result.created

    def _run_openalex_lab_lane(self, container: "Container") -> list:
        """Opt-in curated-lab lead-gen (backend/discovery/openalex_labs.py). Off
        unless `discovery_include_openalex` is set, same convention as fellowship
        seeds. Returns the newly-created discovery people."""
        if not self.settings.discovery_include_openalex:
            return []
        result = container.openalex_lab_expander.expand(on_progress=self._set_source_count)
        logger.info(
            "openalex lab lead-gen: created=%d considered=%d",
            len(result.created), result.considered,
        )
        return result.created

    def _run_github_lane(
        self, container: "Container", token: str, on_progress
    ) -> "tuple[GithubScraper, list]":
        seeds = self._resolve_seeds(container)
        scraper = GithubScraper(GithubClient(token), [])
        expander = GraphExpander(scraper, container.persons, container.edges)
        discovered = expander.expand(
            seeds,
            max_per_seed=self.settings.discovery_max_per_seed,
            on_progress=on_progress,
            # tight collab caps: keep the scoped on-camera run short
            repos_per_seed=2,
            contributors_per_repo=15,
            org_members_per_seed=15,
        )
        return scraper, discovered

    @staticmethod
    def _save_collected(
        container: "Container", signals: list[Signal], edges: list[GraphEdge]
    ) -> None:
        if signals:
            container.resolver.resolve_signals(signals)
            container.signals.save_many(signals)
        if edges:
            container.resolver.resolve_edges(edges)
            container.edges.save_many(edges)

    def _run_collaboration_lane(
        self,
        container: "Container",
        github: GithubScraper | None,
        fresh_people: list[Person],
    ) -> list[Person]:
        """Collect capped public collaboration evidence, then promote dead ends."""
        fresh_ids = {person.id for person in fresh_people}
        pool = fresh_people + [
            person
            for person in container.persons.all("discovery")
            if person.id not in fresh_ids
        ]
        scholar = SemanticScholarScraper()
        openalex = container.openalex_scraper
        devpost = DevpostScraper()
        scholar_checked = openalex_checked = devpost_checked = 0
        for person in pool:
            if scholar_checked < 8 and scholar.has_real_name(person):
                if not any(
                    signal.source == "semantic_scholar"
                    for signal in container.signals.for_person(person.id)
                ):
                    scholar_checked += 1
                    author = scholar.find_author(person.name)
                    self._save_collected(container, *scholar.collect(person, author=author))
                    self._save_collected(
                        container, *scholar.collect_citations(person, author=author)
                    )
            if openalex_checked < 8 and openalex.has_real_name(person):
                if not any(
                    signal.source == "openalex"
                    for signal in container.signals.for_person(person.id)
                ):
                    openalex_checked += 1
                    self._save_collected(container, *openalex.collect(person))
            if devpost_checked < 8 and person.github_username:
                if not any(
                    signal.source == "devpost"
                    for signal in container.signals.for_person(person.id)
                ):
                    devpost_checked += 1
                    self._save_collected(
                        container, *devpost.collect(person, person.github_username)
                    )
            if scholar_checked >= 8 and openalex_checked >= 8 and devpost_checked >= 8:
                break

        result = CollaborationExpander(
            container.persons,
            container.signals,
            container.edges,
            github,
            devpost,
            scholar,
            container.provider_enricher,
            openalex=openalex,
        ).expand(max_promotions=self.settings.collaboration_promotion_cap)
        for source, count in result.source_counts.items():
            self._set_source_count(source, count)
        return result.promoted

    def _run(self, job_id: str) -> None:
        container: "Container | None" = None
        try:
            container = self._container_factory()
            token = self.settings.github_token

            self._set_stage("scrape", status="active")

            def on_progress(stage: str, count: int) -> None:
                self._set_stage(stage, status="active", count=count)

            # LANE 1 (LEAD): provider search — creates discovery people with no
            # GitHub account required. Runs first so it is the primary source.
            provider_people = self._run_provider_lane(container)

            # LANE 1b (LEAD, opt-in): curated-lab lead-gen via OpenAlex —
            # early-career authors at target labs, also no GitHub account required.
            openalex_lab_people = self._run_openalex_lab_lane(container)

            # LANE 2: GitHub expansion — every find is cross-corroborated by
            # pushing it through the provider enrichment chain below.
            github_people: list = []
            scraper: GithubScraper | None = None
            if token:
                scraper, github_people = self._run_github_lane(container, token, on_progress)
            self._set_source_count("github", len(github_people))

            collaboration_people = self._run_collaboration_lane(
                container, scraper, provider_people + openalex_lab_people + github_people
            )
            discovered = provider_people + openalex_lab_people + github_people + collaboration_people
            self._set_stage("scrape", status="done")
            self._set_stage("resolve", status="done", count=len(discovered))

            self._set_stage("enrich", status="active")
            github_ids = {person.id for person in github_people}
            graph_people = github_people + collaboration_people
            for i, person in enumerate(graph_people, start=1):
                if person.id in github_ids:
                    signals = scraper.scrape_user(person.github_username)
                    container.resolver.resolve_signals(signals)
                    container.signals.save_many(signals)
                else:
                    signals = container.signals.for_person(person.id)
                container.contact_enricher.enrich(person, signals)
                container.location_resolver.resolve(person, signals)
                container.persons.save(person)
                self._set_stage("enrich", count=i)

            queue = container.provider_enricher.prioritize([
                person
                for person in container.persons.all("discovery")
                if person.github_username
                and person.enrichment_status in (None, "pending_budget", "provider_error")
            ])
            for person in queue:
                container.provider_enricher.run(person)
                container.persons.save(person)
            self._set_stage("enrich", status="done", count=len(discovered))

            self._set_stage("score", status="active")
            container.candidate_service.rescore_all()
            self._set_stage("score", status="done")

            with self._lock:
                self._state["state"] = "done"
                self._state["discovered_count"] = len(discovered)
                self._state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
            logger.exception("discovery job %s failed", job_id)
            with self._lock:
                self._state["state"] = "error"
                self._state["error"] = str(exc)
                self._state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for stage in self._state["stages"]:
                    if stage["status"] == "active":
                        stage["status"] = "error"
        finally:
            if container is not None:
                container.db.close()
