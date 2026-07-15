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
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    data_dir: Path = DATA_DIR
    seed_signals_dir: Path = DATA_DIR / "seed_signals"
    out_dir: Path = OUT_DIR

    ground_truth_file: Path = DATA_DIR / "ground_truth.json"
    seed_accounts_file: Path = DATA_DIR / "seed_accounts.json"
    school_locations_file: Path = DATA_DIR / "school_locations.json"

    # Scoring / backtest knobs (tuned against the backtest, see backend/scoring/weights.py)
    flag_threshold: float = 40.0  # normalized 0-100 score at which a candidate is "flagged"
    recency_window_days: int = 730
    digest_size: int = 8

    # Live "Run Discovery" knobs — kept small so an on-camera run finishes in ~1-2 min.
    discovery_seed_limit: int = field(default_factory=lambda: int(os.environ.get("DISCOVERY_SEED_LIMIT", "4")))
    discovery_max_per_seed: int = field(default_factory=lambda: int(os.environ.get("DISCOVERY_MAX_PER_SEED", "30")))

    # Licensed enrichment (Phase 1). Missing key -> provider None -> enrichment no-ops.
    enrichment_provider: str = field(default_factory=lambda: os.environ.get("ENRICHMENT_PROVIDER", "pdl"))
    pdl_api_key: str = field(default_factory=lambda: os.environ.get("PDL_API_KEY", ""))
    coresignal_api_key: str = field(default_factory=lambda: os.environ.get("CORESIGNAL_API_KEY", ""))
    daily_enrichment_budget: int = field(default_factory=lambda: int(os.environ.get("DAILY_ENRICHMENT_BUDGET", "100")))


def load_settings() -> Settings:
    return Settings()
