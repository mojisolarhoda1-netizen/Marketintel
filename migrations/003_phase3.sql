-- 003: client watchlists, topics (keyword listening), partnerships, campaigns, report schedules.

-- A watchlist is a named group, usually one per client: their brand, competitors and creators.
CREATE TABLE IF NOT EXISTS watchlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (org_id, name)
);
CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id  INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    entity_id     INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (watchlist_id, entity_id)
);

-- Topics: industry keywords / hashtags watched across all news feeds, not tied to one brand.
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '[]',     -- JSON array; an item matches if ANY keyword appears
    country     TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (org_id, name)
);
CREATE TABLE IF NOT EXISTS topic_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id         INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    source_id        TEXT NOT NULL REFERENCES data_sources(id),
    title            TEXT,
    body             TEXT,
    url              TEXT,
    publisher        TEXT,
    published_at     TEXT,
    collected_at     TEXT NOT NULL,
    matched          TEXT,                       -- which keyword matched
    sentiment_score  REAL,
    sentiment_label  TEXT,
    content_hash     TEXT NOT NULL,
    UNIQUE (topic_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_topic_items ON topic_items(topic_id, published_at);

-- Influencer <-> brand partnerships detected in post captions / video descriptions.
CREATE TABLE IF NOT EXISTS partnerships (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    influencer_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    brand_id       INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    content_id     INTEGER NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    matched        TEXT NOT NULL,               -- the brand term found
    disclosed      INTEGER NOT NULL DEFAULT 0,  -- #ad, #sponsored, "paid partnership" etc. present
    disclosure     TEXT,
    detected_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (content_id, brand_id)
);
CREATE INDEX IF NOT EXISTS idx_partnerships_brand ON partnerships(brand_id);
CREATE INDEX IF NOT EXISTS idx_partnerships_infl ON partnerships(influencer_id);

-- Campaigns: a brand's push between two dates, with its hashtags and the creators involved.
CREATE TABLE IF NOT EXISTS campaigns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    start_date  TEXT NOT NULL,                  -- YYYY-MM-DD
    end_date    TEXT NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '[]',     -- campaign hashtags / slogans
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    CHECK (end_date >= start_date)
);
CREATE TABLE IF NOT EXISTS campaign_influencers (
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    influencer_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, influencer_id)
);

-- Automatic weekly / monthly reports, saved as PDF files on the laptop.
CREATE TABLE IF NOT EXISTS report_schedules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    watchlist_id  INTEGER REFERENCES watchlists(id) ON DELETE CASCADE,
    frequency     TEXT NOT NULL CHECK (frequency IN ('weekly','monthly')),
    active        INTEGER NOT NULL DEFAULT 1,
    last_run      TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    CHECK (entity_id IS NOT NULL OR watchlist_id IS NOT NULL)
);
