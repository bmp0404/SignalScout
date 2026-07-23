"""Provider-diversification tests. Fixture-driven; NEVER spend real credits —
the HTTP layer is mocked and providers are fakes. Covers the enrichment chain
(cache, budgets, PDL->Coresignal fallback, fail-soft outages), provider-search
discovery (no-GitHub candidates, dedupe/merge, dry-run), adapter allowlisting,
and the unchanged founder backtest.
"""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.config import Settings
from backend.container import Container
from backend.db.database import Database
from backend.db.repositories.enrichment import EnrichmentCacheRepository, EnrichmentUsageRepository
from backend.db.repositories.graph_edges import GraphEdgeRepository
from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.provider_identities import ProviderIdentityRepository
from backend.db.repositories.signals import SignalRepository
from backend.discovery.provider_expansion import ProviderExpander
from backend.domain.discovery_recipe import DiscoveryRecipe
from backend.domain.person import Person
from backend.enrichment.budgets import ProviderBudget
from backend.enrichment.provider_enricher import ProviderEnricher
from backend.enrichment.providers.base import (
    Education,
    EnrichmentProvider,
    EnrichmentQuery,
    EnrichmentResult,
    Position,
    ProviderSearchPage,
)
from backend.enrichment.providers.coresignal import CoresignalProvider
from backend.enrichment.providers.exa import ExaProvider
from backend.enrichment.providers.pdl import PdlProvider
from backend.scoring.engine import ScoringEngine
from backend.services.candidate_service import CandidateService


def _recent(days_ago: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


def make_result(provider, pid, name="Ada Lovelace", linkedin="https://linkedin.com/in/ada",
                school="Massachusetts Institute of Technology", with_evidence=True) -> EnrichmentResult:
    education = [Education(school=school, degree="BS", field_of_study="CS",
                          start_date=_recent(700), end_date=None)] if with_evidence else []
    positions = [Position(company="Startup", title="Founder", start_date=_recent(120), is_current=True)] \
        if with_evidence else []
    return EnrichmentResult(
        linkedin_url=linkedin, headline="Building things", education=education,
        positions=positions, location="Cambridge, MA", connections=42,
        provider=provider, provider_person_id=pid, full_name=name,
        raw={"provider": provider, "id": pid},
    )


class FakeProvider(EnrichmentProvider):
    """No-HTTP provider double with call counters and controllable outcomes."""

    def __init__(self, name, enrich_result=None, search_results=None, error=None):
        self.name = name
        self._enrich_result = enrich_result
        self._search_results = search_results or []
        self._error = error
        self.enrich_calls = 0
        self.search_calls = 0
        self.last_error = None

    def enrich_person(self, query):
        self.enrich_calls += 1
        self.last_error = self._error
        if self._error:
            return None
        return self._enrich_result

    def search_people(self, filters, size=10):
        self.search_calls += 1
        self.last_error = self._error
        if self._error:
            return []
        return list(self._search_results[:size])


class PagedFakeProvider(FakeProvider):
    def __init__(self, name, pages):
        super().__init__(name)
        self.pages = pages
        self.cursors = []

    def search_page(self, filters, size=10, cursor=None):
        self.search_calls += 1
        self.cursors.append(cursor)
        index = int(cursor or 0)
        results = list(self.pages[index][:size])
        has_more = index + 1 < len(self.pages)
        return ProviderSearchPage(
            results=results,
            next_cursor=str(index + 1) if has_more else None,
            exhausted=not has_more,
            api_requests=1,
            returned_records=len(results),
            credit_units=len(results),
        )


class ChainTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db = Database(root / "test.db", database_url="")
        self.db.init_schema()
        self.persons = PersonRepository(self.db)
        self.signals = SignalRepository(self.db)
        self.cache = EnrichmentCacheRepository(self.db)
        self.usage = EnrichmentUsageRepository(self.db)
        self.identities = ProviderIdentityRepository(self.db)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def settings(self, **overrides) -> Settings:
        base = dict(db_path=Path(self.temp_dir.name) / "test.db", database_url="")
        base.update(overrides)
        return Settings(**base)

    def budget(self, **overrides) -> ProviderBudget:
        return ProviderBudget(self.usage, self.settings(**overrides))

    def save_person(self, **kwargs) -> Person:
        person = Person(cohort="discovery", **kwargs)
        self.persons.save(person)
        return person

    def _filters_file(self, pdl=None, coresignal=None, per_filter=10, per_run=25) -> Path:
        path = Path(self.temp_dir.name) / "filters.json"
        path.write_text(json.dumps({
            "max_results_per_filter": per_filter,
            "max_new_people_per_run": per_run,
            "pdl_filters": pdl or [],
            "coresignal_filters": coresignal or [],
        }))
        return path

    def _expander(self, providers, filters_file, **budget_overrides) -> ProviderExpander:
        enricher = ProviderEnricher(providers, self.signals, self.cache, self.budget(**budget_overrides))
        return ProviderExpander(
            providers, self.persons, self.identities, enricher,
            self.budget(**budget_overrides), filters_file,
        )


class EnrichmentChainTests(ChainTestBase):
    def test_cache_prevents_repeat_calls_within_ttl(self):
        pdl = FakeProvider("pdl", enrich_result=make_result("pdl", "p1"))
        enricher = ProviderEnricher([pdl], self.signals, self.cache, self.budget())
        person = self.save_person(name="Ada Lovelace")

        first = enricher.enrich(person)
        second = enricher.enrich(person)

        self.assertTrue(first)          # signals emitted on first run
        self.assertEqual(second, [])    # nothing new; served from cache
        self.assertEqual(pdl.enrich_calls, 1)  # only one paid call across both runs
        self.assertEqual(self.usage.count_for_month("pdl", datetime.now(timezone.utc).strftime("%Y-%m")), 1)

    def test_cached_miss_is_not_refetched(self):
        pdl = FakeProvider("pdl", enrich_result=None)  # definitive no-match
        enricher = ProviderEnricher([pdl], self.signals, self.cache, self.budget())
        person = self.save_person(name="Nobody Here")

        enricher.enrich(person)
        enricher.enrich(person)
        self.assertEqual(pdl.enrich_calls, 1)  # miss cached; not re-fetched

    def test_pdl_miss_falls_through_to_coresignal(self):
        pdl = FakeProvider("pdl", enrich_result=None)  # definitive miss
        coresignal = FakeProvider("coresignal", enrich_result=make_result("coresignal", "c1"))
        enricher = ProviderEnricher([pdl, coresignal], self.signals, self.cache, self.budget())
        person = self.save_person(name="Ada Lovelace")

        outcome = enricher.run(person)
        self.assertEqual(outcome.status, "matched")
        self.assertEqual(outcome.provider, "coresignal")
        self.assertTrue(outcome.fallback)
        self.assertEqual(pdl.enrich_calls, 1)
        self.assertEqual(coresignal.enrich_calls, 1)

    def test_confident_pdl_match_does_not_reach_coresignal(self):
        pdl = FakeProvider("pdl", enrich_result=make_result("pdl", "p1"))
        coresignal = FakeProvider("coresignal", enrich_result=make_result("coresignal", "c1"))
        enricher = ProviderEnricher([pdl, coresignal], self.signals, self.cache, self.budget())
        person = self.save_person(name="Ada Lovelace")

        outcome = enricher.run(person)
        self.assertEqual(outcome.provider, "pdl")
        self.assertFalse(outcome.fallback)
        self.assertEqual(coresignal.enrich_calls, 0)  # no double-charge

    def test_provider_outage_is_failsoft_and_not_cached(self):
        pdl = FakeProvider("pdl", error="HTTP 500")
        coresignal = FakeProvider("coresignal", error="HTTP 500")
        enricher = ProviderEnricher([pdl, coresignal], self.signals, self.cache, self.budget())
        person = self.save_person(name="Ada Lovelace")

        outcome = enricher.run(person)          # must not raise
        self.assertEqual(outcome.new_signals, [])
        # Error is not a definitive miss: nothing cached, so a retry is still possible.
        self.assertIsNone(self.cache.get("pdl", person.id))
        # PDL errored (not a clean miss) so the chain stops — Coresignal untouched.
        self.assertEqual(coresignal.enrich_calls, 0)

    def test_budget_stops_cleanly(self):
        # monthly cap 1, split 0 -> enrich lane cap = 1; per-run cap high.
        budget = self.budget(pdl_monthly_cap=1, pdl_search_split=0.0)
        pdl = FakeProvider("pdl", enrich_result=make_result("pdl", "p1"))
        enricher = ProviderEnricher([pdl], self.signals, self.cache, budget)
        p1 = self.save_person(name="Ada Lovelace")
        p2 = self.save_person(name="Grace Hopper")

        o1 = enricher.run(p1)
        o2 = enricher.run(p2)
        self.assertEqual(o1.status, "matched")
        self.assertEqual(o2.status, "skipped")  # budget exhausted, skipped not errored
        self.assertEqual(pdl.enrich_calls, 1)

    def test_founder_gets_contacts_but_no_scored_signals(self):
        pdl = FakeProvider("pdl", enrich_result=make_result("pdl", "p1"))
        enricher = ProviderEnricher([pdl], self.signals, self.cache, self.budget())
        founder = Person(name="Ada Lovelace", cohort="founder")
        self.persons.save(founder)

        signals = enricher.enrich(founder)
        self.assertEqual(signals, [])                        # no scored signals
        self.assertEqual(founder.contact_info.get("enriched_by"), "pdl")  # contacts merged

    def test_dry_run_spends_nothing(self):
        pdl = FakeProvider("pdl", enrich_result=make_result("pdl", "p1"))
        enricher = ProviderEnricher([pdl], self.signals, self.cache, self.budget())
        person = self.save_person(name="Ada Lovelace")

        outcome = enricher.run(person, dry_run=True)
        self.assertEqual(outcome.status, "attempted")
        self.assertEqual(pdl.enrich_calls, 0)          # provider never called
        self.assertIsNone(self.cache.get("pdl", person.id))
        self.assertEqual(self.usage.count_for("pdl", datetime.now(timezone.utc).date().isoformat()), 0)


class ProviderSearchTests(ChainTestBase):
    def test_sqlite_workers_do_not_share_connection(self):
        barrier = threading.Barrier(3)

        def worker_connection_id():
            barrier.wait()
            return id(self.db.conn)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(worker_connection_id)
            second = executor.submit(worker_connection_id)
            barrier.wait()
            connection_ids = {first.result(), second.result()}

        self.assertEqual(len(connection_ids), 2)

    def test_legacy_provider_identity_backfill_is_idempotent(self):
        provider_person = self.save_person(name="Legacy Provider Person")
        github_person = self.save_person(
            name="Provider-Enriched GitHub Person",
            github_username="github-person",
        )
        today = datetime.now(timezone.utc).date().isoformat()
        self.identities.link("pdl", "provider-legacy", provider_person.id, None, today)
        self.identities.link("pdl", "github-merge", github_person.id, None, today)

        migrated = PersonRepository(self.db)
        self.assertEqual(
            migrated.get(provider_person.id).discovery_origin,
            "provider_search",
        )
        self.assertEqual(migrated.get(github_person.id).discovery_origin, "github")
        self.assertEqual(self.signals.for_person(provider_person.id), [])

        migrated_again = PersonRepository(self.db)
        self.assertEqual(
            migrated_again.get(provider_person.id).discovery_origin,
            "provider_search",
        )

    def test_search_creates_candidate_without_github(self):
        pdl = FakeProvider("pdl", search_results=[make_result("pdl", "p1", name="Katie Bouman")])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "massachusetts institute of technology"}])
        expander = self._expander([pdl], filters)

        result = expander.expand()
        self.assertEqual(len(result.created), 1)
        person = result.created[0]
        self.assertIsNone(person.github_username)         # NO GitHub account required
        self.assertEqual(person.cohort, "discovery")
        self.assertTrue(person.linkedin_url)
        self.assertEqual(person.contact_info.get("discovery_lane"), "provider_search")
        self.assertTrue(self.signals.for_person(person.id))  # provider evidence emitted

    def test_duplicate_provider_records_merge(self):
        record = make_result("pdl", "p1", name="Katie Bouman")
        pdl = FakeProvider("pdl", search_results=[record])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "massachusetts institute of technology"}])
        expander = self._expander([pdl], filters)

        first = expander.expand()
        second = expander.expand()  # completed page is checkpointed and never repurchased
        self.assertEqual(len(first.created), 1)
        self.assertEqual(len(second.created), 0)
        self.assertEqual(second.merged, 0)
        self.assertEqual(pdl.search_calls, 1)
        self.assertEqual(len(self.persons.all("discovery")), 1)  # never duplicated

    def test_dedupe_across_providers_by_linkedin(self):
        pdl_rec = make_result("pdl", "p1", name="Katie Bouman", linkedin="https://www.linkedin.com/in/katie/")
        cs_rec = make_result("coresignal", "c9", name="Katie Bouman", linkedin="https://linkedin.com/in/katie")
        pdl = FakeProvider("pdl", search_results=[pdl_rec])
        coresignal = FakeProvider("coresignal", search_results=[cs_rec])
        filters = self._filters_file(
            pdl=[{"label": "MIT", "school": "mit"}],
            coresignal=[{"label": "MIT", "school": "MIT"}],
        )
        expander = self._expander([pdl, coresignal], filters)

        result = expander.expand()
        self.assertEqual(len(result.created), 1)   # one person across both providers
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(len(self.persons.all("discovery")), 1)

    def test_pagination_resumes_next_page_without_repeating(self):
        pdl = PagedFakeProvider("pdl", [
            [make_result("pdl", "p1", name="Person One", linkedin="https://linkedin.com/in/p1")],
            [make_result("pdl", "p2", name="Person Two", linkedin="https://linkedin.com/in/p2")],
        ])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])
        expander = self._expander([pdl], filters)

        first = expander.expand()
        second = expander.expand()
        third = expander.expand()

        self.assertEqual([len(first.created), len(second.created), len(third.created)], [1, 1, 0])
        self.assertEqual(pdl.cursors, [None, "1"])
        checkpoint = self.identities.checkpoints()[0]
        self.assertEqual(checkpoint.requested_pages, 2)
        self.assertEqual(checkpoint.returned_records, 2)
        self.assertTrue(checkpoint.exhausted)

    def test_review_tier_emits_only_dated_education(self):
        record = make_result("pdl", "review-1", name="Review Person")
        record.positions = []
        record.profile_created_at = None
        record.connections = 12
        pdl = FakeProvider("pdl", search_results=[record])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])

        result = self._expander([pdl], filters).expand()

        self.assertEqual(result.review, 1)
        person = result.created[0]
        self.assertEqual(person.evidence_tier, "review")
        self.assertTrue(person.review_required)
        self.assertEqual(
            {signal.signal_type for signal in self.signals.for_person(person.id)},
            {"education_signal"},
        )

    def test_verified_tier_requires_dated_movement(self):
        pdl = FakeProvider("pdl", search_results=[make_result("pdl", "verified-1")])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])

        result = self._expander([pdl], filters).expand()

        self.assertEqual(result.verified, 1)
        self.assertEqual(result.created[0].evidence_tier, "verified")
        self.assertFalse(result.created[0].review_required)

    def test_recent_technical_education_is_selected_over_undated_entry(self):
        record = make_result("pdl", "multi-edu")
        record.education.append(Education(school="Undated Business School", degree="MBA"))
        pdl = FakeProvider("pdl", search_results=[record])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])

        result = self._expander([pdl], filters).expand()

        self.assertEqual(result.created[0].school, "Massachusetts Institute of Technology")

    def test_undated_technical_education_is_rejected(self):
        record = make_result("pdl", "undated-1")
        record.education = [Education(school="MIT", field_of_study="Computer Science")]
        pdl = FakeProvider("pdl", search_results=[record])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])

        result = self._expander([pdl], filters).expand()

        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.rejection_reasons, {"undated_or_stale_education": 1})

    def test_search_error_is_audited_without_advancing_or_spending(self):
        pdl = FakeProvider("pdl", error="HTTP 500")
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])

        self._expander([pdl], filters).expand()

        checkpoint = self.identities.checkpoints()[0]
        self.assertEqual(checkpoint.next_page, 0)
        self.assertEqual(checkpoint.requested_pages, 1)
        self.assertEqual(checkpoint.api_requests, 1)
        self.assertEqual(checkpoint.error_count, 1)
        self.assertEqual(checkpoint.last_outcome, "error:HTTP 500")
        self.assertEqual(
            self.usage.count_for_month(
                "pdl",
                datetime.now(timezone.utc).strftime("%Y-%m"),
                "search",
            ),
            0,
        )


class RecipeTests(ChainTestBase):
    """DiscoveryRecipe layered on ProviderExpander.run_recipe: same engine,
    same dedupe ladder, same ProviderBudget ledger as expand()."""

    def _founder_recipe(self, **overrides) -> DiscoveryRecipe:
        base = dict(
            id="young_founders", name="Young founders", provider="pdl",
            query_type="founder", filters={"title": ["founder", "co-founder"]},
            default_limit=10, approval_state="approved",
        )
        base.update(overrides)
        return DiscoveryRecipe(**base)

    def test_recipe_run_requires_approval(self):
        pdl = FakeProvider("pdl", search_results=[make_result("pdl", "p1")])
        expander = self._expander([pdl], self._filters_file())
        recipe = self._founder_recipe(approval_state="pending")

        with self.assertRaises(PermissionError):
            expander.run_recipe(recipe)
        self.assertEqual(pdl.search_calls, 0)

    def test_recipe_run_requires_approval_even_when_provider_unconfigured(self):
        # No matching provider registered (e.g. PDL_API_KEY unset) — approval
        # must still be checked before the provider lookup short-circuits.
        expander = self._expander([], self._filters_file())
        recipe = self._founder_recipe(approval_state="pending")

        with self.assertRaises(PermissionError):
            expander.run_recipe(recipe)

    def test_recipe_dry_run_allowed_without_approval_and_spends_nothing(self):
        pdl = FakeProvider("pdl", search_results=[make_result("pdl", "p1")])
        expander = self._expander([pdl], self._filters_file())
        recipe = self._founder_recipe(approval_state="pending")

        result = expander.run_recipe(recipe, dry_run=True)
        self.assertEqual(result.created, [])
        self.assertEqual(pdl.search_calls, 0)  # never calls the provider
        self.assertEqual(len(self.persons.all("discovery")), 0)
        self.assertEqual(
            self.usage.count_for("pdl", datetime.now(timezone.utc).date().isoformat()), 0
        )

    def test_founder_admission_admits_without_technical_education(self):
        # MBA, not a technical field — would fail the default admission gate.
        record = make_result("pdl", "p1", name="Priya Founder", school="Booth School of Business")
        record.education[0].field_of_study = "MBA"
        record.education[0].degree = "MBA"
        pdl = FakeProvider("pdl", search_results=[record])
        expander = self._expander([pdl], self._filters_file())
        recipe = self._founder_recipe(query_type="founder")

        result = expander.run_recipe(recipe)
        self.assertEqual(len(result.created), 1)
        person = result.created[0]
        self.assertEqual(person.discovery_source, "pdl_discovery")
        self.assertEqual(person.discovery_origin, "provider_search")
        self.assertEqual(pdl.enrich_calls, 0)  # search result stored directly, never re-enriched

    def test_student_technical_admission_rejects_same_record_without_technical_education(self):
        record = make_result("pdl", "p1", name="Priya Founder", school="Booth School of Business")
        record.education[0].field_of_study = "MBA"
        record.education[0].degree = "MBA"
        pdl = FakeProvider("pdl", search_results=[record])
        expander = self._expander([pdl], self._filters_file())
        recipe = self._founder_recipe(query_type="student_technical")

        result = expander.run_recipe(recipe)
        self.assertEqual(len(result.created), 0)
        self.assertEqual(result.rejected, 1)
        self.assertIn("nontechnical_or_missing_education", result.rejection_reasons)

    def test_relative_filters_computed_at_run_time(self):
        class CapturingProvider(FakeProvider):
            def search_page(self, filters, size=10, cursor=None):
                self.received_filters = filters
                return super().search_page(filters, size=size, cursor=cursor)

        pdl = CapturingProvider("pdl", search_results=[make_result("pdl", "p1")])
        expander = self._expander([pdl], self._filters_file())
        recipe = self._founder_recipe(
            filters={"title": ["founder"]}, relative_filters={"job_start_date_gte": 30},
        )

        expander.run_recipe(recipe)
        expected = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
        self.assertEqual(pdl.received_filters.get("job_start_date_gte"), expected)

    def test_recipe_dedupes_against_existing_github_candidate_by_name_and_school(self):
        self.save_person(
            name="Katie Bouman", github_username="kbouman",
            school="Massachusetts Institute of Technology",
        )
        record = make_result("pdl", "p1", name="Katie Bouman")
        pdl = FakeProvider("pdl", search_results=[record])
        expander = self._expander([pdl], self._filters_file())
        recipe = self._founder_recipe()

        result = expander.run_recipe(recipe)
        self.assertEqual(len(result.created), 0)
        self.assertEqual(result.merged, 1)
        self.assertEqual(len(self.persons.all("discovery")), 1)  # never duplicated

    def test_recipe_hard_caps_results_at_default_limit(self):
        names = ["Ada Lovelace", "Grace Hopper", "Katie Bouman", "Radia Perlman", "Margaret Hamilton"]
        records = [make_result("pdl", f"p{i}", name=name,
                                linkedin=f"https://linkedin.com/in/{name.split()[1].lower()}")
                   for i, name in enumerate(names)]
        pdl = FakeProvider("pdl", search_results=records)
        expander = self._expander([pdl], self._filters_file())
        recipe = self._founder_recipe(default_limit=3)

        result = expander.run_recipe(recipe)
        self.assertEqual(len(result.created), 3)

    def test_recipe_budget_exhausted_blocks_real_run(self):
        pdl = FakeProvider("pdl", search_results=[make_result("pdl", "p1")])
        expander = self._expander([pdl], self._filters_file(), pdl_monthly_cap=0)
        recipe = self._founder_recipe()

        result = expander.run_recipe(recipe)
        self.assertEqual(result.created, [])
        self.assertEqual(pdl.search_calls, 0)


class FakeCompanyFirstProvider(FakeProvider):
    """FakeProvider extended with the company-first methods."""

    def __init__(self, name, companies, employees_by_company):
        super().__init__(name)
        self.companies = companies
        self.employees_by_company = employees_by_company
        self.company_search_calls = 0
        self.employee_search_calls = []

    def search_companies(self, filters, size=10):
        self.company_search_calls += 1
        return list(self.companies[:size])

    def search_company_employees(self, company_id, title_filters, size=10):
        self.employee_search_calls.append(company_id)
        return list(self.employees_by_company.get(company_id, [])[:size])


class CompanyFirstRecipeTests(ChainTestBase):
    def _recipe(self, **overrides) -> DiscoveryRecipe:
        base = dict(
            id="seed_stage_company_first", name="Seed-stage company-first",
            provider="coresignal", query_type="company_first",
            filters={
                "company": {"founded_gte": 2024, "employees_count_lte": 10},
                "employee_title": {"title": "Founder"},
            },
            default_limit=10, approval_state="approved",
        )
        base.update(overrides)
        return DiscoveryRecipe(**base)

    def test_company_first_creates_candidates_via_shared_ingest(self):
        companies = [{"id": "c1", "name": "Seed Co", "employees_count": 5}]
        employees = {
            "c1": [make_result("coresignal", "e1", name="Ada Lovelace",
                                linkedin="https://linkedin.com/in/ada")],
        }
        provider = FakeCompanyFirstProvider("coresignal", companies, employees)
        expander = self._expander([provider], self._filters_file())
        recipe = self._recipe()

        result = expander.run_recipe(recipe)
        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].discovery_source, "coresignal_discovery")
        self.assertEqual(provider.company_search_calls, 1)
        self.assertEqual(provider.employee_search_calls, ["c1"])

    def test_company_first_dry_run_spends_nothing(self):
        companies = [{"id": "c1", "name": "Seed Co", "employees_count": 5}]
        employees = {"c1": [make_result("coresignal", "e1", name="Ada Lovelace")]}
        provider = FakeCompanyFirstProvider("coresignal", companies, employees)
        expander = self._expander([provider], self._filters_file())
        recipe = self._recipe()

        result = expander.run_recipe(recipe, dry_run=True)
        self.assertEqual(result.created, [])
        self.assertEqual(provider.company_search_calls, 0)  # step 1 never runs in dry-run
        self.assertEqual(provider.employee_search_calls, [])  # step 2 never runs
        self.assertEqual(
            self.usage.count_for("coresignal", datetime.now(timezone.utc).date().isoformat()), 0
        )

    def test_company_first_requires_approval(self):
        provider = FakeCompanyFirstProvider("coresignal", [], {})
        expander = self._expander([provider], self._filters_file())
        recipe = self._recipe(approval_state="pending")

        with self.assertRaises(PermissionError):
            expander.run_recipe(recipe)

    def test_company_first_dedupes_across_companies(self):
        companies = [{"id": "c1"}, {"id": "c2"}]
        shared = make_result("coresignal", "e1", name="Ada Lovelace",
                              linkedin="https://linkedin.com/in/ada")
        employees = {"c1": [shared], "c2": [shared]}
        provider = FakeCompanyFirstProvider("coresignal", companies, employees)
        expander = self._expander([provider], self._filters_file())
        recipe = self._recipe()

        result = expander.run_recipe(recipe)
        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.duplicates, 1)

    def test_search_and_collect_credits_tracked_separately(self):
        companies = [{"id": "c1"}]
        employees = {"c1": [make_result("coresignal", "e1", name="Ada Lovelace")]}
        provider = FakeCompanyFirstProvider("coresignal", companies, employees)
        expander = self._expander([provider], self._filters_file())
        recipe = self._recipe()

        expander.run_recipe(recipe)
        checkpoint = expander.recipe_checkpoint(recipe)
        self.assertIsNotNone(checkpoint)
        self.assertGreater(checkpoint.search_credit_units, 0)
        self.assertGreater(checkpoint.collect_credit_units, 0)


class EnrichmentQueueTests(ChainTestBase):
    def _filters_file(self, pdl=None, coresignal=None, per_filter=10, per_run=25) -> Path:
        path = Path(self.temp_dir.name) / "filters.json"
        path.write_text(json.dumps({
            "max_results_per_filter": per_filter,
            "max_new_people_per_run": per_run,
            "pdl_filters": pdl or [],
            "coresignal_filters": coresignal or [],
        }))
        return path

    def _expander(self, providers, filters_file, **budget_overrides) -> ProviderExpander:
        enricher = ProviderEnricher(
            providers,
            self.signals,
            self.cache,
            self.budget(**budget_overrides),
        )
        return ProviderExpander(
            providers,
            self.persons,
            self.identities,
            enricher,
            self.budget(**budget_overrides),
            filters_file,
        )

    def test_queue_prioritizes_high_scoring_github_only_candidates(self):
        enricher = ProviderEnricher([], self.signals, self.cache, self.budget())
        low = self.save_person(
            name="Low Score",
            github_username="low",
            score=10,
            discovery_origin="github",
            enrichment_status="pending_budget",
        )
        high = self.save_person(
            name="High Score",
            github_username="high",
            score=90,
            discovery_origin="github",
            enrichment_status="pending_budget",
        )

        self.assertEqual(enricher.prioritize([low, high]), [high, low])
        self.assertEqual(enricher.pending_github_count([low, high]), 2)

    def test_enrichment_statuses_cover_match_miss_error_and_budget(self):
        matched_provider = FakeProvider("pdl", enrich_result=make_result("pdl", "p1"))
        matched = self.save_person(name="Match Person", github_username="match")
        ProviderEnricher(
            [matched_provider], self.signals, self.cache, self.budget()
        ).run(matched)
        self.assertEqual(matched.enrichment_status, "provider_enriched")

        missed = self.save_person(name="Miss Person", github_username="miss")
        ProviderEnricher(
            [FakeProvider("pdl")], self.signals, self.cache, self.budget()
        ).run(missed)
        self.assertEqual(missed.enrichment_status, "provider_no_match")

        errored = self.save_person(name="Error Person", github_username="error")
        ProviderEnricher(
            [FakeProvider("pdl", error="HTTP 500")],
            self.signals,
            self.cache,
            self.budget(),
        ).run(errored)
        self.assertEqual(errored.enrichment_status, "provider_error")

        pending = self.save_person(name="Pending Person", github_username="pending")
        exhausted = self.budget(pdl_monthly_cap=0)
        ProviderEnricher(
            [FakeProvider("pdl")], self.signals, self.cache, exhausted
        ).run(pending)
        self.assertEqual(pending.enrichment_status, "pending_budget")

    def test_candidate_payload_exposes_rebalance_metadata(self):
        person = self.save_person(
            name="Provider Candidate",
            score=55,
            discovery_origin="provider_search",
            evidence_tier="review",
            review_required=True,
            enrichment_status="provider_enriched",
            enrichment_provider="pdl",
        )
        edges = GraphEdgeRepository(self.db)
        service = CandidateService(
            self.persons,
            self.signals,
            edges,
            ScoringEngine(),
            40,
        )

        payload = service.list_candidates("discovery")[0]

        self.assertEqual(payload["discovery_origin"], "provider_search")
        self.assertEqual(payload["evidence_status"], "review")
        self.assertEqual(payload["evidence_tier"], "review")
        self.assertTrue(payload["review_required"])
        self.assertEqual(payload["enrichment_status"], "provider_enriched")

    def test_low_confidence_records_rejected(self):
        # No linkedin + single-token name -> ambiguous; and no evidence.
        bad = EnrichmentResult(full_name="Anon", provider="pdl", provider_person_id="x")
        pdl = FakeProvider("pdl", search_results=[bad])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])
        expander = self._expander([pdl], filters)

        result = expander.expand()
        self.assertEqual(len(result.created), 0)
        self.assertGreaterEqual(result.rejected, 1)

    def test_search_dry_run_spends_nothing(self):
        pdl = FakeProvider("pdl", search_results=[make_result("pdl", "p1")])
        filters = self._filters_file(pdl=[{"label": "MIT", "school": "mit"}])
        expander = self._expander([pdl], filters)

        result = expander.expand(dry_run=True)
        self.assertEqual(result.attempted, 1)
        self.assertEqual(len(result.created), 0)
        self.assertEqual(pdl.search_calls, 0)
        self.assertEqual(self.identities.checkpoints(), [])
        self.assertEqual(self.usage.count_for("pdl", datetime.now(timezone.utc).date().isoformat()), 0)

    def test_search_budget_limits_records(self):
        results = [make_result("pdl", f"p{i}", name=f"Person {i}",
                               linkedin=f"https://linkedin.com/in/p{i}") for i in range(10)]
        pdl = FakeProvider("pdl", search_results=results)
        filters = self._filters_file(
            pdl=[{"label": "MIT", "school": "mit"}], per_filter=10, per_run=25,
        )
        # search lane cap = floor(5 * 1.0) = 5
        expander = self._expander([pdl], filters, pdl_monthly_cap=5, pdl_search_split=1.0)

        result = expander.expand()
        self.assertLessEqual(len(result.created), 5)
        used = self.usage.count_for_month("pdl", datetime.now(timezone.utc).strftime("%Y-%m"), "search")
        self.assertLessEqual(used, 5)


class AdapterAllowlistTests(unittest.TestCase):
    def test_pdl_search_where_is_allowlisted_and_escaped(self):
        provider = PdlProvider("k", session=_DummySession())
        where = provider._build_where({
            "school": "O'Hara Institute",           # single quote must be escaped
            "title_level": ["entry", "training"],   # list -> IN(...)
            "evil_column": "DROP TABLE",             # unknown key -> ignored
        })
        self.assertIn("education.school.name = 'O''Hara Institute'", where)
        self.assertIn("job_title_levels IN ('entry', 'training')", where)
        self.assertNotIn("evil_column", where)
        self.assertNotIn("DROP TABLE", where)

    def test_pdl_escape_strips_control_chars(self):
        self.assertEqual(PdlProvider._escape("a'b\n"), "a''b")

    def test_coresignal_filters_are_allowlisted(self):
        allowed = CoresignalProvider._build_filters({
            "school": "MIT", "location": "Boston", "bogus": "x",
        })
        self.assertEqual(allowed, {"education_institution_name": "MIT", "location": "Boston"})


class AdapterHttpMockTests(unittest.TestCase):
    def test_pdl_enrich_maps_200_and_handles_404_and_error(self):
        session = _DummySession()
        provider = PdlProvider("k", session=session)

        session.get_response = _Resp(200, {"likelihood": 8, "data": {
            "full_name": "Ada Lovelace", "linkedin_url": "linkedin.com/in/ada",
            "education": [{"school": {"name": "MIT"}, "start_date": "2019", "end_date": "2023"}],
            "id": "PDL-1",
        }})
        result = provider.enrich_person(_query())
        self.assertIsNotNone(result)
        self.assertEqual(result.full_name, "Ada Lovelace")
        self.assertEqual(result.linkedin_url, "https://linkedin.com/in/ada")
        self.assertEqual(result.provider_person_id, "PDL-1")
        self.assertIsNone(provider.last_error)

        session.get_response = _Resp(404, {})
        self.assertIsNone(provider.enrich_person(_query()))
        self.assertIsNone(provider.last_error)  # 404 = clean miss, cacheable

        session.get_response = _Resp(401, {})
        self.assertIsNone(provider.enrich_person(_query()))
        self.assertEqual(provider.last_error, "HTTP 401")  # auth error, must not cache

    def test_pdl_search_page_first_request_omits_scroll_token(self):
        session = _DummySession()
        session.post_response = _Resp(200, {
            "data": [{
                "id": "PDL-1",
                "full_name": "Page One",
                "linkedin_url": "linkedin.com/in/page-one",
            }],
            "scroll_token": "tok-1",
        })
        provider = PdlProvider("k", session=session)

        page = provider.search_page({"school": "MIT"}, size=1, cursor=None)

        self.assertNotIn("scroll_token", session.last_post_json)
        self.assertNotIn("from", session.last_post_json)  # PDL v5 deprecated `from`
        self.assertEqual(page.returned_records, 1)
        self.assertEqual(page.next_cursor, "tok-1")  # more pages: records == size and a token came back
        self.assertFalse(page.exhausted)

    def test_pdl_search_page_resumes_with_scroll_token(self):
        session = _DummySession()
        session.post_response = _Resp(200, {
            "data": [{
                "id": "PDL-11",
                "full_name": "Page Eleven",
                "linkedin_url": "linkedin.com/in/page-eleven",
            }],
            "scroll_token": None,
        })
        provider = PdlProvider("k", session=session)

        page = provider.search_page({"school": "MIT"}, size=10, cursor="tok-1")

        self.assertEqual(session.last_post_json["scroll_token"], "tok-1")
        self.assertEqual(page.returned_records, 1)
        self.assertEqual(page.credit_units, 1)
        self.assertTrue(page.exhausted)  # fewer records than size and no scroll_token

    def test_coresignal_search_page_offsets_collects_and_counts_requests(self):
        session = _DummySession()
        session.post_response = _Resp(200, ["c1", "c2", "c3"])
        session.get_response = _Resp(200, {
            "id": "c2",
            "full_name": "Core Signal",
            "linkedin_url": "linkedin.com/in/core-signal",
        })
        provider = CoresignalProvider("k", session=session)

        page = provider.search_page({"school": "MIT"}, size=1, cursor="1")

        self.assertEqual(json.loads(page.next_cursor)["offset"], 2)
        self.assertEqual(page.api_requests, 2)
        self.assertEqual(page.credit_units, 2)
        self.assertEqual(page.results[0].provider_person_id, "c2")

        resumed = provider.search_page(
            {"school": "MIT"},
            size=1,
            cursor=page.next_cursor,
        )
        self.assertEqual(session.post_calls, 1)
        self.assertEqual(resumed.api_requests, 1)


def _exa_people_payload():
    return {
        "results": [
            {
                "url": "https://www.linkedin.com/in/ada-builder",
                "title": "Ada Builder - Founder at NovaAI",
                "id": "doc-1",
                "highlights": ["Founder building NovaAI"],
                "entities": [
                    {
                        "id": "person-123",
                        "type": "person",
                        "version": 1,
                        "properties": {
                            "name": "Ada Builder",
                            "firstName": "Ada",
                            "lastName": "Builder",
                            "location": "San Francisco, CA",
                            "workHistory": [
                                {
                                    "title": "Founder",
                                    "location": "SF",
                                    "dates": {"from": "2025-01", "to": None},
                                    "company": {"id": "c1", "name": "NovaAI"},
                                }
                            ],
                            "educationHistory": [
                                {
                                    "degree": "BS Computer Science",
                                    "dates": {"from": "2019", "to": "2023"},
                                    "institution": {"id": "i1", "name": "MIT"},
                                }
                            ],
                            "research": None,
                        },
                    }
                ],
            }
        ]
    }


class ExaAdapterTests(unittest.TestCase):
    def test_search_page_maps_person_entity(self):
        session = _DummySession()
        session.post_response = _Resp(200, _exa_people_payload())
        provider = ExaProvider("k", session=session)

        page = provider.search_page({"query": "young technical founders"}, size=10)

        self.assertEqual(session.last_post_json["category"], "people")
        self.assertEqual(page.returned_records, 1)
        self.assertEqual(page.credit_units, 1)
        self.assertTrue(page.exhausted)
        result = page.results[0]
        self.assertEqual(result.full_name, "Ada Builder")
        self.assertEqual(result.linkedin_url, "https://www.linkedin.com/in/ada-builder")
        self.assertEqual(result.provider_person_id, "person-123")
        self.assertEqual(result.location, "San Francisco, CA")
        self.assertEqual(len(result.positions), 1)
        self.assertEqual(result.positions[0].title, "Founder")
        self.assertEqual(result.positions[0].company, "NovaAI")
        self.assertTrue(result.positions[0].is_current)
        self.assertEqual(len(result.education), 1)
        self.assertEqual(result.education[0].school, "MIT")
        self.assertEqual(result.education[0].start_date, "2019-01-01")
        self.assertEqual(result.education[0].end_date, "2023-01-01")
        self.assertEqual(result.raw["source"], "exa")
        self.assertIn("Founder", result.headline)

    def test_enrich_person_is_search_only_noop(self):
        provider = ExaProvider("k", session=_DummySession())
        self.assertIsNone(provider.enrich_person(_query()))
        self.assertIsNone(provider.last_error)

    def test_missing_query_makes_no_request(self):
        session = _DummySession()
        provider = ExaProvider("k", session=session)
        page = provider.search_page({}, size=5)
        self.assertEqual(page.results, [])
        self.assertEqual(session.post_calls, 0)

    def test_http_error_is_fail_soft(self):
        session = _DummySession()
        session.post_response = _Resp(500, {}, text="boom")
        provider = ExaProvider("k", session=session)
        page = provider.search_page({"query": "x"}, size=5)
        self.assertEqual(page.results, [])
        self.assertEqual(provider.last_error, "HTTP 500")


class ExaRecipeTests(ChainTestBase):
    def _exa_result(self, pid="person-1", name="Ada Builder", founder=True) -> EnrichmentResult:
        positions = (
            [Position(company="NovaAI", title="Founder", start_date=_recent(60), is_current=True)]
            if founder
            else []
        )
        return EnrichmentResult(
            linkedin_url="https://linkedin.com/in/ada-builder",
            headline="Founder at NovaAI",
            education=[],
            positions=positions,
            location="San Francisco, CA",
            provider="exa",
            provider_person_id=pid,
            full_name=name,
            raw={"source": "exa", "url": "https://exa.example/ada", "headline": "Founder at NovaAI"},
        )

    def _recipe(self, **overrides) -> DiscoveryRecipe:
        base = dict(
            id="exa_young_technical_founders", name="Young technical founders (Exa)",
            provider="exa", query_type="exa",
            filters={"query": "young technical founders"},
            default_limit=10, approval_state="approved",
        )
        base.update(overrides)
        return DiscoveryRecipe(**base)

    def test_exa_recipe_creates_reviewable_person_with_web_signal(self):
        provider = FakeProvider("exa", search_results=[self._exa_result(founder=False)])
        expander = self._expander([provider], self._filters_file())

        result = expander.run_recipe(self._recipe())

        self.assertEqual(len(result.created), 1)
        person = result.created[0]
        self.assertEqual(person.discovery_source, "exa_discovery")
        self.assertEqual(person.discovery_origin, "provider_search")
        self.assertEqual(person.evidence_tier, "review")
        self.assertTrue(person.needs_review)
        signal_types = {s.signal_type for s in self.signals.for_person(person.id)}
        self.assertIn("web_presence", signal_types)

    def test_exa_founder_with_recent_move_is_verified(self):
        provider = FakeProvider("exa", search_results=[self._exa_result(founder=True)])
        expander = self._expander([provider], self._filters_file())

        result = expander.run_recipe(self._recipe())
        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].evidence_tier, "verified")

    def test_exa_recipe_missing_provider_no_ops(self):
        expander = self._expander([], self._filters_file())
        result = expander.run_recipe(self._recipe())
        self.assertEqual(result.created, [])
        self.assertFalse(expander.has_provider("exa"))


class BacktestRegressionTests(unittest.TestCase):
    def test_founder_backtest_unchanged(self):
        settings = Settings()
        if not settings.db_path.exists():
            self.skipTest("seeded signal_scout.db not present")
        container = Container(settings)
        try:
            report = container.backtest.run()
        finally:
            container.db.close()
        if report["founders_total"] == 0:
            self.skipTest("no founders seeded")
        self.assertEqual(report["recall_pct"], 70.0)
        self.assertEqual(report["false_positive_pct"], 1.7)


# -- tiny HTTP doubles (no real network) ------------------------------------


class _Resp:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload


class _DummySession:
    def __init__(self):
        self.headers = {}
        self.get_response = _Resp(404, {})
        self.post_response = _Resp(200, {"data": []})
        self.last_post_json = None
        self.post_calls = 0

    def get(self, url, **kwargs):
        return self.get_response

    def post(self, url, **kwargs):
        self.post_calls += 1
        self.last_post_json = kwargs.get("json")
        return self.post_response


def _query():
    return EnrichmentQuery(name="Ada Lovelace", school="MIT", github_username="ada")


if __name__ == "__main__":
    unittest.main()
