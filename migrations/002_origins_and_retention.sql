-- 002: data-origin labelling, CSV import as its own source, verified feed list,
-- and an index used by the YouTube 30-day retention rule.

INSERT OR IGNORE INTO data_sources(id, name, cost_tier, enabled, notes)
VALUES ('csv_import', 'CSV import', 'free', 1, 'Numbers imported from a spreadsheet you supplied.');

-- Feeds that could not be verified from an internet-connected fetcher start switched off.
-- `python manage.py check-sources` tests them on your laptop; switch on the ones that work.
UPDATE news_feeds SET enabled = 0 WHERE verified = 0;

CREATE INDEX IF NOT EXISTS idx_metrics_source_collected ON metric_snapshots(source_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_content_source_collected ON content_items(source_id, collected_at);
