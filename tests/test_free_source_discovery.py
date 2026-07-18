"""Phase 3: free-source (fellowship/competition) lead extraction and
resolution. Reuses ChainTestBase/FakeProvider from test_provider_diversification
so budget/cache/dedupe setup stays identical to the provider-search tests.
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_provider_diversification import ChainTestBase, FakeProvider, make_result

from backend.enrichment.provider_enricher import ProviderEnricher
from backend.scrapers.competition_scraper import CompetitionScraper
from backend.scrapers.config_scraper import ConfigSourceScraper
from backend.scrapers.fellowship_scraper import FellowshipScraper
from backend.scrapers.lead_extraction import extract_leads
from backend.scrapers.resolve import LeadResolver, RawLead


class ExtractLeadsTests(unittest.TestCase):
    def test_name_near_linkedin_link_becomes_a_lead(self):
        html = """
        <div class="fellow">
          <h3>Ada Lovelace</h3>
          <a href="https://linkedin.com/in/adalovelace">LinkedIn</a>
        </div>
        """
        leads = extract_leads(html, source="z_fellows", source_url="https://zfellows.com")
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].name, "Ada Lovelace")
        self.assertEqual(leads[0].linkedin_url, "https://linkedin.com/in/adalovelace")
        self.assertEqual(leads[0].source, "z_fellows")

    def test_name_near_github_link_captures_username(self):
        html = '<p>Grace Hopper — <a href="https://github.com/gracehopper">code</a></p>'
        leads = extract_leads(html, source="usaco")
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].github_username, "gracehopper")

    def test_name_with_no_nearby_link_is_dropped(self):
        html = "<p>Just some prose about Katie Bouman with no contact info nearby.</p>"
        leads = extract_leads(html, source="imo")
        self.assertEqual(leads, [])

    def test_empty_html_returns_no_leads(self):
        self.assertEqual(extract_leads("", source="ioi"), [])

    def test_duplicate_names_on_one_page_are_deduped(self):
        html = """
        <a href="https://linkedin.com/in/ada1">L</a> Ada Lovelace
        <a href="https://linkedin.com/in/ada2">L</a> Ada Lovelace
        """
        leads = extract_leads(html, source="putnam")
        self.assertEqual(len(leads), 1)

    def test_max_leads_is_respected(self):
        html = "".join(
            f'<p>Person {chr(65 + i)} Number — <a href="https://github.com/person{i}">gh</a></p>'
            for i in range(10)
        )
        leads = extract_leads(html, source="regeneron_sts", max_leads=3)
        self.assertLessEqual(len(leads), 3)


class ConfigScraperTests(unittest.TestCase):
    def _sources_file(self, sources) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "sources.json"
        import json
        path.write_text(json.dumps({"sources": sources}))
        return path

    def test_fellowship_scraper_is_a_config_source_scraper(self):
        self.assertTrue(issubclass(FellowshipScraper, ConfigSourceScraper))
        self.assertEqual(FellowshipScraper.name, "fellowship")

    def test_competition_scraper_is_a_config_source_scraper(self):
        self.assertTrue(issubclass(CompetitionScraper, ConfigSourceScraper))
        self.assertEqual(CompetitionScraper.name, "competition")

    def test_scrape_extracts_leads_from_configured_sources(self):
        class _Resp:
            status_code = 200
            text = '<p>Ada Lovelace <a href="https://linkedin.com/in/ada">in</a></p>'

        class _Session:
            headers = {}

            def get(self, url, timeout=15):
                return _Resp()

        sources_file = self._sources_file([{"id": "z_fellows", "url": "https://zfellows.com"}])
        scraper = FellowshipScraper(sources_file, session=_Session())

        leads = scraper.scrape()
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].source, "z_fellows")

    def test_missing_sources_file_is_fail_soft(self):
        scraper = FellowshipScraper(Path("/nonexistent/sources.json"))
        self.assertEqual(scraper.scrape(), [])

    def test_bad_http_status_is_fail_soft(self):
        class _Resp:
            status_code = 500
            text = "error"

        class _Session:
            headers = {}

            def get(self, url, timeout=15):
                return _Resp()

        sources_file = self._sources_file([{"id": "usaco", "url": "https://usaco.org"}])
        scraper = CompetitionScraper(sources_file, session=_Session())
        self.assertEqual(scraper.scrape(), [])


class LeadResolverTests(ChainTestBase):
    def _resolver(self, providers) -> LeadResolver:
        enricher = ProviderEnricher(providers, self.signals, self.cache, self.budget())
        return LeadResolver(self.persons, self.identities, enricher)

    def test_lead_matching_existing_candidate_by_linkedin_is_skipped(self):
        existing = self.save_person(name="Ada Lovelace", linkedin_url="https://linkedin.com/in/ada")
        self.identities.link("pdl", "p1", existing.id, "https://linkedin.com/in/ada", "2024-01-01")
        pdl = FakeProvider("pdl")  # never called — dedupe happens first
        resolver = self._resolver([pdl])

        result = resolver.resolve([
            RawLead(name="Ada Lovelace", source="z_fellows", linkedin_url="https://linkedin.com/in/ada"),
        ])
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.created, [])
        self.assertEqual(pdl.enrich_calls, 0)
        self.assertEqual(len(self.persons.all("discovery")), 1)  # no duplicate inserted

    def test_lead_matching_existing_candidate_by_name_and_school_is_skipped(self):
        self.save_person(name="Grace Hopper", school="Yale University")
        pdl = FakeProvider("pdl")
        resolver = self._resolver([pdl])

        result = resolver.resolve([
            RawLead(name="Grace Hopper", source="thiel_fellowship", school="Yale University"),
        ])
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(pdl.enrich_calls, 0)

    def test_unresolved_lead_tries_pdl_identify_before_giving_up(self):
        pdl = FakeProvider("pdl", enrich_result=make_result("pdl", "p1", name="Katie Bouman"))
        resolver = self._resolver([pdl])

        result = resolver.resolve([
            RawLead(name="Katie Bouman", source="neo_scholars", school="MIT"),
        ])
        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].discovery_source, "neo_scholars")
        self.assertEqual(pdl.enrich_calls, 1)
        self.assertEqual(len(self.persons.all("discovery")), 1)

    def test_pdl_identify_miss_leaves_lead_unresolved_and_uninserted(self):
        pdl = FakeProvider("pdl", enrich_result=None)  # definitive miss
        resolver = self._resolver([pdl])

        result = resolver.resolve([RawLead(name="Nobody Findable", source="1517_fund")])
        self.assertEqual(result.created, [])
        self.assertEqual(len(result.unresolved), 1)
        self.assertEqual(len(self.persons.all("discovery")), 0)  # never inserted

    def test_provider_outage_leaves_lead_unresolved_without_billing(self):
        pdl = FakeProvider("pdl", error="HTTP 500")
        resolver = self._resolver([pdl])

        result = resolver.resolve([RawLead(name="Someone Here", source="contrary_talent")])
        self.assertEqual(result.created, [])
        self.assertEqual(len(result.unresolved), 1)
        self.assertEqual(
            self.usage.count_for("pdl", datetime.now(timezone.utc).date().isoformat()), 0,
        )  # no credit spent on a transient failure


if __name__ == "__main__":
    unittest.main()
