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
    raw: dict = field(default_factory=dict)  # slim provider payload for evidence/debugging


class EnrichmentProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def enrich_person(self, query: EnrichmentQuery) -> EnrichmentResult | None:
        """One-person lookup. None = no confident match (or API failure)."""

    @abstractmethod
    def search_people(self, filters: dict) -> list[EnrichmentResult]:
        """Filter-based search. Empty list on no match or failure."""
