"""EnrichmentProvider contract: the typed query/result pair plus the ABC every
licensed LinkedIn-data adapter (PDL, Coresignal) implements.

Adapters are fail-soft like the scrapers: network/API errors log a warning and
return None / [] — they never raise into the pipeline. Zero linkedin.com
scraping: all data comes from licensed provider APIs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


def normalize_date(value) -> str | None:
    """Coerce provider dates ('2021', '2021-05', '2021-05-04 08:00:00', full ISO)
    to YYYY-MM-DD, padding missing month/day with 01. None if unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    date_part = value.strip().replace(" ", "T").split("T")[0]
    parts = date_part.split("-")
    if not parts[0].isdigit() or len(parts[0]) != 4:
        return None
    month = parts[1] if len(parts) > 1 and parts[1].isdigit() else "1"
    day = parts[2] if len(parts) > 2 and parts[2].isdigit() else "1"
    return f"{parts[0]}-{month.zfill(2)}-{day.zfill(2)}"


@dataclass(frozen=True)
class EnrichmentQuery:
    name: str
    school: str | None = None
    twitter_handle: str | None = None
    github_username: str | None = None
    linkedin_url: str | None = None  # strongest key when we already have one


@dataclass
class Education:
    school: str
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None  # normalized YYYY-MM-DD
    end_date: str | None = None


@dataclass
class Position:
    company: str | None = None
    title: str | None = None
    start_date: str | None = None  # normalized YYYY-MM-DD
    end_date: str | None = None
    is_current: bool = False


@dataclass
class EnrichmentResult:
    linkedin_url: str | None = None
    headline: str | None = None
    education: list[Education] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    # First-seen-in-provider-DB proxy for profile age (Coresignal created_at).
    # PDL does not expose it — always None there; connections is the proxy instead.
    profile_created_at: str | None = None
    location: str | None = None
    connections: int | None = None
    # Identity/provenance — populated for search results so the discovery lane can
    # dedupe on a stable provider key and create a real named person. On one-person
    # enrichment these may be None.
    provider: str | None = None            # provenance: "pdl" | "coresignal"
    provider_person_id: str | None = None  # stable per-provider record id
    full_name: str | None = None           # real name from the provider record
    raw: dict = field(default_factory=dict)  # slim provider payload for evidence/debugging


@dataclass
class ProviderSearchPage:
    """One resumable provider-search page.

    ``api_requests`` counts HTTP requests, while ``credit_units`` records the
    adapter's conservative billing unit. They are deliberately separate from
    returned records because providers do not bill every endpoint identically.
    """

    results: list[EnrichmentResult] = field(default_factory=list)
    next_cursor: str | None = None
    exhausted: bool = True
    api_requests: int = 0
    returned_records: int = 0
    credit_units: int = 0
    # Optional provider-billing split (Coresignal bills Search and Collect
    # separately); providers that don't distinguish leave both 0 and rely on
    # credit_units alone.
    search_credits: int = 0
    collect_credits: int = 0


class EnrichmentProvider(ABC):
    name: str = "provider"
    supported_search_filters: frozenset[str] = frozenset()
    search_credit_overhead: int = 0
    # Set by adapters on every enrich_person / search_people call: None after a
    # definitive answer (match or clean no-match, safe to cache), an error message
    # after auth / network / server failures (must NOT be cached as a 30-day miss).
    last_error: str | None = None

    @abstractmethod
    def enrich_person(self, query: EnrichmentQuery) -> EnrichmentResult | None:
        """One-person lookup. None = no confident match (or API failure)."""

    @abstractmethod
    def search_people(self, filters: dict, size: int = 10) -> list[EnrichmentResult]:
        """Allowlisted filter-based search. Results carry provider identity, real
        name, LinkedIn URL, education, positions, and location. Empty list on no
        match or failure; `size` caps returned records."""

    def search_page(
        self,
        filters: dict,
        size: int = 10,
        cursor: str | None = None,
    ) -> ProviderSearchPage:
        """Resumable search contract.

        Legacy/test adapters get a safe one-page implementation. Production
        adapters override this with their provider-appropriate cursor/offset.
        """
        if cursor:
            return ProviderSearchPage()
        results = self.search_people(filters, size=size)
        return ProviderSearchPage(
            results=results,
            exhausted=True,
            api_requests=1,
            returned_records=len(results),
            credit_units=len(results),
        )
