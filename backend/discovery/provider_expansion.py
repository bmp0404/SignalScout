"""ProviderExpander: the independent, LEAD discovery lane.

Queries licensed provider SEARCH APIs (PDL primary, Coresignal independent) for
target cohorts — current/recent students and early-career builders at top
technical programs — and creates real `discovery` people who need NO GitHub
account. Every candidate must clear a confidence + evidence gate, and dedupe runs
in a strict ladder so PDL/Coresignal never create duplicate people:

    1. provider identity  (provider, provider_person_id)
    2. canonical LinkedIn URL
    3. normalized name + school

Budgeted by the SEARCH lane of ProviderBudget. Fail-soft throughout: provider
errors log a warning and skip; nothing here raises into the pipeline.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.provider_identities import (
    ProviderIdentityRepository,
    ProviderSearchCheckpoint,
    canonical_linkedin,
)
from backend.discovery.entity_resolution import normalize_name
from backend.domain.discovery_recipe import DiscoveryRecipe
from backend.enrichment.budgets import SEARCH, ProviderBudget
from backend.enrichment.provider_enricher import ProviderEnricher
from backend.enrichment.providers.base import Education, EnrichmentProvider, EnrichmentResult, Position
from backend.domain.person import Person

logger = logging.getLogger(__name__)

RECENT_EDUCATION_DAYS = 1095      # graduated within ~3 years still counts as recent
CURRENT_EDUCATION_HORIZON = 1825  # started within ~5 years (still enrolled) counts
RECENT_MOVEMENT_DAYS = 365
FOUNDER_TITLE_TERMS = ("founder", "co-founder", "cofounder", "ceo", "chief executive")
TECHNICAL_EDUCATION_TERMS = (
    "computer",
    "software",
    "engineering",
    "mathemat",
    "physics",
    "robot",
    "artificial intelligence",
    "machine learning",
    "data science",
    "informatics",
    "cyber",
)


@dataclass
class ProviderExpansionResult:
    created: list[Person] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)  # provider -> new people
    merged: int = 0
    rejected: int = 0
    attempted: int = 0  # provider search queries issued (or, in dry-run, that would run)
    requested_pages: int = 0
    api_requests: int = 0
    returned_records: int = 0
    credit_units: int = 0
    verified: int = 0
    review: int = 0
    duplicates: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    planned_pages: list[dict] = field(default_factory=list)


class ProviderExpander:
    def __init__(
        self,
        providers: list[EnrichmentProvider],
        persons: PersonRepository,
        identities: ProviderIdentityRepository,
        enricher: ProviderEnricher,
        budget: ProviderBudget,
        filters_file: Path,
    ):
        self.providers = providers
        self.persons = persons
        self.identities = identities
        self.enricher = enricher
        self.budget = budget
        self.filters_file = filters_file

    def expand(
        self,
        dry_run: bool = False,
        on_progress: Callable[[str, int], None] | None = None,
    ) -> ProviderExpansionResult:
        result = ProviderExpansionResult(source_counts={p.name: 0 for p in self.providers})
        if not self.providers:
            return result
        config = self._load_config()
        if not config:
            return result
        cap_per_filter = int(config.get("max_results_per_filter", 10))
        pages_per_filter = max(1, int(config.get("pages_per_filter_per_run", 1)))
        max_new = int(config.get("max_new_people_per_run", 25))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        today = now[:10]
        dry_remaining = {
            provider.name: self.budget.remaining(provider.name, SEARCH)
            for provider in self.providers
        }
        dry_reserved = 0

        for provider in self.providers:
            filter_sets = config.get(f"{provider.name}_filters", [])
            for filter_set in filter_sets:
                if len(result.created) >= max_new or (
                    dry_run and dry_reserved >= max_new
                ):
                    return result
                dry_reserved, stop = self._run_filter_set(
                    provider,
                    filter_set,
                    result,
                    dry_run=dry_run,
                    max_new=max_new,
                    cap_per_filter=cap_per_filter,
                    pages_per_filter=pages_per_filter,
                    dry_remaining=dry_remaining,
                    dry_reserved=dry_reserved,
                    now=now,
                    today=today,
                    on_progress=on_progress,
                )
                if stop:
                    break
        return result

    def run_recipe(
        self,
        recipe: DiscoveryRecipe,
        dry_run: bool = False,
        override_limit: int | None = None,
    ) -> ProviderExpansionResult:
        """Run one named recipe through the SAME engine `expand()` uses: same
        dedupe ladder, same ProviderBudget ledger, same checkpoint run-log. A
        recipe must be `approved` before its first non-dry run; dry runs are
        always allowed so operators can preview before approving."""
        result = ProviderExpansionResult(source_counts={recipe.provider: 0})
        if not dry_run and recipe.approval_state != "approved":
            raise PermissionError(f"recipe {recipe.id!r} requires approval before a real run")
        provider = self._provider_by_name(recipe.provider)
        if provider is None:
            return result
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        today = now[:10]
        limit = recipe.default_limit
        if override_limit is not None:
            limit = min(limit, override_limit)
        if recipe.query_type == "company_first":
            return self._run_company_first(provider, recipe, result, dry_run=dry_run, limit=limit)
        filter_set = {**self._with_relative_filters(recipe), "label": recipe.name}
        dry_remaining = {recipe.provider: self.budget.remaining(recipe.provider, SEARCH)}
        self._run_filter_set(
            provider,
            filter_set,
            result,
            dry_run=dry_run,
            max_new=limit,
            cap_per_filter=limit,
            pages_per_filter=1,
            dry_remaining=dry_remaining,
            dry_reserved=0,
            now=now,
            today=today,
            on_progress=None,
            admission=recipe.query_type,
        )
        return result

    def _run_company_first(
        self,
        provider: EnrichmentProvider,
        recipe: DiscoveryRecipe,
        result: ProviderExpansionResult,
        *,
        dry_run: bool,
        limit: int,
    ) -> ProviderExpansionResult:
        """Company-first discovery: find seed-stage companies, then search each
        for founder-titled employees. Simpler run-log than `_run_filter_set`
        (one checkpoint update per run, no cross-run pagination) — company-first
        result sets are small and cheap to re-run. `recipe.filters` here is
        `{"company": {...company_base filters...}, "employee_title": {...}}`."""
        if not hasattr(provider, "search_companies"):
            return result
        company_filters = recipe.filters.get("company", {})
        employee_filters = recipe.filters.get("employee_title", {})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        today = now[:10]
        remaining = self.budget.remaining(provider.name, SEARCH)
        if remaining <= 0:
            return result
        companies = provider.search_companies(company_filters, size=min(5, max(1, limit)))
        result.attempted = len(companies)
        if dry_run:
            return result
        outcomes: dict[str, int] = {}
        reasons: dict[str, int] = {}
        search_credits = 0
        collect_credits = 1  # company collects already spent inside search_companies
        for company in companies:
            if len(result.created) >= limit:
                break
            if self.budget.remaining(provider.name, SEARCH) <= 0:
                break
            company_id = company.get("id")
            if company_id is None:
                continue
            employees = provider.search_company_employees(
                company_id, employee_filters, size=limit - len(result.created)
            )
            search_credits += 1
            collect_credits += len(employees)
            result.returned_records += len(employees)
            for record in employees:
                status, person, reason = self._ingest(provider, record, today, admission="founder")
                outcomes[status] = outcomes.get(status, 0) + 1
                if status in ("verified", "review"):
                    result.created.append(person)
                    result.source_counts[provider.name] += 1
                    if status == "verified":
                        result.verified += 1
                    else:
                        result.review += 1
                elif status == "merged":
                    result.merged += 1
                elif status == "duplicate":
                    result.duplicates += 1
                else:
                    result.rejected += 1
                    reasons[reason] = reasons.get(reason, 0) + 1
                    result.rejection_reasons[reason] = result.rejection_reasons.get(reason, 0) + 1
        credit_units = search_credits + collect_credits
        if credit_units:
            self.budget.spend(provider.name, SEARCH, by=credit_units)
        result.credit_units += credit_units
        filter_identity = self._filter_identity(recipe.filters)
        checkpoint = self.identities.ensure_checkpoint(provider.name, filter_identity, recipe.filters, now)
        self.identities.record_search_page(
            checkpoint,
            next_cursor=None,
            exhausted=True,
            api_requests=result.attempted,
            returned_records=result.returned_records,
            credit_units=credit_units,
            outcomes=outcomes,
            rejection_reasons=reasons,
            last_outcome="completed",
            updated_at=now,
            search_credit_units=search_credits,
            collect_credit_units=collect_credits,
        )
        return result

    def recipe_checkpoint(self, recipe: DiscoveryRecipe) -> ProviderSearchCheckpoint | None:
        """Best-effort run-log lookup for a recipe's current filter identity —
        used by the recipe service for status/cost reporting. Relative-date
        filters mean this reflects the *most recent* run's identity, not
        necessarily every historical run."""
        provider = self._provider_by_name(recipe.provider)
        if provider is None:
            return None
        if recipe.query_type == "company_first":
            if not recipe.filters:
                return None
            return self.identities.checkpoint(
                recipe.provider, self._filter_identity(recipe.filters)
            )
        filters = self._effective_filters(
            provider, {**self._with_relative_filters(recipe), "label": recipe.name}
        )
        if not filters:
            return None
        return self.identities.checkpoint(recipe.provider, self._filter_identity(filters))

    def _provider_by_name(self, name: str) -> EnrichmentProvider | None:
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    @staticmethod
    def _with_relative_filters(recipe: DiscoveryRecipe) -> dict:
        """Merge in date filters computed relative to "today" at run time —
        recipes never store an absolute date."""
        filters = dict(recipe.filters)
        today = datetime.now(timezone.utc).date()
        for key, lookback_days in recipe.relative_filters.items():
            filters[key] = (today - timedelta(days=lookback_days)).isoformat()
        return filters

    def _run_filter_set(
        self,
        provider: EnrichmentProvider,
        filter_set: dict,
        result: ProviderExpansionResult,
        *,
        dry_run: bool,
        max_new: int,
        cap_per_filter: int,
        pages_per_filter: int,
        dry_remaining: dict[str, int],
        dry_reserved: int,
        now: str,
        today: str,
        on_progress: Callable[[str, int], None] | None,
        admission: str = "student_technical",
    ) -> tuple[int, bool]:
        """Run one filter_set through checkpoint + budget + paging + ingest,
        mutating `result` in place. Returns (updated dry_reserved, stop) where
        `stop` tells the caller to stop issuing further filter_sets for this
        provider (budget/capacity exhausted)."""
        filters = self._effective_filters(provider, filter_set)
        if not filters:
            return dry_reserved, False
        filter_identity = self._filter_identity(filters)
        checkpoint = self.identities.checkpoint(provider.name, filter_identity)
        if checkpoint and checkpoint.exhausted:
            return dry_reserved, False
        checkpoint = checkpoint or ProviderSearchCheckpoint(
            provider=provider.name,
            filter_identity=filter_identity,
            filters=filters,
            updated_at=now,
        )
        remaining = (
            dry_remaining[provider.name]
            if dry_run
            else self.budget.remaining(provider.name, SEARCH)
        )
        if remaining <= 0:
            if not dry_run:
                logger.warning(
                    "%s search budget exhausted — stopping search lane",
                    provider.name,
                )
            return dry_reserved, True
        run_capacity = (
            max_new - dry_reserved
            if dry_run
            else max_new - len(result.created)
        )
        record_budget = max(
            0,
            remaining - provider.search_credit_overhead,
        )
        size = min(cap_per_filter, record_budget, run_capacity)
        if size <= 0:
            return dry_reserved, True
        result.planned_pages.append({
            "provider": provider.name,
            "label": filter_set.get("label", filter_identity[:8]),
            "filter_identity": filter_identity,
            "filters": filters,
            "next_page": checkpoint.next_page,
            "cursor": checkpoint.cursor,
            "size": size,
        })
        if dry_run:
            result.attempted += 1
            dry_remaining[provider.name] -= (
                size + provider.search_credit_overhead
            )
            dry_reserved += size
            return dry_reserved, False  # never call the provider, write, or spend a credit
        for _ in range(pages_per_filter):
            if checkpoint.exhausted or len(result.created) >= max_new:
                break
            page_size = min(
                size,
                max(
                    0,
                    self.budget.remaining(provider.name, SEARCH)
                    - provider.search_credit_overhead,
                ),
                max_new - len(result.created),
            )
            if page_size <= 0:
                break
            result.attempted += 1
            result.requested_pages += 1
            page = provider.search_page(
                filters,
                size=page_size,
                cursor=checkpoint.cursor,
            )
            result.api_requests += page.api_requests
            result.returned_records += page.returned_records
            if provider.last_error:
                logger.warning(
                    "%s search failed (%s) — checkpoint not advanced",
                    provider.name,
                    provider.last_error,
                )
                checkpoint = self.identities.record_search_page(
                    checkpoint,
                    next_cursor=checkpoint.cursor,
                    exhausted=False,
                    api_requests=page.api_requests,
                    returned_records=page.returned_records,
                    credit_units=0,
                    outcomes={},
                    rejection_reasons={},
                    last_outcome=f"error:{provider.last_error}",
                    updated_at=now,
                    advance=False,
                )
                break
            outcomes: dict[str, int] = {}
            page_reasons: dict[str, int] = {}
            for record in page.results:
                status, person, reason = self._ingest(provider, record, today, admission=admission)
                outcomes[status] = outcomes.get(status, 0) + 1
                if status in ("verified", "review"):
                    result.created.append(person)
                    result.source_counts[provider.name] += 1
                    if status == "verified":
                        result.verified += 1
                    else:
                        result.review += 1
                    if on_progress:
                        on_progress(provider.name, result.source_counts[provider.name])
                elif status == "merged":
                    result.merged += 1
                elif status == "duplicate":
                    result.duplicates += 1
                else:
                    result.rejected += 1
                    page_reasons[reason] = page_reasons.get(reason, 0) + 1
                    result.rejection_reasons[reason] = (
                        result.rejection_reasons.get(reason, 0) + 1
                    )
            if page.credit_units:
                self.budget.spend(
                    provider.name,
                    SEARCH,
                    by=page.credit_units,
                )
            result.credit_units += page.credit_units
            checkpoint = self.identities.record_search_page(
                checkpoint,
                next_cursor=page.next_cursor,
                exhausted=page.exhausted,
                api_requests=page.api_requests,
                returned_records=page.returned_records,
                credit_units=page.credit_units,
                outcomes=outcomes,
                rejection_reasons=page_reasons,
                last_outcome="completed",
                updated_at=now,
                search_credit_units=page.search_credits,
                collect_credit_units=page.collect_credits,
            )
        return dry_reserved, False

    def _load_config(self) -> dict:
        try:
            return json.loads(Path(self.filters_file).read_text())
        except (OSError, ValueError) as exc:
            logger.warning("provider discovery filters unavailable (%s) — search lane idle", exc)
            return {}

    # -- ingest one search record -------------------------------------------

    def _ingest(
        self,
        provider: EnrichmentProvider,
        record: EnrichmentResult,
        today: str,
        admission: str = "student_technical",
    ) -> tuple[str, Person | None, str]:
        if not self._is_confident(record):
            return "rejected", None, "ambiguous_identity"
        if admission == "founder":
            tier, rejection_reason = self._founder_tier(record)
            education = _best_education(record.education)
        else:
            tier, rejection_reason = self._evidence_tier(record)
            education = _best_recent_technical_education(record.education)
        if tier is None:
            return "rejected", None, rejection_reason
        evidence_record = replace(record, education=[education] if education else [])

        existing, duplicate_kind = self._resolve_existing(provider, record)
        if existing is not None:
            self.enricher.apply_result(
                existing,
                provider,
                evidence_record,
                evidence_tier=tier,
            )
            if existing.discovery_origin == "provider_search":
                if existing.evidence_tier != "verified":
                    existing.evidence_tier = tier
                existing.review_required = existing.evidence_tier == "review"
                existing.needs_review = existing.review_required
                if not existing.discovery_source:
                    existing.discovery_source = f"{provider.name}_discovery"
            elif existing.github_username:
                existing.enrichment_status = "provider_enriched"
                existing.enrichment_provider = provider.name
                existing.enrichment_updated_at = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )
            self.persons.save(existing)
            self._link(provider, record, existing.id, today)
            return duplicate_kind, existing, duplicate_kind

        person = Person(
            name=record.full_name.strip(),
            cohort="discovery",
            discovery_origin="provider_search",
            discovery_source=f"{provider.name}_discovery",
            evidence_tier=tier,
            review_required=tier == "review",
            needs_review=tier == "review",
        )
        person.linkedin_url = record.linkedin_url
        if record.location:
            person.current_location = record.location
        if education:
            person.school = education.school
            grad = _year(education.end_date)
            if grad:
                person.graduation_year = grad
        person.contact_info["discovered_via"] = provider.name
        person.contact_info["discovery_lane"] = "provider_search"
        self.persons.save(person)
        self._link(provider, record, person.id, today)
        self.enricher.apply_result(
            person,
            provider,
            evidence_record,
            evidence_tier=tier,
        )
        self.persons.save(person)
        logger.info(
            "provider-search discovered %s via %s (%s)",
            person.name,
            provider.name,
            tier,
        )
        return tier, person, tier

    def _resolve_existing(
        self,
        provider: EnrichmentProvider,
        record: EnrichmentResult,
    ) -> tuple[Person | None, str]:
        pid = record.provider_person_id or canonical_linkedin(record.linkedin_url)
        if pid:
            person_id = self.identities.find_person_by_provider_id(provider.name, pid)
            if person_id:
                found = self.persons.get(person_id)
                if found:
                    return found, "duplicate"
        person_id = self.identities.find_person_by_linkedin(record.linkedin_url)
        if person_id:
            found = self.persons.get(person_id)
            if found:
                return found, "duplicate"
        key = normalize_name(record.full_name)
        school = _best_recent_technical_education(record.education)
        school_name = school.school.lower() if school and school.school else None
        for existing in self.persons.all():
            if normalize_name(existing.name) != key:
                continue
            if school_name and existing.school and school_name in existing.school.lower():
                return existing, "merged"
            if not school_name and not existing.school:
                return existing, "merged"
        return None, ""

    def _link(self, provider: EnrichmentProvider, record: EnrichmentResult, person_id: str, today: str) -> None:
        pid = record.provider_person_id or canonical_linkedin(record.linkedin_url)
        if not pid:
            return
        self.identities.link(provider.name, pid, person_id, record.linkedin_url, today)

    # -- gates ---------------------------------------------------------------

    @staticmethod
    def _is_confident(record: EnrichmentResult) -> bool:
        """Reject ambiguous/low-confidence records: need a real (multi-token) name
        and either canonical LinkedIn or a stable provider ID to anchor it."""
        if not record.full_name or len(record.full_name.split()) < 2:
            return False
        return bool(canonical_linkedin(record.linkedin_url) or record.provider_person_id)

    @staticmethod
    def _evidence_tier(record: EnrichmentResult) -> tuple[str | None, str]:
        """Admit only dated technical education.

        Verified additionally requires dated movement or a dated provider
        first-seen value. Review candidates remain real identities but receive
        education evidence only.
        """
        technical = [
            education
            for education in record.education
            if _technical_education(education)
        ]
        if not technical:
            return None, "nontechnical_or_missing_education"
        if not _recent_education(technical):
            return None, "undated_or_stale_education"
        if _recent_movement(record):
            return "verified", ""
        return "review", ""

    @staticmethod
    def _founder_tier(record: EnrichmentResult) -> tuple[str | None, str]:
        """Founder-recipe admission: no technical-education requirement, just a
        founder/co-founder/CEO title. Dated/recent movement (or provider
        first-seen) still gates verified vs review, same as `_evidence_tier`."""
        if not any(_is_founder_position(position) for position in record.positions):
            return None, "no_founder_title"
        if _recent_movement(record):
            return "verified", ""
        return "review", ""

    @staticmethod
    def _effective_filters(
        provider: EnrichmentProvider,
        filter_set: dict,
    ) -> dict:
        requested = {key: value for key, value in filter_set.items() if key != "label"}
        supported = provider.supported_search_filters
        if not supported:
            return requested
        return {key: value for key, value in requested.items() if key in supported}

    @staticmethod
    def _filter_identity(filters: dict) -> str:
        canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _best_education(education: list[Education]) -> Education | None:
    if not education:
        return None
    return max(education, key=lambda e: (e.end_date is None, e.start_date or ""))


def _recent_education(education: list[Education]) -> bool:
    return any(_education_is_recent(item) for item in education)


def _education_is_recent(education: Education) -> bool:
    today = datetime.now(timezone.utc).date()
    if not education.start_date and not education.end_date:
        return False
    end = _date(education.end_date)
    if education.end_date is None or (
        end and (end - today).days >= -RECENT_EDUCATION_DAYS
    ):
        # still enrolled, or graduated within the recent window
        start = _date(education.start_date)
        if education.end_date is None and (
            not start
            or not 0 <= (today - start).days <= CURRENT_EDUCATION_HORIZON
        ):
            return False  # "current" but started too long ago to be a student
        return True
    return False


def _best_recent_technical_education(
    education: list[Education],
) -> Education | None:
    eligible = [
        item
        for item in education
        if _technical_education(item) and _education_is_recent(item)
    ]
    return _best_education(eligible)


def _recent_movement(record: EnrichmentResult) -> bool:
    today = datetime.now(timezone.utc).date()
    for position in record.positions:
        started = _date(position.start_date)
        if started and 0 <= (today - started).days <= RECENT_MOVEMENT_DAYS:
            return True
    first_seen = _date(record.profile_created_at)
    if first_seen and 0 <= (today - first_seen).days <= 365:
        return True
    return False


def _is_founder_position(position: Position) -> bool:
    title = (position.title or "").lower()
    return any(term in title for term in FOUNDER_TITLE_TERMS)


def _technical_education(education: Education) -> bool:
    text = " ".join(
        value.lower()
        for value in (education.degree, education.field_of_study)
        if value
    )
    return "cs" in text.split() or any(
        term in text for term in TECHNICAL_EDUCATION_TERMS
    )


def _date(iso: str | None):
    if not iso:
        return None
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _year(iso: str | None) -> int | None:
    parsed = _date(iso)
    return parsed.year if parsed else None
