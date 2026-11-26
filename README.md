# Market Intel — African Market Intelligence Platform (working name)

A working, local-first market intelligence engine. It tracks brands, competitors and influencers, collects permitted public data from free sources, keeps every observation with its history, analyses trends and writes client-ready reports.

**Cost to run: ₦0.** It runs on your own laptop with free, open-source software. No hosting, no subscriptions, no paid APIs.

## Start here

- **Online (any device, including iPad):** follow **[DEPLOY_VERCEL.md](DEPLOY_VERCEL.md)**: GitHub + Turso + Vercel, all free while testing. Password-protected; collects data the moment you press Start tracking, and again every day.

1. **Windows:** follow **[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md)**. In short: install Python → `setup.bat` → `validate.bat` → `run.bat`.
2. macOS/Linux: `./setup.sh`, then `python manage.py validate --enable-working`, then `./run.sh`.
3. Open http://127.0.0.1:5000

## Documents

| File | What it answers |
| --- | --- |
| [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) | What to install, how to start, everyday use, fixes |
| [docs/USER_GUIDE_V03.md](docs/USER_GUIDE_V03.md) | How to use watchlists, comparison, topics, partnerships, campaigns, opportunities, schedules, Ask your data |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Exactly what news feeds, websites, YouTube and Instagram can and can't give; quotas; setup |
| [docs/TESTING.md](docs/TESTING.md) | What the automated tests cover and what they don't |
| [docs/PHASE2_PLAN.md](docs/PHASE2_PLAN.md) | The zero-spend plan |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it fits together; adding sources; moving to a hosted stack later |
| [CLAUDE.md](CLAUDE.md) | Rules Claude Code follows in this project |

## What works for free

| Feature | Source | Setup |
| --- | --- | --- |
| Press mentions for brands and competitors | 6 verified Nigerian publisher RSS feeds (+7 more tested on your laptop) | None |
| Website, product and price change alerts | Brand websites (robots.txt respected) | None |
| Sentiment, English + Nigerian Pidgin | Built-in word list (rule-based, offline) | None |
| Follower history for any platform, incl. TikTok | Manual entry or CSV import | None |
| YouTube channel stats + creator discovery | YouTube Data API | Free API key |
| Instagram followers + recent post engagement of public professional accounts | Instagram Business Discovery | Free Meta developer app |
| Written reports | Built-in analyst · Ollama (free local AI) · Claude Pro hand-off | Optional |
| Client-ready PDF and tidy CSV | Built-in | None |
| Client watchlists, creator comparison | Built-in | None |
| Topic / hashtag listening across news feeds | News RSS feeds | None |
| Influencer–brand partnership detection (#ad aware) | Collected captions | Instagram/YouTube connected |
| Campaign tracking vs baseline, with PDF | Stored data | None |
| Opportunities feed, scheduled weekly/monthly reports, Ask your data | Built-in · Ollama · Claude Pro hand-off | None |

## Real, imported and sample data are never confused

Every number carries a label:

- **LIVE**: collected automatically from a public source
- **MANUAL**: typed in by you
- **IMPORTED**: from a CSV you supplied
- **SAMPLE**: fictional demo data

Headline totals count real data only. Sample entries can be hidden with one click, can't receive real numbers, and any sample report is titled and watermarked "SAMPLE DATA" on every page.

## Your data is safe on your laptop

Everything is stored in `data/intel.db`. Restarting the app or the laptop changes nothing. The app saves one backup a day to `data/backups/` when it starts, and `backup.bat` makes one any time. `validate.bat` compares counts with the previous run to prove nothing was lost.

## Commands

```
python manage.py validate [--enable-working]   full self-check, saved to data/validation-report-*.txt
python manage.py collect --due                 collect for everything due (Task Scheduler: collect_due.bat)
python manage.py collect --id 3                collect one brand/influencer now
python manage.py scheduler                     collect every hour while the laptop is on
python manage.py report --id 3                 create a report
python manage.py backup                        copy the database to data/backups/
python manage.py seed-sample / clear-sample    add / remove fictional demo data
python -m pytest -q                            run the automated tests
```

## Project layout

```
app.py                  web app + JSON API
manage.py               command line
intel/providers/        one file per data source
intel/pipeline.py       collect → validate → de-duplicate → save → analyse → alert
intel/retention.py      platform data-retention rules (YouTube 30 days)
intel/analysis/         sentiment, trends, authenticity signals
intel/ai/engines.py     built-in / Ollama / Claude Pro hand-off
intel/reports.py        the facts every report is built from
intel/pdf.py, exports.py  PDF and CSV
intel/maintenance.py    backups and validation
intel/watchlists.py, topics.py, partnerships.py, campaigns.py, opportunities.py, schedules.py, ask.py   v0.3 features
migrations/             database schema
templates/, static/     screens
tests/                  automated tests and recorded fixtures
```
