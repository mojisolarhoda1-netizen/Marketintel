# Architecture

## Flow

```
Brand / influencer added
        │
        ▼
Pipeline.run_entity()  ── picks providers that apply (has a website? a YouTube handle?)
        │
        ├─ provider not set up / postponed ──► run logged "unavailable" + reason shown in UI
        │
        ▼
provider.collect()  ── throttling, robots.txt, quota guard (api_usage table)
        │   errors: TransientError → retry 3× with 1s, 2s backoff
        │           RateLimited    → wait if short, else "skipped"
        ▼
pydantic validation (MetricObs / ContentObs / PageObs)
        ▼
save: metric_snapshots (append-only) · content_items (deduped by content_hash) · page_snapshots (only if changed)
        ▼
sentiment scored on new content
        ▼
insights.detect_for_entity(): mention spike · sentiment drop · follower jump · page change · source failure
        ▼
dashboard, competitors, listening, reports read only from the database
```

## Rules the code enforces

1. Every stored number has `source_id` and `collected_at` (and `observed_at` for metrics).
2. Snapshots are never overwritten. Growth = first vs last observation in the period.
3. Missing data is reported, never estimated. `build_facts()` fills a `missing` list that every report prints.
4. Sample data has `is_sample=1` and is labelled on every screen and report.
5. Paid engines refuse to run unless `ALLOW_PAID_AI=true`.

## Adding a data provider

1. Create `intel/providers/<name>.py` with a `Provider` subclass:
   - `id`, `name`, `cost_tier`
   - `is_configured()` → `(bool, message)`
   - `supports(entity, accounts)` → does this source apply?
   - `collect(entity, accounts, ctx)` → `CollectResult(metrics=[], content=[], pages=[])`
2. Add the class to `PROVIDER_CLASSES` in `intel/providers/__init__.py`.
3. Add a row to `DEFAULT_SOURCES` in `intel/db.py`.
4. Write a test with a recorded fixture in `tests/fixtures/`.

Nothing else changes: the pipeline, dashboard and reports pick it up.

## Moving to the production stack later

| Now | Later | How |
| --- | --- | --- |
| SQLite `data/intel.db` | Supabase Postgres | Run `migrations/*.sql` on Postgres (swap `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGINT GENERATED ALWAYS AS IDENTITY`, `strftime(...)` defaults → `now()`), then copy rows. `org_id` already exists on entities for Row Level Security. |
| Flask pages | Next.js front end | The JSON API (`/api/...`) is the start of the backend the Next.js app will call. |
| `manage.py scheduler` | Supabase pg_cron / Edge Functions or a small server | Same `Pipeline` code, called on a schedule. |
| Single workspace, no login | Supabase Auth + roles | Add `org_id` filters + RLS policies before any outside user. |

## Database tables

`organisations`, `entities` (brands + influencers), `competitor_links`, `social_accounts`, `data_sources`, `collection_runs`, `metric_snapshots`, `content_items`, `page_snapshots`, `alerts`, `reports`, `api_usage`, `news_feeds`, `settings`, `schema_migrations`. See `migrations/001_init.sql`.
