"""Semantic Scholar co-author scraper (free graph API, no key required).

Author search by name -> papers -> co-authors. Emits `co_authored_paper`
signals and `co_author` edges for discovery-cohort people with real names.

The unauthenticated API rate-limits aggressively (HTTP 429): every call backs
off with increasing waits and then fails soft (None/[]) — never fatal, so a
discovery run degrades gracefully when the API is throttling.
"""

import logging
import time
from datetime import datetime, timezone

import requests

from backend.discovery.entity_resolution import normalize_name
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.domain.signal import Signal

logger = logging.getLogger(__name__)

API = "https://api.semanticscholar.org/graph/v1"

# Above this many papers the author is an established academic, not the young
# pre-breakout person we resolved by name — treat the match as wrong/ambiguous.
MAX_AUTHOR_PAPERS = 50


class SemanticScholarClient:
    """Thin wrapper. All failures (including persistent 429s) return None/[]."""

    def __init__(self, max_retries: int = 3, backoff_seconds: float = 2.0):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "signal-scout/0.1 (research signal discovery)"})
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def _get(self, path: str, params: dict | None = None):
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(f"{API}{path}", params=params, timeout=15)
            except requests.RequestException as exc:
                logger.warning("semantic scholar request failed %s: %s", path, exc)
                return None
            if resp.status_code == 429:
                wait = self.backoff_seconds * (attempt + 1)
                logger.warning("semantic scholar 429 on %s — backing off %.1fs", path, wait)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                logger.warning("semantic scholar %s -> %s", path, resp.status_code)
                return None
            return resp.json()
        logger.warning("semantic scholar still rate-limited on %s — giving up", path)
        return None

    def search_author(self, name: str) -> list[dict]:
        data = self._get("/author/search", {"query": name, "fields": "name,paperCount,hIndex,url"})
        return (data or {}).get("data") or []

    def author_papers(self, author_id: str, limit: int = 10) -> list[dict]:
        data = self._get(
            f"/author/{author_id}/papers",
            {"fields": "paperId,title,year,url,authors", "limit": limit},
        )
        return (data or {}).get("data") or []

    def paper_citations(self, paper_id: str, limit: int = 10) -> list[dict]:
        data = self._get(
            f"/paper/{paper_id}/citations",
            {"fields": "title,year,authors", "limit": limit},
        )
        return (data or {}).get("data") or []


class SemanticScholarScraper:
    """Per-person collection: signals + edges, capped so one person costs 2 API calls."""

    name = "semantic_scholar"

    def __init__(self, client: SemanticScholarClient | None = None,
                 max_papers: int = 3, max_coauthors_per_paper: int = 5):
        self.client = client or SemanticScholarClient()
        self.max_papers = max_papers
        self.max_coauthors_per_paper = max_coauthors_per_paper

    @staticmethod
    def has_real_name(person: Person) -> bool:
        """Only search for people with a plausible full name — a bare GitHub
        login would resolve to random same-string authors."""
        name = (person.name or "").strip()
        if " " not in name:
            return False
        if person.github_username and name.lower() == person.github_username.lower():
            return False
        return True

    def find_author(self, name: str) -> dict | None:
        """Exact normalized-name match with a young-researcher paper count.
        Anything ambiguous or prolific is skipped — wrong attribution is worse
        than a missing signal."""
        key = normalize_name(name)
        matches = [
            a for a in self.client.search_author(name)
            if normalize_name(a.get("name", "")) == key
            and 1 <= (a.get("paperCount") or 0) <= MAX_AUTHOR_PAPERS
        ]
        # A same-name collision is not a confident identity match.
        return matches[0] if len(matches) == 1 else None

    def collect(
        self, person: Person, author: dict | None = None
    ) -> tuple[list[Signal], list[GraphEdge]]:
        """co_authored_paper signals + co_author edges for one person. Fail-soft."""
        if not self.has_real_name(person):
            return [], []
        author = author or self.find_author(person.name)
        if not author or not author.get("authorId"):
            return [], []

        today = datetime.now(timezone.utc).date().isoformat()
        person_key = normalize_name(person.name)
        signals: list[Signal] = []
        edges: list[GraphEdge] = []
        for paper in self.client.author_papers(author["authorId"], limit=self.max_papers * 2):
            if len(signals) >= self.max_papers:
                break
            title = (paper.get("title") or "").strip()
            coauthors = [
                a["name"] for a in (paper.get("authors") or [])
                if a.get("name") and normalize_name(a["name"]) != person_key
            ]
            if not title or not coauthors:
                continue  # solo papers are not co-authorship evidence
            year = paper.get("year")
            date = f"{year}-01-01" if year else today
            signals.append(
                Signal(
                    person_name=person.name, signal_type="co_authored_paper",
                    signal_category="research", signal_date=date,
                    signal_strength=0.6, source="semantic_scholar",
                    source_url=paper.get("url") or author.get("url") or "",
                    summary=f'Co-authored "{title[:80]}" ({year or "n.d."}) with {len(coauthors)} other{"s" if len(coauthors) != 1 else ""}',
                    raw_data={
                        "author_id": author["authorId"],
                        "coauthors": coauthors[:10],
                        "year": year,
                    },
                )
            )
            for coauthor in coauthors[: self.max_coauthors_per_paper]:
                edges.append(
                    GraphEdge(
                        source_name=person.name, target_name=coauthor,
                        edge_type="co_author", observed_date=date,
                        source="semantic_scholar", metadata={"paper": title[:120]},
                    )
                )
        return signals, edges

    def collect_citations(
        self, person: Person, author: dict | None = None,
        max_papers: int = 3, max_citations_per_paper: int = 5,
    ) -> tuple[list[Signal], list[GraphEdge]]:
        """paper_citation edges from authors who cite one of this person's papers,
        plus a discovery-cohort-only `cited_paper` achievement signal.
        Attention-tier: a citation is field-alignment/awareness, not a relationship.

        The `cited_paper` signal is gated to `person.cohort == "discovery"` so it
        never touches a founder's pre-breakout score — founder backtest calibration
        (the reference scale every score is normalized against) stays byte-for-byte
        unchanged, same convention as the discovery-only surface_bonus in
        ScoringEngine.connection_signal. Fail-soft, same as collect()."""
        if not self.has_real_name(person):
            return [], []
        author = author or self.find_author(person.name)
        if not author or not author.get("authorId"):
            return [], []

        person_key = normalize_name(person.name)
        signals: list[Signal] = []
        edges: list[GraphEdge] = []
        papers = [
            paper for paper in self.client.author_papers(author["authorId"], limit=max_papers * 2)
            if paper.get("paperId")
        ][:max_papers]
        for paper in papers:
            title = (paper.get("title") or "").strip()
            date = f"{paper['year']}-01-01" if paper.get("year") else datetime.now(timezone.utc).date().isoformat()
            citations = self.client.paper_citations(paper["paperId"], limit=max_citations_per_paper * 2)
            if not citations:
                continue
            for citation in citations:
                citing_paper = citation.get("citingPaper") or {}
                citers = [
                    a["name"] for a in (citing_paper.get("authors") or [])
                    if a.get("name") and normalize_name(a["name"]) != person_key
                ]
                for citer in citers[:max_citations_per_paper]:
                    edges.append(
                        GraphEdge(
                            source_name=person.name, target_name=citer,
                            edge_type="paper_citation", observed_date=date,
                            source="semantic_scholar",
                            metadata={"paper": title[:120], "citing_paper": (citing_paper.get("title") or "")[:120]},
                        )
                    )
            if person.cohort == "discovery":
                signals.append(
                    Signal(
                        person_name=person.name, signal_type="cited_paper",
                        signal_category="research", signal_date=date,
                        signal_strength=0.6, source="semantic_scholar",
                        source_url=paper.get("url") or author.get("url") or "",
                        summary=f'"{title[:80]}" cited by {len(citations)} other work{"s" if len(citations) != 1 else ""}',
                        raw_data={"paper_id": paper["paperId"], "citation_count": len(citations)},
                    )
                )
        return signals, edges
