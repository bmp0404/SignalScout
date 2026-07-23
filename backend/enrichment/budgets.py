"""Provider-scoped budget ledger shared by the enrichment chain and provider
search. Encapsulates the search-first split so both lanes draw from one place.

Budget model (locked by the user):
- PDL: ~100 lookups/month shared. `pdl_search_split` (default 0.7) reserves that
  fraction of the monthly cap for the SEARCH lane (the lead discovery source);
  the remainder is the ENRICH lane (GitHub finds cross-corroborated via PDL).
- Coresignal: its OWN separate daily cap, shared across its independent search
  and its role as PDL's no-match fallback.
- `provider_per_run_cap` bounds fresh lookups in a single process regardless of
  provider, guarding runaway backfills.

Exhaustion is always a soft skip: `remaining(...)` returns 0 and callers log and
move on. Nothing here raises.
"""

import logging
from datetime import datetime, timezone

from backend.config import Settings
from backend.db.repositories.enrichment import EnrichmentUsageRepository

logger = logging.getLogger(__name__)

SEARCH = "search"
ENRICH = "enrich"


class ProviderBudget:
    def __init__(self, usage: EnrichmentUsageRepository, settings: Settings):
        self.usage = usage
        self.settings = settings
        self._run_spent = 0  # in-process counter for the per-run cap

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _lane_caps(self, provider: str) -> dict[str, int]:
        """Monthly (PDL) / daily (Coresignal) caps per lane for a provider."""
        if provider == "pdl":
            search_cap = int(self.settings.pdl_monthly_cap * self.settings.pdl_search_split)
            return {SEARCH: search_cap, ENRICH: self.settings.pdl_monthly_cap - search_cap}
        if provider == "coresignal":
            # One shared daily cap across both lanes.
            cap = self.settings.coresignal_daily_cap
            return {SEARCH: cap, ENRICH: cap}
        if provider == "exa":
            # Search-only lead lane with its own daily cap; no enrich lane.
            return {SEARCH: self.settings.exa_daily_cap, ENRICH: 0}
        return {SEARCH: 0, ENRICH: 0}

    def _used(self, provider: str, lane: str) -> int:
        now = self._now()
        if provider == "pdl":
            return self.usage.count_for_month(provider, now.strftime("%Y-%m"), lane)
        if provider == "coresignal":
            # Daily cap is shared across lanes, so count the whole day.
            return self.usage.count_for(provider, now.date().isoformat())
        if provider == "exa":
            return self.usage.count_for(provider, now.date().isoformat(), lane)
        return 0

    def remaining(self, provider: str, lane: str) -> int:
        """Fresh lookups still allowed for this provider+lane right now (>= 0)."""
        run_left = max(0, self.settings.provider_per_run_cap - self._run_spent)
        if run_left == 0:
            return 0
        cap = self._lane_caps(provider).get(lane, 0)
        used = self._used(provider, lane)
        return max(0, min(run_left, cap - used))

    def can_spend(self, provider: str, lane: str) -> bool:
        return self.remaining(provider, lane) > 0

    def spend(self, provider: str, lane: str, by: int = 1) -> None:
        day = self._now().date().isoformat()
        self.usage.increment(provider, lane, day, by)
        self._run_spent += by
