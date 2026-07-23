"""Signal-type weight table (spec §7.1) — hand-tuned, validated against the backtest.

score contribution = signal_strength (0-1, per-record) x weight (per-type, below).
Connection to seed founders is weight 3 per the plan.
"""

WEIGHTS: dict[str, float] = {
    # competition
    "imo_medal": 10.0,
    "ioi_medal": 10.0,
    "usaco_camp": 9.0,
    "usaco_platinum": 8.0,
    "usaco_gold": 6.0,
    "usamo_qualifier": 7.0,
    "aime_qualifier": 4.0,
    "amc_high_score": 3.0,
    "physics_olympiad": 6.0,
    # research
    "regeneron_sts_finalist": 8.0,
    "isef_award": 7.0,
    "science_fair_win": 7.0,
    "cited_paper": 8.0,
    "research_paper": 6.0,
    "co_authored_paper": 7.0,
    # code
    "github_star_project": 7.0,
    "github_early_builder": 5.0,
    "github_prolific": 4.0,
    "shipped_product": 7.0,
    # education (young / pre-breakout signal)
    "student_builder": 5.0,
    # hackathon
    "hackathon_win": 5.0,
    "hackathon_finalist": 3.0,
    # fellowship / debate
    "fellowship_finalist": 6.0,
    "debate_nationals": 4.0,
    # connection (derived from graph_edges, weight 3 per plan)
    "connected_to_seeds": 3.0,
    # licensed enrichment (Phase 1) — discovery cohort only, never founders,
    # so the backtest's pre-breakout founder scores are untouched.
    "linkedin_created_recently": 8.0,  # brand-new profile = about to do something
    "education_signal": 4.0,
    "job_change": 4.0,
    # semantic web discovery (Exa) — a labeled web match, weaker than dated
    # provider evidence; enough to surface a real lead into the review queue.
    "web_presence": 3.0,
}

DEFAULT_WEIGHT = 3.0

DIVERSITY_BONUS_PER_CATEGORY = 0.15  # multiplier: 1 + 0.15 * (distinct categories - 1)
RECENCY_BONUS_PER_SIGNAL = 0.1       # additive raw points per signal inside the recency window
RECENCY_BONUS_CAP = 5                # at most 5 signals count toward the recency bonus


def weight_for(signal_type: str) -> float:
    return WEIGHTS.get(signal_type, DEFAULT_WEIGHT)


def age_factor(graduation_year: int | None, as_of_year: int) -> float:
    """Younger = stronger prior. Approximates age from HS/college graduation year (spec §7.2)."""
    if graduation_year is None:
        return 1.0
    approx_age = 18 + (as_of_year - graduation_year)
    if approx_age < 18:
        return 1.4
    if approx_age < 20:
        return 1.2
    return 1.0
