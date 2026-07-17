import json
import tempfile
import unittest
from pathlib import Path

from backend.config import Settings
from backend.container import Container
from backend.discovery.collab_expansion import CollaborationExpander
from backend.discovery.openalex_labs import OpenAlexLabExpander
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.scrapers.github_scraper import GithubScraper
from backend.scrapers.openalex import OpenAlexScraper
from backend.scrapers.semantic_scholar import SemanticScholarScraper


class FakeOpenAlexClient:
    """Canned OpenAlex responses: one early-career author ("New Researcher",
    low works_count) and one established author ("Senior Professor", high
    works_count) co-authoring a lab paper — exercises the early-career gate."""

    def __init__(self):
        self._authors = {
            "A1": {"id": "A1", "display_name": "New Researcher", "works_count": 5, "cited_by_count": 20},
            "A2": {"id": "A2", "display_name": "Senior Professor", "works_count": 300, "cited_by_count": 9000},
            "A3": {"id": "A3", "display_name": "Collaborator Name", "works_count": 4, "cited_by_count": 10},
        }

    def works_by_affiliation(self, affiliation, from_date=None, limit=25, institution_id=None):
        return [
            {
                "id": "https://openalex.org/W1",
                "title": "A Lab Paper",
                "publication_year": 2024,
                "publication_date": "2024-05-01",
                "authorships": [
                    {"author": {"id": "A1", "display_name": "New Researcher"}},
                    {"author": {"id": "A2", "display_name": "Senior Professor"}},
                ],
            }
        ]

    def author(self, author_id):
        return self._authors.get(author_id)

    def search_author(self, name):
        return [a for a in self._authors.values() if a["display_name"] == name]

    def author_works(self, author_id, limit=10):
        primary = self._authors.get(author_id, {"display_name": "Unknown"})
        return [
            {
                "id": "https://openalex.org/W2",
                "title": "Another Paper",
                "publication_year": 2024,
                "publication_date": "2024-06-01",
                "authorships": [
                    {"author": {"id": author_id, "display_name": primary["display_name"]}},
                    {"author": {"id": "AX", "display_name": "Someone Else"}},
                ],
            }
        ]


class FakeGithubClient:
    def user(self, username):
        return None

    def repos(self, username):
        return []


class FakeDevpost:
    def github_username(self, username):
        return "candidate"


class FakeScholarClient:
    def search_author(self, name):
        return []

    def author_papers(self, author_id, limit=10):
        return []


class OpenAlexDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            db_path=Path(self.temp_dir.name) / "test.db",
            database_url="",
            out_dir=Path(self.temp_dir.name) / "out",
        )
        self.container = Container(self.settings)
        self.container.db.init_schema()
        self.targets_file = Path(self.temp_dir.name) / "openalex_targets.json"
        self.targets_file.write_text(json.dumps({
            "targets": [{"school": "MIT", "affiliation": "MIT CSAIL", "area": "AI Research"}]
        }))

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_lab_lead_gen_gates_early_career_and_skips_senior(self):
        client = FakeOpenAlexClient()
        expander = OpenAlexLabExpander(
            self.container.persons, self.container.signals, self.container.edges,
            client, self.targets_file,
        )
        result = expander.expand(max_new=5)

        self.assertEqual(len(result.created), 1)
        created = result.created[0]
        self.assertEqual(created.name, "New Researcher")
        self.assertEqual(created.cohort, "discovery")
        self.assertEqual(created.discovery_origin, "openalex_lab")
        self.assertEqual(created.school, "MIT")
        self.assertIsNone(self.container.persons.find_by_name("Senior Professor"))
        self.assertTrue(self.container.signals.for_person(created.id))

        coauthor_edges = [e for e in self.container.edges.all() if e.edge_type == "co_author"]
        self.assertTrue(any(e.target_name == "Senior Professor" for e in coauthor_edges))

    def test_lab_leads_form_a_school_concentration(self):
        people = [Person(name=f"Researcher {i}", cohort="discovery", school="MIT") for i in range(3)]
        for person in people:
            self.container.persons.save(person)

        concentrations = self.container.concentration_detector.compute(people)

        self.assertEqual(len(concentrations), 1)
        self.assertEqual(concentrations[0].kind, "school")
        self.assertEqual(concentrations[0].key, "MIT")
        self.assertEqual(concentrations[0].count, 3)

    def test_openalex_co_author_edge_promotes_via_collaboration_expander(self):
        seed = Person(name="Seed Author", cohort="founder")
        self.container.persons.save(seed)
        openalex = OpenAlexScraper(FakeOpenAlexClient())

        edge = GraphEdge(
            source_name=seed.name, target_name="Collaborator Name",
            edge_type="co_author", observed_date="2024-01-01",
            source="openalex", metadata={"paper": "Another Paper"},
        )
        edge.source_person_id = seed.id
        self.container.edges.save_many([edge])

        result = CollaborationExpander(
            self.container.persons, self.container.signals, self.container.edges,
            GithubScraper(FakeGithubClient(), []), FakeDevpost(),
            SemanticScholarScraper(FakeScholarClient()),
            openalex=openalex,
        ).expand(max_promotions=15)

        self.assertEqual(len(result.promoted), 1)
        promoted = self.container.persons.find_by_name("Collaborator Name")
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.discovery_origin, "openalex")
        saved_edge = self.container.edges.all()[0]
        self.assertEqual(saved_edge.target_person_id, promoted.id)


if __name__ == "__main__":
    unittest.main()
