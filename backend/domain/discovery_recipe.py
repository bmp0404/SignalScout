"""DiscoveryRecipe: a named, scheduled, approvable provider-search query.

A thin metadata wrapper around the filter dicts ProviderExpander already
consumes. `filters` IS the filter_set passed to `_run_filter_set`; its
`_filter_identity` hash links the recipe to its run-log row in
`provider_search_checkpoints` (result counts, credits, last_outcome).
"""

from dataclasses import dataclass, field


@dataclass
class DiscoveryRecipe:
    id: str
    name: str
    provider: str  # "pdl" | "coresignal" | "exa"
    query_type: str  # "student_technical" | "founder" | "company_first" | "exa"
    filters: dict = field(default_factory=dict)
    # filter key -> lookback days; computed relative to "today" at run time and
    # merged into `filters` (e.g. {"job_start_date_gte": 30}). Never hardcode dates.
    relative_filters: dict[str, int] = field(default_factory=dict)
    default_limit: int = 25
    frequency: str = "manual"  # "weekly" | "biweekly" | "manual"
    status: str = "active"  # "active" | "paused"
    approval_state: str = "pending"  # "pending" | "approved"
    last_run: str | None = None
