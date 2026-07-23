"""ProviderEnricher: licensed-provider (PDL / Coresignal) enrichment with guardrails.

Runs an ordered provider CHAIN — PDL first, Coresignal only when PDL returns a
definitive no-match or lacks enough useful professional data. Merges an
EnrichmentResult into a Person (linkedin_url, location, contact_info) and emits
new scored signals — linkedin_created_recently / education_signal / job_change —
for DISCOVERY-cohort people only. Founders and controls get contact merges at
most, so backtest recall / false positives never move.

Guardrails (hard requirements):
- No provider configured -> every call is a silent no-op (keyless demo works).
- enrichment_cache: provider+person key, 30-day TTL, misses cached too, so a PDL
  miss never suppresses Coresignal and repeat runs make no paid calls.
- Provider-scoped budgets (ProviderBudget): on exhaustion log a warning and skip,
  never raise. Network/auth/quota failures are fail-soft and are NOT cached as
  misses, so a rotated key retries cleanly.

Signal semantics are deliberately conservative (dated, material evidence only):
- PDL exposes no profile-creation date; a sparse connection count is a labeled
  proxy for "likely new", never a known creation date.
- Coresignal `created_at` is "first seen by Coresignal", an upper bound on age.
- education_signal / job_change require a real date; metadata alone never scores.
"""

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from backend.config import Settings
from backend.db.repositories.enrichment import EnrichmentCacheRepository
from backend.db.repositories.signals import SignalRepository
from backend.domain.person import Person
from backend.domain.signal import Signal
from backend.enrichment.budgets import ENRICH, ProviderBudget
from backend.enrichment.providers.base import Education, EnrichmentProvider, EnrichmentQuery, EnrichmentResult, Position
from backend.enrichment.providers.coresignal import CoresignalProvider
from backend.enrichment.providers.exa import ExaProvider
from backend.enrichment.providers.pdl import PdlProvider

logger = logging.getLogger(__name__)

CACHE_TTL_DAYS = 30
RECENT_PROFILE_DAYS = 365       # profile_created_at inside this window = "created recently"
JOB_CHANGE_WINDOW_DAYS = 365    # a position started inside this window = fresh job change
LOW_CONNECTIONS_PROXY = 200     # PDL proxy: a real profile this sparse reads as new/young


def build_provider_chain(settings: Settings) -> list[EnrichmentProvider]:
    """Ordered enrichment chain: PDL first (primary), Coresignal fallback.

    A provider joins the chain only if its key is present; a missing key silently
    drops that provider, and an empty chain makes all enrichment a no-op.
    """
    chain: list[EnrichmentProvider] = []
    if settings.pdl_api_key:
        chain.append(PdlProvider(settings.pdl_api_key))
    else:
        logger.info("PDL_API_KEY not set — PDL enrichment disabled")
    if settings.coresignal_api_key:
        chain.append(CoresignalProvider(settings.coresignal_api_key))
    else:
        logger.info("CORESIGNAL_API_KEY not set — Coresignal fallback disabled")
    return chain


def build_provider(settings: Settings) -> EnrichmentProvider | None:
    """Back-compat single-provider selector (first available in the chain)."""
    chain = build_provider_chain(settings)
    return chain[0] if chain else None


def build_search_providers(settings: Settings) -> list[EnrichmentProvider]:
    """Providers available to the SEARCH/lead lane (ProviderExpander).

    Superset of the enrichment chain plus search-only sources like Exa. Exa is
    kept OUT of the enrichment chain (it has no one-person enrich) so it never
    consumes an enrich budget slot or caches a miss.
    """
    providers = build_provider_chain(settings)
    if settings.exa_api_key:
        providers.append(ExaProvider(settings.exa_api_key))
    else:
        logger.info("EXA_API_KEY not set — Exa search lane disabled")
    return providers


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
        provider=payload.get("provider"),
        provider_person_id=payload.get("provider_person_id"),
        full_name=payload.get("full_name"),
        raw=payload.get("raw", {}),
    )


def _is_sufficient(result: EnrichmentResult) -> bool:
    """Enough useful professional data to stop the chain (else try the fallback)."""
    return bool(result.linkedin_url or result.education or result.positions)


@dataclass
class EnrichOutcome:
    """Per-person result of a chain walk, for backfill reporting/auditing."""
    status: str = "no_provider"  # matched | miss | skipped | no_provider
    provider: str | None = None  # which provider produced the accepted match
    fresh_call: bool = False     # a paid (or, in dry-run, would-be paid) call happened
    from_cache: bool = False     # the accepted answer came from cache (no spend)
    fallback: bool = False       # matched via a non-primary provider
    new_signals: list[Signal] = field(default_factory=list)


class ProviderEnricher:
    def __init__(
        self,
        providers: list[EnrichmentProvider],
        signals: SignalRepository,
        cache: EnrichmentCacheRepository,
        budget: ProviderBudget,
    ):
        self.providers = providers
        self.signals = signals
        self.cache = cache
        self.budget = budget

    @property
    def provider(self) -> EnrichmentProvider | None:
        """First provider in the chain (kept for callers that only test presence)."""
        return self.providers[0] if self.providers else None

    def enrich(self, person: Person) -> list[Signal]:
        """Walk the provider chain and persist new discovery signals (see `run`)."""
        return self.run(person).new_signals

    def apply_result(
        self,
        person: Person,
        provider: EnrichmentProvider,
        result: EnrichmentResult,
        evidence_tier: str | None = None,
    ) -> list[Signal]:
        """Merge an already-fetched provider result (e.g. from provider search)
        into `person` and persist derived discovery signals. No provider call,
        no budget spend — the caller already paid for `result`."""
        self._merge_contacts(person, provider, result)
        if person.cohort != "discovery":
            return []
        new_signals = self._derive_signals(
            person,
            provider,
            result,
            evidence_tier=evidence_tier,
        )
        if new_signals:
            self.signals.save_many(new_signals)
        return new_signals

    def run(self, person: Person, dry_run: bool = False) -> EnrichOutcome:
        """Walk the provider chain (PDL -> Coresignal). Merge the first useful
        result into `person` in place and persist any new discovery-cohort
        signals. Returns an EnrichOutcome for reporting. Never raises into the
        pipeline — degraded modes report a benign status. In dry-run, no provider
        is ever called and no credit/cache slot is ever spent."""
        if not self.providers:
            return self._finalize(
                person,
                EnrichOutcome(status="no_provider"),
                dry_run,
            )

        outcome = EnrichOutcome(status="miss")
        matched: tuple[EnrichmentProvider, EnrichmentResult] | None = None
        for index, provider in enumerate(self.providers):
            status, result = self._fetch(provider, person, dry_run=dry_run)
            if status not in ("cache_match", "cache_miss"):
                outcome.provider = provider.name
            if status in ("match", "miss"):
                outcome.fresh_call = True  # a paid call was made this run
            if status == "error":
                outcome.status = "error"
                break  # fail-soft: stop, keep whatever we merged
            if status == "budget":
                if matched is None:
                    outcome.status = "skipped"
                break
            if status == "would_attempt":
                # dry-run: a fresh call WOULD be made here; the result is unknown.
                return self._finalize(
                    person,
                    EnrichOutcome(
                        status="attempted",
                        provider=provider.name,
                        fresh_call=True,
                        fallback=index > 0,
                    ),
                    dry_run,
                )
            if status in ("cache_miss", "miss"):
                continue  # definitive no-match — fall through to the fallback
            # match (fresh) or cache_match: a usable result
            if not dry_run:
                self._merge_contacts(person, provider, result)
            if matched is None or _is_sufficient(result):
                matched = (provider, result)
                outcome.provider = provider.name
                outcome.fallback = index > 0
                outcome.from_cache = status == "cache_match"
            if _is_sufficient(result):
                break

        if matched is None:
            return self._finalize(person, outcome, dry_run)

        outcome.status = "matched"
        provider, result = matched
        if dry_run or person.cohort != "discovery":
            return self._finalize(
                person,
                outcome,
                dry_run,
            )  # founders/controls: contact fields only, never scored signals
        new_signals = self._derive_signals(person, provider, result)
        if new_signals:
            self.signals.save_many(new_signals)
        outcome.new_signals = new_signals
        return self._finalize(person, outcome, dry_run)

    def prioritize(self, people: list[Person]) -> list[Person]:
        """Put high-scoring, GitHub-only pending candidates first."""

        def priority(person: Person) -> tuple[int, int, float, str]:
            sources = {signal.source for signal in self.signals.for_person(person.id)}
            github_only = bool(person.github_username) and sources.issubset({"github"})
            pending = person.enrichment_status in (None, "pending_budget")
            return (
                0 if github_only and pending else 1,
                0 if github_only else 1,
                -(person.score or 0),
                person.name.lower(),
            )

        return sorted(people, key=priority)

    def pending_github_count(self, people: list[Person]) -> int:
        return sum(
            1
            for person in people
            if person.github_username
            and person.enrichment_status in (None, "pending_budget")
            and {
                signal.source
                for signal in self.signals.for_person(person.id)
            }.issubset({"github"})
        )

    @staticmethod
    def _finalize(
        person: Person,
        outcome: EnrichOutcome,
        dry_run: bool,
    ) -> EnrichOutcome:
        if dry_run or person.cohort != "discovery" or not person.github_username:
            return outcome
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if outcome.status == "matched":
            person.enrichment_status = "provider_enriched"
            person.enrichment_provider = outcome.provider
        elif outcome.status == "miss":
            person.enrichment_status = "provider_no_match"
            person.enrichment_provider = outcome.provider
        elif outcome.status == "error":
            person.enrichment_status = "provider_error"
            person.enrichment_provider = outcome.provider
        elif outcome.status in ("skipped", "no_provider"):
            person.enrichment_status = "pending_budget"
        else:
            return outcome
        person.enrichment_updated_at = now
        return outcome

    # -- fetch with provider-scoped cache + budget --------------------------

    def _fetch(
        self, provider: EnrichmentProvider, person: Person, dry_run: bool = False
    ) -> tuple[str, EnrichmentResult | None]:
        """Returns (status, result). Statuses:
        cache_match / cache_miss (served from cache, no spend),
        match / miss (fresh definitive answer, spent + cached),
        would_attempt (dry-run: a fresh call would happen here),
        error / budget (fail-soft; never spent or cached)."""
        now = datetime.now(timezone.utc)
        cached = self.cache.get(provider.name, person.id)
        if cached is not None:
            payload, fetched_at = cached
            age = now - datetime.fromisoformat(fetched_at)
            if age <= timedelta(days=CACHE_TTL_DAYS):
                if payload:
                    return "cache_match", _result_from_payload(payload)
                return "cache_miss", None  # {} = cached miss, authoritative inside TTL

        if not self.budget.can_spend(provider.name, ENRICH):
            logger.warning(
                "%s enrich budget exhausted — skipping %s", provider.name, person.name
            )
            return "budget", None

        if dry_run:
            return "would_attempt", None  # never call the provider or spend a credit

        query = EnrichmentQuery(
            name=person.name,
            school=(person.school or "").split("(")[0].strip() or None,
            twitter_handle=person.twitter_handle,
            github_username=person.github_username,
            linkedin_url=person.linkedin_url,
        )
        result = provider.enrich_person(query)
        if result is None and provider.last_error:
            # Auth / credits / network failure — not a real miss: don't burn a
            # 30-day cache slot or a budget credit on it.
            logger.warning(
                "%s enrichment failed for %s (%s) — not cached",
                provider.name, person.name, provider.last_error,
            )
            return "error", None
        self.budget.spend(provider.name, ENRICH)
        self.cache.put(
            provider.name, person.id,
            _result_to_payload(result) if result else {},
            now.isoformat(timespec="seconds"),
        )
        if result is None:
            return "miss", None
        return "match", result

    # -- merge (idempotent, never overwrites existing values) ---------------

    def _merge_contacts(self, person: Person, provider: EnrichmentProvider, result: EnrichmentResult) -> None:
        if result.linkedin_url and not person.linkedin_url:
            person.linkedin_url = result.linkedin_url
            person.contact_info["linkedin_source"] = provider.name
        if result.location and not person.current_location:
            person.current_location = result.location
        if result.headline:
            person.contact_info.setdefault("headline", result.headline)
        if result.connections is not None:
            person.contact_info["linkedin_connections"] = result.connections
        person.contact_info["enriched_by"] = provider.name

    # -- new scored signals (discovery cohort only) --------------------------

    def _derive_signals(
        self,
        person: Person,
        provider: EnrichmentProvider,
        result: EnrichmentResult,
        evidence_tier: str | None = None,
    ) -> list[Signal]:
        today = datetime.now(timezone.utc).date()
        existing_types = {
            s.signal_type for s in self.signals.for_person(person.id)
            if s.source == provider.name
        }
        signals: list[Signal] = []

        def emit(signal: Signal) -> None:
            if signal.signal_type not in existing_types:  # idempotent across re-runs
                signal.person_id = person.id
                signals.append(signal)

        if result.raw.get("source") == "exa" and (result.headline or result.raw.get("summary")):
            # Exa is a semantic web match: anchor the person with a modest,
            # human-reviewable web-presence signal regardless of tier, so a
            # lead with sparse structured data still surfaces for review.
            emit(Signal(
                person_name=person.name, signal_type="web_presence",
                signal_category="web", signal_date=today.isoformat(),
                signal_strength=0.6, source=provider.name,
                source_url=result.raw.get("url") or result.linkedin_url or "",
                summary=(result.headline or result.raw.get("summary") or "")[:300],
                raw_data=result.raw,
                metadata={"evidence": "exa_web_match", "provider": provider.name},
            ))

        created = self._parse(result.profile_created_at)
        if (
            evidence_tier != "review"
            and created
            and 0 <= (today - created).days <= RECENT_PROFILE_DAYS
        ):
            # Coresignal only: "first seen by Coresignal", an upper bound on age.
            emit(Signal(
                person_name=person.name, signal_type="linkedin_created_recently",
                signal_category="network", signal_date=created.isoformat(),
                signal_strength=0.7, source=provider.name,
                source_url=result.linkedin_url or "",
                summary=f"Profile first seen by {provider.name.title()} {created.isoformat()} "
                        f"(first-seen date, not a known LinkedIn creation date)",
                raw_data=result.raw,
                metadata={"evidence": "provider_first_seen", "provider": provider.name},
            ))
        elif (
            evidence_tier != "review"
            and result.profile_created_at is None
            and result.connections is not None
            and result.connections < LOW_CONNECTIONS_PROXY
            and result.linkedin_url
        ):
            # PDL never exposes profile age; a very sparse network is a labeled
            # PROXY for "likely new" — never presented as a known creation date.
            emit(Signal(
                person_name=person.name, signal_type="linkedin_created_recently",
                signal_category="network", signal_date=today.isoformat(),
                signal_strength=0.5, source=provider.name,
                source_url=result.linkedin_url,
                summary=f"Sparse LinkedIn network ({result.connections} connections) — "
                        f"proxy for a likely-new profile, not a known creation date",
                raw_data=result.raw,
                metadata={"evidence": "sparse_connections_proxy", "connections": result.connections},
            ))

        education = self._best_education(result.education)
        if education and (education.start_date or education.end_date):
            # Dated, material evidence only — metadata alone never scores.
            is_current = education.end_date is None or (
                (end := self._parse(education.end_date)) is not None and end >= today
            )
            degree_bits = " ".join(b for b in (education.degree, education.field_of_study) if b)
            emit(Signal(
                person_name=person.name, signal_type="education_signal",
                signal_category="education",
                signal_date=education.start_date or education.end_date,
                signal_strength=0.7 if is_current else 0.5,
                source=provider.name, source_url=result.linkedin_url or "",
                summary=f"{'Studying' if is_current else 'Studied'} at {education.school}"
                        + (f" ({degree_bits})" if degree_bits else ""),
                metadata={"school": education.school, "degree": education.degree,
                          "end_date": education.end_date, "evidence": "dated_education"},
            ))

        position = self._latest_position(result.positions)
        if evidence_tier != "review" and position and position.start_date:
            started = self._parse(position.start_date)
            if started and 0 <= (today - started).days <= JOB_CHANGE_WINDOW_DAYS:
                role = " ".join(b for b in (position.title, "at" if position.company else None, position.company) if b)
                emit(Signal(
                    person_name=person.name, signal_type="job_change",
                    signal_category="career", signal_date=started.isoformat(),
                    signal_strength=0.7, source=provider.name,
                    source_url=result.linkedin_url or "",
                    summary=f"Recent move: {role or 'new position'} ({started.isoformat()})",
                    metadata={"company": position.company, "title": position.title,
                              "evidence": "dated_position"},
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
