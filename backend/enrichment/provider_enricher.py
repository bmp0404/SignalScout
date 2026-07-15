"""ProviderEnricher: licensed-provider (PDL / Coresignal) enrichment with guardrails.

Merges an EnrichmentResult into a Person (linkedin_url, location, contact_info)
and emits new scored signals — linkedin_created_recently / education_signal /
job_change — for DISCOVERY-cohort people only. Founders and controls get contact
merges at most, so backtest recall / false positives never move.

Guardrails (hard requirements):
- No provider configured -> every call is a silent no-op (keyless demo works).
- enrichment_cache: provider+person key, 30-day TTL, misses cached too —
  never re-fetch inside the TTL.
- enrichment_usage: DAILY_ENRICHMENT_BUDGET fresh lookups per UTC day;
  on exhaustion log a warning and skip, never raise.
"""

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from backend.config import Settings
from backend.db.repositories.enrichment import EnrichmentCacheRepository, EnrichmentUsageRepository
from backend.db.repositories.signals import SignalRepository
from backend.domain.person import Person
from backend.domain.signal import Signal
from backend.enrichment.providers.base import Education, EnrichmentProvider, EnrichmentQuery, EnrichmentResult, Position
from backend.enrichment.providers.coresignal import CoresignalProvider
from backend.enrichment.providers.pdl import PdlProvider

logger = logging.getLogger(__name__)

CACHE_TTL_DAYS = 30
RECENT_PROFILE_DAYS = 365       # profile_created_at inside this window = "created recently"
JOB_CHANGE_WINDOW_DAYS = 365    # a position started inside this window = fresh job change
LOW_CONNECTIONS_PROXY = 200     # PDL proxy: a real profile this sparse reads as new/young


def build_provider(settings: Settings) -> EnrichmentProvider | None:
    """ENRICHMENT_PROVIDER selector. Missing/unknown key -> None -> no-op enrichment."""
    choice = settings.enrichment_provider.strip().lower()
    if choice == "pdl":
        if settings.pdl_api_key:
            return PdlProvider(settings.pdl_api_key)
        logger.info("PDL_API_KEY not set — provider enrichment disabled")
        return None
    if choice == "coresignal":
        if settings.coresignal_api_key:
            return CoresignalProvider(settings.coresignal_api_key)
        logger.info("CORESIGNAL_API_KEY not set — provider enrichment disabled")
        return None
    logger.warning("Unknown ENRICHMENT_PROVIDER=%r — provider enrichment disabled", choice)
    return None


def _result_to_payload(result: EnrichmentResult) -> dict:
    return asdict(result)


def _result_from_payload(payload: dict) -> EnrichmentResult:
    return EnrichmentResult(
        linkedin_url=payload.get("linkedin_url"),
        headline=payload.get("headline"),
        education=[Education(**e) for e in payload.get("education", [])],
        positions=[Position(**p) for p in payload.get("positions", [])],
        profile_created_at=payload.get("profile_created_at"),
        location=payload.get("location"),
        connections=payload.get("connections"),
        raw=payload.get("raw", {}),
    )


class ProviderEnricher:
    def __init__(
        self,
        provider: EnrichmentProvider | None,
        signals: SignalRepository,
        cache: EnrichmentCacheRepository,
        usage: EnrichmentUsageRepository,
        daily_budget: int,
    ):
        self.provider = provider
        self.signals = signals
        self.cache = cache
        self.usage = usage
        self.daily_budget = daily_budget

    def enrich(self, person: Person) -> list[Signal]:
        """Fetch (or reuse cached) provider data, merge into `person` in place,
        and persist any new signals. Returns the newly-saved signals. Never raises
        into the pipeline — degraded modes all return []."""
        if self.provider is None:
            return []
        result = self._fetch(person)
        if result is None:
            return []

        self._merge_contacts(person, result)
        if person.cohort != "discovery":
            return []  # founders/controls: contact fields only, never scored signals

        new_signals = self._derive_signals(person, result)
        if new_signals:
            self.signals.save_many(new_signals)
        return new_signals

    # -- fetch with cache + budget ------------------------------------------

    def _fetch(self, person: Person) -> EnrichmentResult | None:
        now = datetime.now(timezone.utc)
        cached = self.cache.get(self.provider.name, person.id)
        if cached is not None:
            payload, fetched_at = cached
            age = now - datetime.fromisoformat(fetched_at)
            if age <= timedelta(days=CACHE_TTL_DAYS):
                return _result_from_payload(payload) if payload else None  # {} = cached miss

        today = now.date().isoformat()
        used = self.usage.count_for(today)
        if used >= self.daily_budget:
            logger.warning(
                "Daily enrichment budget exhausted (%d/%d) — skipping %s",
                used, self.daily_budget, person.name,
            )
            return None

        query = EnrichmentQuery(
            name=person.name,
            school=(person.school or "").split("(")[0].strip() or None,
            twitter_handle=person.twitter_handle,
            github_username=person.github_username,
            linkedin_url=person.linkedin_url,
        )
        result = self.provider.enrich_person(query)
        if result is None and self.provider.last_error:
            # Auth / credits / network failure — not a real miss: don't burn a
            # 30-day cache slot or a budget credit on it.
            logger.warning(
                "%s enrichment failed for %s (%s) — not cached",
                self.provider.name, person.name, self.provider.last_error,
            )
            return None
        self.usage.increment(today)
        self.cache.put(
            self.provider.name, person.id,
            _result_to_payload(result) if result else {},
            now.isoformat(timespec="seconds"),
        )
        return result

    # -- merge (idempotent, never overwrites existing values) ---------------

    def _merge_contacts(self, person: Person, result: EnrichmentResult) -> None:
        if result.linkedin_url and not person.linkedin_url:
            person.linkedin_url = result.linkedin_url
            person.contact_info["linkedin_source"] = self.provider.name
        if result.location and not person.current_location:
            person.current_location = result.location
        if result.headline:
            person.contact_info.setdefault("headline", result.headline)
        if result.connections is not None:
            person.contact_info["linkedin_connections"] = result.connections
        person.contact_info["enriched_by"] = self.provider.name

    # -- new scored signals (discovery cohort only) --------------------------

    def _derive_signals(self, person: Person, result: EnrichmentResult) -> list[Signal]:
        today = datetime.now(timezone.utc).date()
        existing_types = {
            s.signal_type for s in self.signals.for_person(person.id)
            if s.source == self.provider.name
        }
        signals: list[Signal] = []

        def emit(signal: Signal) -> None:
            if signal.signal_type not in existing_types:  # idempotent across re-runs
                signal.person_id = person.id
                signals.append(signal)

        created = self._parse(result.profile_created_at)
        if created and (today - created).days <= RECENT_PROFILE_DAYS:
            emit(Signal(
                person_name=person.name, signal_type="linkedin_created_recently",
                signal_category="network", signal_date=created.isoformat(),
                signal_strength=0.9, source=self.provider.name,
                source_url=result.linkedin_url or "",
                summary=f"LinkedIn profile first seen {created.isoformat()} — brand new",
                raw_data=result.raw,
            ))
        elif (
            result.connections is not None
            and result.connections < LOW_CONNECTIONS_PROXY
            and result.linkedin_url
        ):
            # PDL never exposes profile age; a very sparse network is the proxy.
            emit(Signal(
                person_name=person.name, signal_type="linkedin_created_recently",
                signal_category="network", signal_date=today.isoformat(),
                signal_strength=0.6, source=self.provider.name,
                source_url=result.linkedin_url,
                summary=f"LinkedIn profile with only {result.connections} connections — likely new",
                raw_data=result.raw,
            ))

        education = self._best_education(result.education)
        if education:
            is_current = education.end_date is None or (
                (end := self._parse(education.end_date)) is not None and end >= today
            )
            degree_bits = " ".join(b for b in (education.degree, education.field_of_study) if b)
            emit(Signal(
                person_name=person.name, signal_type="education_signal",
                signal_category="education",
                signal_date=education.start_date or today.isoformat(),
                signal_strength=0.7 if is_current else 0.5,
                source=self.provider.name, source_url=result.linkedin_url or "",
                summary=f"{'Studying' if is_current else 'Studied'} at {education.school}"
                        + (f" ({degree_bits})" if degree_bits else ""),
                metadata={"school": education.school, "degree": education.degree,
                          "end_date": education.end_date},
            ))

        position = self._latest_position(result.positions)
        if position and position.start_date:
            started = self._parse(position.start_date)
            if started and (today - started).days <= JOB_CHANGE_WINDOW_DAYS:
                role = " ".join(b for b in (position.title, "at" if position.company else None, position.company) if b)
                emit(Signal(
                    person_name=person.name, signal_type="job_change",
                    signal_category="career", signal_date=started.isoformat(),
                    signal_strength=0.7, source=self.provider.name,
                    source_url=result.linkedin_url or "",
                    summary=f"Recent move: {role or 'new position'} ({started.isoformat()})",
                    metadata={"company": position.company, "title": position.title},
                ))
        return signals

    @staticmethod
    def _best_education(education: list[Education]) -> Education | None:
        if not education:
            return None
        return max(education, key=lambda e: (e.end_date is None, e.start_date or ""))

    @staticmethod
    def _latest_position(positions: list[Position]) -> Position | None:
        dated = [p for p in positions if p.start_date]
        if not dated:
            return None
        return max(dated, key=lambda p: p.start_date)

    @staticmethod
    def _parse(iso: str | None):
        if not iso:
            return None
        try:
            return datetime.strptime(iso[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
