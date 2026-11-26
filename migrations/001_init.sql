-- 001_init.sql
-- Core schema. Written in plain SQL that also runs on PostgreSQL
-- (swap INTEGER PRIMARY KEY AUTOINCREMENT for BIGSERIAL / identity when migrating).
-- Every observation records WHERE it came from (source_id) and WHEN (collected_at).
-- Snapshots are append-only: history is never overwritten.

CREATE TABLE IF NOT EXISTS organisations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- A tracked thing: a brand or an influencer.
CREATE TABLE IF NOT EXISTS entities (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id         INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('brand','influencer')),
    name           TEXT NOT NULL,
    country        TEXT,
    category       TEXT,                 -- industry for brands, niche for influencers
    website        TEXT,
    keywords       TEXT NOT NULL DEFAULT '[]',   -- JSON array of extra search terms
    notes          TEXT,
    refresh_hours  INTEGER NOT NULL DEFAULT 24 CHECK (refresh_hours BETWEEN 1 AND 720),
    active         INTEGER NOT NULL DEFAULT 1,
    is_sample      INTEGER NOT NULL DEFAULT 0,   -- 1 = development sample data, always labelled in the UI
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (org_id, kind, name)
);
CREATE INDEX IF NOT EXISTS idx_entities_org_kind ON entities(org_id, kind);

-- brand -> competitor brand
CREATE TABLE IF NOT EXISTS competitor_links (
    brand_id       INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    competitor_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (brand_id, competitor_id),
    CHECK (brand_id <> competitor_id)
);

CREATE TABLE IF NOT EXISTS social_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    platform     TEXT NOT NULL CHECK (platform IN ('instagram','youtube','tiktok','x','facebook','linkedin','other')),
    handle       TEXT NOT NULL,
    external_id  TEXT,                    -- platform's own id once resolved
    url          TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (entity_id, platform, handle)
);

-- Registry of data sources and their current state.
CREATE TABLE IF NOT EXISTS data_sources (
    id            TEXT PRIMARY KEY,       -- e.g. 'rss_news'
    name          TEXT NOT NULL,
    cost_tier     TEXT NOT NULL CHECK (cost_tier IN ('free','free_setup','paid','unavailable')),
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_status   TEXT,
    last_checked  TEXT,
    notes         TEXT
);

-- One row per provider run for one entity.
CREATE TABLE IF NOT EXISTS collection_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id    INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    source_id    TEXT NOT NULL REFERENCES data_sources(id),
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL CHECK (status IN ('running','ok','partial','failed','unavailable','skipped')),
    attempts     INTEGER NOT NULL DEFAULT 1,
    items_found  INTEGER NOT NULL DEFAULT 0,
    items_new    INTEGER NOT NULL DEFAULT 0,
    message      TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_entity ON collection_runs(entity_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_source ON collection_runs(source_id, started_at);

-- Numeric observations over time (followers, subscribers, views, engagement...).
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    account_id    INTEGER REFERENCES social_accounts(id) ON DELETE SET NULL,
    source_id     TEXT NOT NULL REFERENCES data_sources(id),
    platform      TEXT,
    metric        TEXT NOT NULL,
    value         REAL NOT NULL,
    observed_at   TEXT NOT NULL,          -- when the value was true
    collected_at  TEXT NOT NULL,          -- when we fetched / recorded it
    is_sample     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_metrics_lookup ON metric_snapshots(entity_id, metric, observed_at);

-- Posts, videos, articles, mentions.
CREATE TABLE IF NOT EXISTS content_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    account_id       INTEGER REFERENCES social_accounts(id) ON DELETE SET NULL,
    source_id        TEXT NOT NULL REFERENCES data_sources(id),
    kind             TEXT NOT NULL CHECK (kind IN ('mention','post','video','article')),
    external_id      TEXT,
    url              TEXT,
    title            TEXT,
    body             TEXT,
    author           TEXT,
    publisher        TEXT,
    published_at     TEXT,
    collected_at     TEXT NOT NULL,
    likes            INTEGER,
    comments         INTEGER,
    views            INTEGER,
    sentiment_score  REAL,
    sentiment_label  TEXT CHECK (sentiment_label IN ('positive','neutral','negative')),
    content_hash     TEXT NOT NULL,
    is_sample        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (entity_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_content_entity_date ON content_items(entity_id, published_at);

-- Website monitoring.
CREATE TABLE IF NOT EXISTS page_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id         INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source_id         TEXT NOT NULL REFERENCES data_sources(id),
    url               TEXT NOT NULL,
    title             TEXT,
    meta_description  TEXT,
    headings          TEXT,               -- JSON array
    prices            TEXT,               -- JSON array of price strings found
    text_hash         TEXT NOT NULL,
    changed           INTEGER NOT NULL DEFAULT 0,
    collected_at      TEXT NOT NULL,
    is_sample         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pages_entity ON page_snapshots(entity_id, url, collected_at);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,            -- mention_spike, sentiment_drop, follower_jump, page_change, source_failure
    severity    TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    message     TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}',
    dedupe_key  TEXT UNIQUE,              -- stops the same alert being raised twice
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,          -- brand, influencer, competitor
    title         TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    engine        TEXT NOT NULL,          -- builtin, ollama, claude_handoff
    facts         TEXT NOT NULL,          -- JSON: every number the report uses
    narrative     TEXT NOT NULL,
    missing       TEXT NOT NULL DEFAULT '[]',
    generated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Daily API usage for quota guards (e.g. YouTube units).
CREATE TABLE IF NOT EXISTS api_usage (
    source_id  TEXT NOT NULL,
    day        TEXT NOT NULL,
    units      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, day)
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- News feeds to watch (user-editable).
CREATE TABLE IF NOT EXISTS news_feeds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    url        TEXT NOT NULL UNIQUE,
    country    TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    verified   INTEGER NOT NULL DEFAULT 0,  -- 1 once check-sources has fetched it successfully
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
