import tempfile
import unittest
from datetime import date
from pathlib import Path

from backend.config import Settings
from backend.container import Container
from backend.discovery.collab_expansion import CollaborationExpander
from backend.discovery.fellowship_seeds import FellowshipSeedLoader
from backend.discovery.graph_expansion import GraphExpander
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.scoring.engine import ScoringEngine
from backend.scrapers.github_scraper import GithubClient, GithubScraper
from backend.scrapers.semantic_scholar import SemanticScholarScraper


class FakeGithubClient:
    def __init__(self):
        self.surface_calls = []

    def following(self, username, limit=100):
        return []

    def followers(self, username, limit=100):
        return []

    def repos(self, username):
        if username == "seed":
            return [
                {"name": "niche", "full_name": "seed/niche", "fork": False, "stargazers_count": 50},
                {"name": "popular", "full_name": "seed/popular", "fork": False, "stargazers_count": 5000},
            ]
        return [
            {"name": "one", "created_at": "2022-01-01", "stargazers_count": 0},
            {"name": "two", "created_at": "2022-01-01", "stargazers_count": 0},
            {"name": "three", "created_at": "2022-01-01", "stargazers_count": 0},
        ]

    def repo_contributors(self, owner, repo, limit=30):
        return []

    def repo_stargazers(self, owner, repo, limit=20):
        self.surface_calls.append(("stars", repo, limit))
        return [{"login": "candidate"}]

    def repo_forkers(self, owner, repo, limit=15):
        self.surface_calls.append(("forks", repo, limit))
        return [{"owner": {"login": "candidate"}}]

    def repo_issues(self, owner, repo, limit=20):
        self.surface_calls.append(("issues", repo, limit))
        return [
            {
                "number": 7,
                "user": {"login": "candidate"},
                "pull_request": {"url": "api"},
                "html_url": "https://github.com/seed/niche/pull/7",
            }
        ]

    def user_orgs(self, username):
        return []

    def org_members(self, org, limit=30):
        return []

    def user(self, username):
        return {
            "login": username,
            "name": "Real Candidate",
            "type": "User",
            "followers": 12,
            "created_at": "2022-01-01T00:00:00Z",
            "html_url": f"https://github.com/{username}",
            "bio": "student builder",
        }

    def social_accounts(self, username):
        return []


class FakeDevpost:
    def github_username(self, username):
        return "candidate"


class FakeScholarClient:
    def search_author(self, name):
        if name == "Research Candidate":
            return [
                {
                    "authorId": "author-1",
                    "name": "Research Candidate",
                    "paperCount": 2,
                    "url": "https://www.semanticscholar.org/author/author-1",
                }
            ]
        if name == "Citing Author":
            return [
                {
                    "authorId": "author-2",
                    "name": "Citing Author",
                    "paperCount": 1,
                    "url": "https://www.semanticscholar.org/author/author-2",
                }
            ]
        return []

    def author_papers(self, author_id, limit=10):
        return [
            {
                "paperId": "paper-1",
                "title": "A Verified Paper",
                "year": 2024,
                "url": "https://www.semanticscholar.org/paper/paper-1",
                "authors": [
                    {"name": "Research Candidate"},
                    {"name": "Other Author"},
                ],
            }
        ]

    def paper_citations(self, paper_id, limit=10):
        return [
            {
                "citingPaper": {
                    "title": "A Later Paper",
                    "year": 2025,
                    "authors": [{"name": "Citing Author"}],
                }
            }
        ]


class RecordingSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return type("Response", (), {"status_code": 200, "headers": {}, "json": lambda self: []})()


class GraphExpansionDeepeningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            db_path=Path(self.temp_dir.name) / "test.db",
            database_url="",
            out_dir=Path(self.temp_dir.name) / "out",
        )
        self.container = Container(self.settings)
        self.container.db.init_schema()

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def test_github_client_uses_direct_capped_rest_endpoints(self):
        client = GithubClient("fixture-token")
        session = RecordingSession()
        client.session = session
        client.repo_stargazers("owner", "repo", limit=4)
        client.repo_forkers("owner", "repo", limit=5)
        client.repo_issues("owner", "repo", limit=6)

        self.assertEqual(
            [call[0].split("api.github.com")[-1] for call in session.calls],
            [
                "/repos/owner/repo/stargazers",
                "/repos/owner/repo/forks",
                "/repos/owner/repo/issues",
            ],
        )
        self.assertEqual(session.calls[2][1]["state"], "all")
        self.assertEqual([call[1]["per_page"] for call in session.calls], [4, 5, 6])

    def test_graph_expansion_adds_precise_niche_repo_surfaces(self):
        seed = Person(name="Seed Founder", cohort="founder", github_username="seed")
        self.container.persons.save(seed)
        client = FakeGithubClient()
        expander = GraphExpander(
            GithubScraper(client, []), self.container.persons, self.container.edges
        )
        discovered = expander.expand(
            ["seed"],
            repos_per_seed=2,
            contributors_per_repo=0,
            org_members_per_seed=0,
            stargazers_per_repo=3,
            forkers_per_repo=4,
            interactions_per_repo=5,
            niche_repo_star_ceiling=2000,
        )

        self.assertEqual([person.github_username for person in discovered], ["candidate"])
        saved_edges = self.container.edges.all()
        edge_types = {edge.edge_type for edge in saved_edges}
        self.assertEqual(
            edge_types, {"starred_repo", "forked_repo", "issue_pr_interaction"}
        )
        self.assertNotIn("mutual_star", edge_types)
        candidate = discovered[0]
        self.assertTrue(all(edge.source_person_id == candidate.id for edge in saved_edges))
        self.assertTrue(all(edge.target_person_id == seed.id for edge in saved_edges))
        self.assertEqual(
            client.surface_calls,
            [("stars", "niche", 3), ("forks", "niche", 4), ("issues", "niche", 5)],
        )

    def test_collaboration_promotes_devpost_and_scholar_with_repeat_metadata(self):
        seed = Person(name="Seed Founder", cohort="founder", github_username="seed")
        self.container.persons.save(seed)
        edges = []
        for project in ("Project One", "Project Two"):
            edge = GraphEdge(
                source_name=seed.name,
                target_name="Devpost Candidate",
                edge_type="hackathon_teammate",
                observed_date="2024-01-01",
                source="devpost",
                metadata={"project": project, "devpost_username": "dev-user"},
            )
            edge.source_person_id = seed.id
            edges.append(edge)
        scholar_edge = GraphEdge(
            source_name=seed.name,
            target_name="Research Candidate",
            edge_type="co_author",
            observed_date="2024-01-01",
            source="semantic_scholar",
            metadata={"paper": "A Verified Paper"},
        )
        scholar_edge.source_person_id = seed.id
        edges.append(scholar_edge)
        self.container.edges.save_many(edges)

        github = GithubScraper(FakeGithubClient(), [])
        scholar = SemanticScholarScraper(FakeScholarClient())
        result = CollaborationExpander(
            self.container.persons,
            self.container.signals,
            self.container.edges,
            github,
            FakeDevpost(),
            scholar,
        ).expand(max_promotions=15)

        self.assertEqual(len(result.promoted), 2)
        research = self.container.persons.find_by_name("Research Candidate")
        self.assertIsNotNone(research)
        self.assertIsNone(research.github_username)
        self.assertTrue(self.container.signals.for_person(research.id))
        devpost_edges = [
            edge
            for edge in self.container.edges.all()
            if edge.edge_type == "hackathon_teammate"
        ]
        self.assertTrue(all(edge.metadata["repeat"] == 2 for edge in devpost_edges))
        self.assertTrue(all(edge.target_person_id for edge in self.container.edges.all()))

    def test_citation_promotes_citing_author(self):
        seed = Person(name="Research Candidate", cohort="founder")
        self.container.persons.save(seed)
        scholar = SemanticScholarScraper(FakeScholarClient())

        # Founder cohort: no cited_paper signal, so founder pre-breakout scores
        # (and the backtest reference scale derived from them) are untouched.
        signals, edges = scholar.collect_citations(seed)
        self.assertEqual(signals, [])
        self.assertEqual(len(edges), 1)
        citation_edge = edges[0]
        self.assertEqual(citation_edge.edge_type, "paper_citation")
        self.assertEqual(citation_edge.source_name, seed.name)
        self.assertEqual(citation_edge.target_name, "Citing Author")
        self.assertEqual(citation_edge.source, "semantic_scholar")
        citation_edge.source_person_id = seed.id
        self.container.edges.save_many(edges)

        result = CollaborationExpander(
            self.container.persons,
            self.container.signals,
            self.container.edges,
            GithubScraper(FakeGithubClient(), []),
            FakeDevpost(),
            scholar,
        ).expand(max_promotions=15)

        self.assertEqual(len(result.promoted), 1)
        citer = self.container.persons.find_by_name("Citing Author")
        self.assertIsNotNone(citer)
        saved_edge = self.container.edges.all()[0]
        self.assertEqual(saved_edge.target_person_id, citer.id)

    def test_cited_paper_signal_only_for_discovery_cohort(self):
        scholar = SemanticScholarScraper(FakeScholarClient())
        founder = Person(name="Research Candidate", cohort="founder")
        discovery = Person(name="Research Candidate", cohort="discovery")

        founder_signals, _ = scholar.collect_citations(founder)
        discovery_signals, _ = scholar.collect_citations(discovery)

        self.assertEqual(founder_signals, [])
        self.assertEqual(len(discovery_signals), 1)
        self.assertEqual(discovery_signals[0].signal_type, "cited_paper")
        self.assertEqual(discovery_signals[0].raw_data["citation_count"], 1)

    def test_discovery_scoring_compounds_distinct_surfaces_but_founders_do_not(self):
        seed = Person(name="Seed", cohort="founder")
        discovery = Person(name="Discovery", cohort="discovery")
        edges = []
        for edge_type in ("github_follows", "forked_repo", "issue_pr_interaction"):
            edge = GraphEdge(
                source_name=seed.name,
                target_name=discovery.name,
                edge_type=edge_type,
                observed_date="2025-01-01",
                source="fixture",
            )
            edge.source_person_id = seed.id
            edge.target_person_id = discovery.id
            edges.append(edge)
        engine = ScoringEngine()
        discovery_signal = engine.connection_signal(
            discovery, edges, {seed.id}, date(2026, 1, 1)
        )
        discovery.cohort = "founder"
        founder_signal = engine.connection_signal(
            discovery, edges, {seed.id}, date(2026, 1, 1)
        )

        self.assertEqual(discovery_signal.metadata["surface_bonus"], 0.2)
        self.assertEqual(founder_signal.metadata["surface_bonus"], 0.0)
        self.assertGreater(discovery_signal.signal_strength, founder_signal.signal_strength)

        repeat_edge = GraphEdge(
            source_name=seed.name,
            target_name=discovery.name,
            edge_type="hackathon_teammate",
            observed_date="2025-01-01",
            source="fixture",
            metadata={"repeat": 2},
        )
        repeat_edge.source_person_id = seed.id
        repeat_edge.target_person_id = discovery.id
        repeat_signal = engine.connection_signal(
            discovery, [repeat_edge], {seed.id}, date(2026, 1, 1)
        )
        self.assertEqual(repeat_signal.metadata["best_quality"], 1.0)

    def test_fellowship_seed_loader_is_opt_in_and_idempotent(self):
        loader = FellowshipSeedLoader(
            self.container.persons,
            self.container.edges,
            self.settings.fellowship_alumni_file,
        )
        usernames = loader.load()
        first_edges = self.container.edges.all()
        loader.load()

        self.assertIn("Sanger2000", usernames)
        self.assertEqual(len(first_edges), 1)
        self.assertEqual(len(self.container.edges.all()), 1)
        self.assertEqual(first_edges[0].edge_type, "fellowship_cohort")
        self.assertEqual(
            self.container.persons.find_by_github("Sanger2000").cohort, "seed"
        )


if __name__ == "__main__":
    unittest.main()
