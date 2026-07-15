-- Signal Scout SQLite schema. UUIDs, dates, and JSON stored as TEXT (spec §13).

CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    cohort TEXT NOT NULL DEFAULT 'unknown',
    github_username TEXT,
    twitter_handle TEXT,
    linkedin_url TEXT,
    email TEXT,
    personal_site TEXT,
    contact_info TEXT NOT NULL DEFAULT '{}',
    school TEXT,
    graduation_year INTEGER,
    origin_location TEXT,
    current_location TEXT,
    region TEXT,
    fellowship TEXT,
    breakout_date TEXT,
    area TEXT,
    thesis TEXT,
    score REAL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    discovery_origin TEXT,
    evidence_tier TEXT,
    review_required INTEGER NOT NULL DEFAULT 0,
    enrichment_status TEXT,
    enrichment_provider TEXT,
    enrichment_updated_at TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_persons_github ON persons(github_username);
CREATE INDEX IF NOT EXISTS idx_persons_cohort ON persons(cohort);

-- Human launch review is separate from machine/provider review metadata.
-- approved_at is preserved across edits while approved and defines launch newness.
CREATE TABLE IF NOT EXISTS candidate_reviews (
    person_id TEXT PRIMARY KEY REFERENCES persons(id),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'approved', 'rejected')),
    why_now TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    source_bucket TEXT NOT NULL DEFAULT ''
        CHECK (source_bucket IN ('', 'github_cross_source', 'provider_discovered', 'manual_public')),
    contactable INTEGER NOT NULL DEFAULT 0,
    primary_evidence_url TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT '',
    approved_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_reviews_launch
    ON candidate_reviews(state, contactable, approved_at);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    person_id TEXT REFERENCES persons(id),
    person_name TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_category TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    signal_strength REAL NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    raw_data TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_signals_person ON signals(person_id);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);

CREATE TABLE IF NOT EXISTS graph_edges (
    id TEXT PRIMARY KEY,
    source_person_id TEXT REFERENCES persons(id),
    target_person_id TEXT REFERENCES persons(id),
    source_name TEXT NOT NULL,
    target_name TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    source TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_person_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_person_id);

CREATE TABLE IF NOT EXISTS concentrations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    count INTEGER NOT NULL,
    person_ids TEXT NOT NULL DEFAULT '[]',
    person_names TEXT NOT NULL DEFAULT '[]',
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    subject TEXT NOT NULL,
    entries TEXT NOT NULL DEFAULT '[]',
    html TEXT NOT NULL DEFAULT ''
);

-- Licensed-enrichment guardrails (Phase 1). Cache rows (including misses) are
-- authoritative for 30 days — never re-fetch a person inside the TTL.
CREATE TABLE IF NOT EXISTS enrichment_cache (
    cache_key TEXT PRIMARY KEY,  -- '<provider>:<person_id>'
    provider TEXT NOT NULL,
    person_id TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',  -- slim provider result; '{}' caches a miss
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrichment_cache_person ON enrichment_cache(person_id);

-- Provider-scoped usage: one row per (provider, lane, UTC day). Daily/monthly
-- budgets aggregate over these rows (skip on exhaustion, never error). Lanes:
-- 'search' (provider-search discovery) and 'enrich' (one-person enrichment).
CREATE TABLE IF NOT EXISTS enrichment_usage (
    provider TEXT NOT NULL,
    lane TEXT NOT NULL DEFAULT 'enrich',
    day TEXT NOT NULL,  -- YYYY-MM-DD (UTC)
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, lane, day)
);

-- Stable provider-search identities. Keyed by (provider, provider_person_id) so
-- the same person surfacing again from PDL/Coresignal is deduped to one person,
-- never re-created. canonical_linkedin backs the second dedupe tier.
CREATE TABLE IF NOT EXISTS provider_identities (
    provider TEXT NOT NULL,
    provider_person_id TEXT NOT NULL,
    person_id TEXT NOT NULL REFERENCES persons(id),
    canonical_linkedin TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (provider, provider_person_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_identities_person ON provider_identities(person_id);
CREATE INDEX IF NOT EXISTS idx_provider_identities_linkedin ON provider_identities(canonical_linkedin);

-- Resumable provider-search state and cumulative audit outcomes. A stable hash
-- of the effective, adapter-allowlisted filter is the filter identity.
CREATE TABLE IF NOT EXISTS provider_search_checkpoints (
    provider TEXT NOT NULL,
    filter_identity TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    cursor TEXT,
    next_page INTEGER NOT NULL DEFAULT 0,
    exhausted INTEGER NOT NULL DEFAULT 0,
    requested_pages INTEGER NOT NULL DEFAULT 0,
    api_requests INTEGER NOT NULL DEFAULT 0,
    returned_records INTEGER NOT NULL DEFAULT 0,
    credit_units INTEGER NOT NULL DEFAULT 0,
    verified_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    merged_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    rejection_reasons TEXT NOT NULL DEFAULT '{}',
    last_outcome TEXT NOT NULL DEFAULT 'never_run',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, filter_identity)
);

-- Phase 4 email digest subscriptions. Booleans remain INTEGER so the schema is
-- portable between SQLite and Postgres without backend-specific types.
CREATE TABLE IF NOT EXISTS subscribers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    frequency TEXT NOT NULL CHECK (frequency IN ('daily', 'weekly')),
    preferences TEXT NOT NULL DEFAULT '{}',
    unsubscribe_token TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subscribers_active_frequency
    ON subscribers(active, frequency);

-- The composite primary key is the never-repeat guarantee for each subscriber.
CREATE TABLE IF NOT EXISTS digest_sends (
    subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
    person_id TEXT NOT NULL REFERENCES persons(id),
    sent_at TEXT NOT NULL,
    provider_message_id TEXT,
    PRIMARY KEY (subscriber_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_digest_sends_subscriber
    ON digest_sends(subscriber_id);

CREATE TABLE IF NOT EXISTS feedback_votes (
    subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
    person_id TEXT NOT NULL REFERENCES persons(id),
    vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subscriber_id, person_id)
);

-- Privacy-minimal product analytics. Deliberately excludes IP addresses,
-- user agents, cookies, and persistent visitor identifiers.
CREATE TABLE IF NOT EXISTS page_views (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    viewed_at TEXT NOT NULL,
    referrer TEXT
);

CREATE INDEX IF NOT EXISTS idx_page_views_viewed_at ON page_views(viewed_at);
