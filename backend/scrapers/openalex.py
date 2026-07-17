"""OpenAlex co-author scraper (free graph API, no key required; a `mailto`
param is honored for the polite pool, giving priority routing/higher limits).

Author search by name -> works -> co-authors, mirroring
`backend/scrapers/semantic_scholar.py`. Emits `co_authored_paper` signals and
`co_author` graph edges for discovery-cohort people with real names. All
failures (rate limiting included) fail soft — never fatal.
"""

import logging
import time
from datetime import datetime, timezone

import requests

from backend.discovery.entity_resolution import normalize_name
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.domain.signal import Signal
from backend.scrapers.semantic_scholar import SemanticScholarScraper

logger = logging.getLogger(__name__)

API = "https://api.openalex.org"

# Above this many works, or this many citations, the author reads as an
# established researcher rather than the pre-breakout person we're after.
MAX_AUTHOR_WORKS = 30
MAX_AUTHOR_CITED_BY = 500


class OpenAlexClient:
    """Thin wrapper. All failures (including persistent 429s) return None/[]."""

    def __init__(self, mailto: str = "", max_retries: int = 3, backoff_seconds: float = 2.0):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "signal-scout/0.1 (research signal discovery)"})
        self.mailto = mailto
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def _get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        if self.mailto:
            params.setdefault("mailto", self.mailto)
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(f"{API}{path}", params=params, timeout=15)
            except requests.RequestException as exc:
                logger.warning("openalex request failed %s: %s", path, exc)
                return None
            if resp.status_code == 429:
                wait = self.backoff_seconds * (attempt + 1)
                logger.warning("openalex 429 on %s — backing off %.1fs", path, wait)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                logger.warning("openalex %s -> %s", path, resp.status_code)
                return None
            return resp.json()
        logger.warning("openalex still rate-limited on %s — giving up", path)
        return None

    def search_author(self, name: str) -> list[dict]:
        data = self._get(
            "/authors",
            {"search": name, "select": "id,display_name,works_count,cited_by_count", "per_page": 10},
        )
        return (data or {}).get("results") or []

    def author(self, author_id: str) -> dict | None:
        return self._get(
            f"/authors/{author_id}",
            {"select": "id,display_name,works_count,cited_by_count,summary_stats,last_known_institutions"},
        )

    def author_works(self, author_id: str, limit: int = 10) -> list[dict]:
        data = self._get(
            "/works",
            {
                "filter": f"authorships.author.id:{author_id}",
                "select": "id,title,publication_year,publication_date,authorships",
                "per_page": limit,
            },
        )
        return (data or {}).get("results") or []

    def works_by_affiliation(
        self, affiliation: str, from_date: str | None = None,
        limit: int = 25, institution_id: str | None = None,
    ) -> list[dict]:
        """Recent works matching a lab. Prefers a precise `institution_id`
        (OpenAlex has resolved some labs, e.g. MIT CSAIL, as their own
        institution) and falls back to a free-text affiliation-string search
        (works for labs OpenAlex hasn't resolved, e.g. Stanford SAIL/Berkeley
        BAIR) — no HTML scraping of lab pages either way."""
        filters = [f"institutions.id:{institution_id}"] if institution_id else [
            f"raw_affiliation_strings.search:{affiliation}"
        ]
        if from_date:
            filters.append(f"from_publication_date:{from_date}")
        data = self._get(
            "/works",
            {
                "filter": ",".join(filters),
                "select": "id,title,publication_year,publication_date,authorships",
                "per_page": limit,
            },
        )
        return (data or {}).get("results") or []


class OpenAlexScraper:
    """Per-person collection: signals + edges, capped so one person costs 2 API calls."""

    name = "openalex"

    def __init__(self, client: OpenAlexClient | None = None,
                 max_papers: int = 3, max_coauthors_per_paper: int = 5):
        self.client = client or OpenAlexClient()
        self.max_papers = max_papers
        self.max_coauthors_per_paper = max_coauthors_per_paper

    # Reuse the name-plausibility gate — a bare username shouldn't trigger an
    # author search, same reasoning as the Semantic Scholar scraper.
    has_real_name = staticmethod(SemanticScholarScraper.has_real_name)

    @staticmethod
    def is_early_career(author: dict) -> bool:
        works = author.get("works_count") or 0
        cited_by = author.get("cited_by_count") or 0
        return 1 <= works <= MAX_AUTHOR_WORKS and cited_by <= MAX_AUTHOR_CITED_BY

    def find_author(self, name: str) -> dict | None:
        """Exact normalized-name match with an early-career works count.
        Anything ambiguous or prolific is skipped — wrong attribution is worse
        than a missing signal."""
        key = normalize_name(name)
        matches = [
            a for a in self.client.search_author(name)
            if normalize_name(a.get("display_name", "")) == key and self.is_early_career(a)
        ]
        return matches[0] if len(matches) == 1 else None

    def collect(
        self, person: Person, author: dict | None = None
    ) -> tuple[list[Signal], list[GraphEdge]]:
        """co_authored_paper signals + co_author edges for one person. Fail-soft."""
        if not self.has_real_name(person):
            return [], []
        author = author or self.find_author(person.name)
        if not author or not author.get("id"):
            return [], []

        today = datetime.now(timezone.utc).date().isoformat()
        person_key = normalize_name(person.name)
        signals: list[Signal] = []
        edges: list[GraphEdge] = []
        for work in self.client.author_works(author["id"], limit=self.max_papers * 2):
            if len(signals) >= self.max_papers:
                break
            title = (work.get("title") or "").strip()
            coauthors = [
                a["author"]["display_name"]
                for a in (work.get("authorships") or [])
                if a.get("author", {}).get("display_name")
                and normalize_name(a["author"]["display_name"]) != person_key
            ]
            if not title or not coauthors:
                continue  # solo works are not co-authorship evidence
            year = work.get("publication_year")
            date = work.get("publication_date") or (f"{year}-01-01" if year else today)
            signals.append(
                Signal(
                    person_name=person.name, signal_type="co_authored_paper",
                    signal_category="research", signal_date=date,
                    signal_strength=0.6, source="openalex",
                    source_url=work.get("id") or "",
                    summary=f'Co-authored "{title[:80]}" ({year or "n.d."}) with {len(coauthors)} other{"s" if len(coauthors) != 1 else ""}',
                    raw_data={
                        "author_id": author["id"],
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
                        source="openalex", metadata={"paper": title[:120]},
                    )
                )
        return signals, edges
