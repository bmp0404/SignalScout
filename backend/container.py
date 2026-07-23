"""DI root: wires Database -> repositories -> services. No global singletons;
main.py and scripts construct exactly one Container each."""

from backend.config import Settings, load_settings
from backend.db.database import Database
from backend.db.repositories.candidate_reviews import CandidateReviewRepository
from backend.db.repositories.concentrations import ConcentrationRepository
from backend.db.repositories.digests import DigestRepository
from backend.db.repositories.discovery_recipes import DiscoveryRecipeRepository
from backend.db.repositories.enrichment import EnrichmentCacheRepository, EnrichmentUsageRepository
from backend.db.repositories.graph_edges import GraphEdgeRepository
from backend.db.repositories.page_views import PageViewRepository
from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.provider_identities import ProviderIdentityRepository
from backend.db.repositories.signals import SignalRepository
from backend.db.repositories.subscriptions import (
    DigestSendRepository,
    FeedbackRepository,
    SubscriberRepository,
)
from backend.digest.generator import DigestGenerator
from backend.digest.sender import ResendSender
from backend.discovery.concentrations import ConcentrationDetector
from backend.discovery.entity_resolution import EntityResolver
from backend.discovery.openalex_labs import OpenAlexLabExpander
from backend.discovery.provider_expansion import ProviderExpander
from backend.discovery.recipe_seeds import INITIAL_RECIPES
from backend.enrichment.budgets import ProviderBudget
from backend.enrichment.contacts import ContactEnricher
from backend.enrichment.locations import LocationResolver
from backend.enrichment.provider_enricher import (
    ProviderEnricher,
    build_provider_chain,
    build_search_providers,
)
from backend.scoring.backtest import BacktestRunner
from backend.scoring.engine import ScoringEngine
from backend.scrapers.competition_scraper import CompetitionScraper
from backend.scrapers.fellowship_scraper import FellowshipScraper
from backend.scrapers.openalex import OpenAlexClient, OpenAlexScraper
from backend.scrapers.producthunt_scraper import ProductHuntScraper
from backend.scrapers.resolve import LeadResolver
from backend.security.email_actions import EmailActionSigner
from backend.services.candidate_service import CandidateService
from backend.services.candidate_review import CandidateReviewService
from backend.services.discovery_job import DiscoveryJobManager
from backend.services.discovery_recipe_service import DiscoveryRecipeService
from backend.services.subscriber_digest import SubscriberDigestService


class Container:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.settings.validate_security()
        self.db = Database(self.settings.db_path, database_url=self.settings.database_url)
        self.db.init_schema()

        self.persons = PersonRepository(self.db)
        self.signals = SignalRepository(self.db)
        self.candidate_reviews = CandidateReviewRepository(self.db)
        self.edges = GraphEdgeRepository(self.db)
        self.concentrations = ConcentrationRepository(self.db)
        self.digests = DigestRepository(self.db)
        self.page_views = PageViewRepository(self.db)
        self.subscribers = SubscriberRepository(self.db)
        self.digest_sends = DigestSendRepository(self.db)
        self.feedback = FeedbackRepository(self.db)

        self.engine = ScoringEngine(self.settings.recency_window_days)
        self.resolver = EntityResolver(self.persons, self.signals, self.edges)
        self.contact_enricher = ContactEnricher()
        self.location_resolver = LocationResolver(self.settings.school_locations_file)
        self.enrichment_cache = EnrichmentCacheRepository(self.db)
        self.enrichment_usage = EnrichmentUsageRepository(self.db)
        self.provider_identities = ProviderIdentityRepository(self.db)
        self.provider_chain = build_provider_chain(self.settings)
        # Search/lead lane: enrichment chain + search-only sources (Exa). Kept
        # separate so Exa (no one-person enrich) never touches the enrich lane.
        self.search_providers = build_search_providers(self.settings)
        self.provider_budget = ProviderBudget(self.enrichment_usage, self.settings)
        self.provider_enricher = ProviderEnricher(
            self.provider_chain, self.signals, self.enrichment_cache, self.provider_budget,
        )
        self.provider_expander = ProviderExpander(
            self.search_providers, self.persons, self.provider_identities,
            self.provider_enricher, self.provider_budget,
            self.settings.provider_discovery_filters_file,
        )
        self.fellowship_scraper = FellowshipScraper(self.settings.fellowship_sources_file)
        self.competition_scraper = CompetitionScraper(self.settings.competition_sources_file)
        self.producthunt_scraper = ProductHuntScraper(self.settings.producthunt_sources_file)
        self.lead_resolver = LeadResolver(
            self.persons, self.provider_identities, self.provider_enricher,
        )
        self.discovery_recipes = DiscoveryRecipeRepository(self.db)
        self.discovery_recipes.seed(INITIAL_RECIPES)
        self.discovery_recipes.approve_pending_seeds({recipe.id for recipe in INITIAL_RECIPES})
        self.discovery_recipe_service = DiscoveryRecipeService(
            self.discovery_recipes, self.provider_identities, self.provider_expander,
            self.provider_budget, self.enrichment_usage, self.persons,
        )
        self.openalex_client = OpenAlexClient(mailto=self.settings.openalex_mailto)
        self.openalex_scraper = OpenAlexScraper(self.openalex_client)
        self.openalex_lab_expander = OpenAlexLabExpander(
            self.persons, self.signals, self.edges,
            self.openalex_client, self.settings.openalex_targets_file,
        )
        self.candidate_service = CandidateService(
            self.persons,
            self.signals,
            self.edges,
            self.engine,
            self.settings.flag_threshold,
            self.candidate_reviews,
        )
        self.discovery_recipe_service.candidate_service = self.candidate_service
        self.candidate_review_service = CandidateReviewService(
            self.candidate_reviews,
            self.persons,
            self.signals,
        )
        self.backtest = BacktestRunner(
            self.persons, self.signals, self.edges, self.engine, self.settings.flag_threshold
        )
        self.concentration_detector = ConcentrationDetector(self.concentrations)
        self.digest_generator = DigestGenerator(
            self.candidate_service, self.digests, self.settings.out_dir, self.settings.digest_size
        )
        self.email_sender = ResendSender(
            self.settings.resend_api_key,
            self.settings.digest_from_email,
        )
        self.email_action_signer = EmailActionSigner(
            self.settings.cron_secret
        )
        self.subscriber_digest = SubscriberDigestService(
            self.subscribers,
            self.digest_sends,
            self.candidate_service,
            self.email_sender,
            self.settings.public_base_url,
            self.email_action_signer,
            size=10,
        )
        self.discovery_job = DiscoveryJobManager(
            self.settings, container_factory=lambda: Container(self.settings)
        )
