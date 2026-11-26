# Phase 2 Plan — Zero-Spend Build

**Rule:** Build first. Validate second. Spend money only when the business gives us a good reason to.

Every tool below passed one test: *can we get the same result for free right now?*
If yes, we use the free option. If no, the feature is built as a plug-in and switched off until it earns its cost.

---

## 1. What changed from the original Phase 2

| Original plan | Zero-spend replacement | Why it's fine for now | When we switch back |
| --- | --- | --- | --- |
| Next.js on Vercel | Python + Flask, running on your laptop | Free, one command to start, no account | When paying customers need a public web address |
| Supabase Postgres | SQLite (one file: `data/intel.db`) | Free, zero setup, standard SQL that moves to Postgres later | When more than one person needs to log in from different places |
| Supabase Auth | Single local workspace, no login | Only you use it during validation | Before the first outside user |
| Supabase Edge Functions + pg_cron | `python manage.py scheduler` (or Windows Task Scheduler) | Free, runs while your laptop is on | When collection must run 24/7 |
| Claude API (pay per token) | Built-in analysis engine + free local AI (Ollama) + "Claude Pro hand-off" | Your Claude Pro subscription can't be called by software, but you can paste a ready-made data pack into it | Not planned during validation |
| Recharts / component library | Hand-built CSS + server-drawn SVG charts | No downloads, works offline | When we rebuild the front end in Next.js |
| Sentry | Errors written to `data/logs/` and shown on the Sources page | Free | At public launch |
| Resend email | Alerts shown inside the app | Free | When customers need email alerts |

The **engine** — providers, pipeline, database design, analysis and reports — is the valuable part and carries over to the production stack. The screens will be rebuilt in Next.js later; the engine will become its backend.

---

## 2. Data sources: free now, free with setup, postponed

| Source | Status | What it gives us | What it costs |
| --- | --- | --- | --- |
| News RSS feeds (6 verified Nigerian publishers + any you add) | **Free now** | Brand and competitor mentions in the press | Nothing |
| Brand and competitor websites | **Free now** | Page changes, product and price changes (we respect robots.txt) | Nothing |
| Manual entry and CSV import | **Free now** | Follower counts and results from any platform, including TikTok | Your time |
| YouTube Data API | **Free with setup** | Channel subscribers (rounded), views, videos; creator search | Free API key; 10,000 units/day + 100 searches/day; data kept max 30 days (YouTube rule) |
| Instagram Business Discovery | **Free with setup** | Followers, post count, recent post likes/comments/views of other Business/Creator accounts | Free Meta developer app in Development mode + your professional account |
| Google News search RSS | **Off by default** | Wider mention search | Free, but terms for commercial use need review first |
| TikTok | **Postponed** | — | Research API is closed to companies; Login Kit needs an approved app |
| X (Twitter) | **Postponed** | — | Pay per call |
| Apify | **Postponed** | — | Scraping-based; needs review (your rule) |

When a source is postponed or not set up, the app says so on screen. It never shows a made-up number.

---

## 3. Free AI, three levels

1. **Built-in engine (always on, offline):** sentiment scoring with English and Nigerian Pidgin terms, growth and engagement maths, anomaly detection, share of voice, and written report summaries built only from stored numbers.
2. **Ollama (optional, free, runs on your laptop):** a small open-source model writes the report narrative from the same stored facts. Needs about 8 GB of RAM.
3. **Claude Pro hand-off (free with your subscription):** one click copies a data pack and instructions; you paste it into Claude and get an analyst-grade report. Nothing is sent automatically.

All three follow the same rule: the AI sees only data stored in the database, with dates and sources, and every report lists what was missing.

---

## 4. Build order (this phase)

1. Database schema and migrations (Postgres-compatible SQL).
2. Provider plug-in contract + RSS, website, YouTube, Instagram, manual/CSV providers + TikTok/X placeholders.
3. Collection pipeline: validate → de-duplicate → normalise → save snapshot → analyse → alert. Retries, rate limits, quota guard, run log.
4. Analysis: sentiment, growth, engagement rate, anomalies, share of voice, authenticity signals.
5. Reports: HTML, PDF and CSV; built-in, Ollama and Claude Pro hand-off.
6. Web app: Overview, Brands, Influencers, Discover, Competitors, Listening, Reports, Alerts, Sources.
7. Tests for every major piece; then a live check on your laptop with `python manage.py check-sources`.

**Done means:** you can add a real brand, its competitors and influencers; collect real data from the free sources; see history build up; and generate a report you would be comfortable showing a Dexia Global client.

---

## 5. Validation targets before spending anything

- Track 10 real brands and 50 real creators for 4 weeks.
- Show weekly reports to 3–5 Dexia Global clients.
- Spend trigger: at least 2 clients say they would pay, or ask for something only a paid source can give.
