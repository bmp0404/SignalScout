"""Initial discovery recipes, seeded (not overwritten) into discovery_recipes
on container init. Filter keys map to the allowlisted columns in
backend/enrichment/providers/pdl.py and coresignal.py — unsupported keys are
silently dropped by ProviderExpander._effective_filters.
"""

from backend.domain.discovery_recipe import DiscoveryRecipe

PDL_RECIPES = [
    DiscoveryRecipe(
        id="young_founders",
        name="Young founders (PDL)",
        provider="pdl",
        query_type="founder",
        filters={
            "birth_year_gte": 2000,
            "title": ["founder", "co-founder", "ceo"],
            "company_size": "1-10",
            "country": "united states",
        },
        default_limit=25,
        frequency="weekly",
        approval_state="approved",
    ),
    DiscoveryRecipe(
        id="recent_founder_starts",
        name="Recent founder starts (PDL)",
        provider="pdl",
        query_type="founder",
        filters={
            "title": ["founder", "co-founder"],
            "birth_year_gte": 2000,
        },
        relative_filters={"job_start_date_gte": 30},
        default_limit=25,
        frequency="weekly",
        approval_state="approved",
    ),
    DiscoveryRecipe(
        id="top_school_technical",
        name="Top-school technical (PDL)",
        provider="pdl",
        query_type="student_technical",
        filters={
            "school": [
                "massachusetts institute of technology",
                "stanford university",
                "carnegie mellon university",
                "georgia institute of technology",
                "university of california, berkeley",
                "california institute of technology",
                "university of texas at austin",
            ],
            "birth_year_gte": 2000,
            "skill": ["artificial intelligence", "machine learning", "deep learning"],
        },
        default_limit=25,
        frequency="biweekly",
        approval_state="approved",
    ),
]

CORESIGNAL_RECIPES = [
    DiscoveryRecipe(
        id="faang_to_startup",
        name="FAANG to startup (Coresignal)",
        provider="coresignal",
        query_type="founder",
        filters={
            "title": "Founder",
            "previous_company": ["Google", "Meta", "Apple", "Amazon", "Microsoft"],
            "company_size_lte": 20,
        },
        default_limit=20,
        frequency="weekly",
        approval_state="approved",
    ),
    DiscoveryRecipe(
        id="young_ai_founders",
        name="Young AI founders (Coresignal)",
        provider="coresignal",
        query_type="founder",
        filters={
            "title": "Founder",
            "company_size_lte": 10,
            "company_founded_gte": 2024,
            "country": "United States",
        },
        default_limit=20,
        frequency="weekly",
        approval_state="approved",
    ),
    DiscoveryRecipe(
        id="seed_stage_company_first",
        name="Seed-stage company-first (Coresignal)",
        provider="coresignal",
        query_type="company_first",
        # Nested shape (unique to company_first): step-1 company_base filters
        # and step-2 employee_base title filters, applied per matching company.
        filters={
            "company": {"founded_gte": 2024, "employees_count_lte": 10},
            "employee_title": {"title": "Founder"},
        },
        default_limit=20,
        frequency="weekly",
        approval_state="approved",
    ),
]

INITIAL_RECIPES = [*PDL_RECIPES, *CORESIGNAL_RECIPES]
