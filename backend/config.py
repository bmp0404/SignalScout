"""Application settings. Everything configurable lives here, read from env with sane defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
OUT_DIR = ROOT_DIR / "out"


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("SIGNAL_SCOUT_DB", ROOT_DIR / "signal_scout.db")))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))  # Postgres when set; SQLite otherwise
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    data_dir: Path = DATA_DIR
    seed_signals_dir: Path = DATA_DIR / "seed_signals"
    out_dir: Path = OUT_DIR

    ground_truth_file: Path = DATA_DIR / "ground_truth.json"
    seed_accounts_file: Path = DATA_DIR / "seed_accounts.json"
    fellowship_alumni_file: Path = DATA_DIR / "fellowship_alumni.json"
    school_locations_file: Path = DATA_DIR / "school_locations.json"
    provider_discovery_filters_file: Path = DATA_DIR / "provider_discovery_filters.json"
    openalex_targets_file: Path = DATA_DIR / "openalex_targets.json"
    fellowship_sources_file: Path = DATA_DIR / "fellowship_sources.json"
    competition_sources_file: Path = DATA_DIR / "competition_sources.json"

    # Scoring / backtest knobs (tuned against the backtest, see backend/scoring/weights.py)
    flag_threshold: float = 40.0  # normalized 0-100 score at which a candidate is "flagged"
    recency_window_days: int = 730
    digest_size: int = 8

    # Live "Run Discovery" knobs — kept small so an on-camera run finishes in ~1-2 min.
    discovery_seed_limit: int = field(default_factory=lambda: int(os.environ.get("DISCOVERY_SEED_LIMIT", "4")))
    discovery_max_per_seed: int = field(default_factory=lambda: int(os.environ.get("DISCOVERY_MAX_PER_SEED", "30")))
    collaboration_promotion_cap: int = field(
        default_factory=lambda: int(os.environ.get("COLLABORATION_PROMOTION_CAP", "15"))
    )
    discovery_include_fellowship_seeds: bool = field(
        default_factory=lambda: os.environ.get(
            "DISCOVERY_INCLUDE_FELLOWSHIP_SEEDS", ""
        ).lower() in ("1", "true", "yes")
    )
    # Curated-lab lead-gen (backend/discovery/openalex_labs.py). Opt-in like the
    # fellowship seeds — off by default so a run doesn't grow scope unexpectedly.
    discovery_include_openalex: bool = field(
        default_factory=lambda: os.environ.get(
            "DISCOVERY_INCLUDE_OPENALEX", ""
        ).lower() in ("1", "true", "yes")
    )
    # OpenAlex "polite pool" contact param — priority routing/higher limits, no key required.
    openalex_mailto: str = field(default_factory=lambda: os.environ.get("OPENALEX_MAILTO", ""))

    # Licensed enrichment. Missing key -> provider absent from chain -> that lane no-ops.
    # The chain is PDL-first, Coresignal-fallback; ENRICHMENT_PROVIDER is retained only as a
    # legacy single-provider hint and no longer disables the other provider.
    enrichment_provider: str = field(default_factory=lambda: os.environ.get("ENRICHMENT_PROVIDER", "pdl"))
    pdl_api_key: str = field(default_factory=lambda: os.environ.get("PDL_API_KEY", ""))
    coresignal_api_key: str = field(default_factory=lambda: os.environ.get("CORESIGNAL_API_KEY", ""))

    # Provider budgets (search-first). PDL free tier is ~100 lookups/month, shared across
    # provider-search discovery and one-person GitHub enrichment. The split reserves a
    # fraction of the monthly cap for the SEARCH lane (the lead discovery source).
    pdl_monthly_cap: int = field(default_factory=lambda: int(os.environ.get("PDL_MONTHLY_CAP", "100")))
    pdl_search_split: float = field(default_factory=lambda: float(os.environ.get("PDL_SEARCH_SPLIT", "0.7")))
    # Max fresh provider lookups a single run/process may spend (guards runaway backfills).
    provider_per_run_cap: int = field(default_factory=lambda: int(os.environ.get("PROVIDER_PER_RUN_CAP", "100")))
    # Coresignal runs its OWN independent search + serves as PDL's no-match fallback; both
    # share this separate daily cap.
    coresignal_daily_cap: int = field(default_factory=lambda: int(os.environ.get("CORESIGNAL_DAILY_CAP", "20")))
    # Legacy global daily counter setting; superseded by provider-scoped budgets above.
    daily_enrichment_budget: int = field(default_factory=lambda: int(os.environ.get("DAILY_ENRICHMENT_BUDGET", "100")))

    # Subscriber digest delivery. Missing Resend credentials keeps delivery in preview mode.
    resend_api_key: str = field(default_factory=lambda: os.environ.get("RESEND_API_KEY", ""))
    digest_from_email: str = field(default_factory=lambda: os.environ.get("DIGEST_FROM_EMAIL", ""))
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000"))
    cron_secret: str = field(default_factory=lambda: os.environ.get("CRON_SECRET", ""))

    # Server-side bearer gate for review, preview, and cost-incurring operator actions.
    environment: str = field(default_factory=lambda: os.environ.get("APP_ENV", "development"))
    admin_secret: str = field(default_factory=lambda: os.environ.get("ADMIN_SECRET", ""))
    owner_test_email: str = field(default_factory=lambda: os.environ.get("OWNER_TEST_EMAIL", ""))

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    def validate_security(self) -> None:
        if self.is_production and not (self.admin_secret or self.cron_secret):
            raise RuntimeError("Production requires ADMIN_SECRET or CRON_SECRET.")


def load_settings() -> Settings:
    return Settings()
