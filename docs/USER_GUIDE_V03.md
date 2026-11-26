# What's new in v0.3 — and how to use it

All new features are free and run on your laptop. They only use data the app has already collected or that you entered.

## Client watchlists (Monitor → Client watchlists)
One list per client: their brand, competitors and creators.
- Create a list, then add brands/creators. You can also add from any brand or creator page ("+ Add to watchlist…").
- The list page shows followers, engagement, mentions, sentiment and unread alerts for everyone in it.
- Schedule weekly or monthly reports for the whole list (see below).

## Compare creators (Influencers → tick 2–4 → Compare)
A side-by-side table per platform showing followers, engagement rate, average likes/comments/views, 30-day growth, authenticity checks to look at, press mentions and brands they post about. Every number shows its date and label (LIVE / MANUAL / IMPORTED / SAMPLE). Blank means not collected; nothing is estimated.

## Topic listening (Monitor → Topics)
Follow an industry, trend or hashtag across all your news feeds, e.g. *Nigerian fintech: fintech, mobile money, POS*.
- Press **Scan news now**, or let scheduled collection do it.
- Each topic shows stories per day, tone, top publishers, recurring words, and which of **your tracked brands** appear in those stories.
- Matching is whole-word, so use specific keywords.

## Partnership detection (on every brand and creator page)
When creators' posts are collected (Instagram/YouTube), the app looks for your tracked brands in captions and descriptions: name, extra search terms, @handle and #BrandName. It also notes whether the post says #ad, #sponsored, "paid partnership" or similar.
- On a brand page: "Creators mentioning this brand"
- On a creator page: "Brands mentioned in posts"

It's a lead to check, not proof of a paid deal.

## Campaign tracking (Monitor → Campaigns)
Create a campaign with its brand, dates, hashtags/slogans and the creators involved. The campaign page compares the campaign with the same number of days just before it:
- press mentions and average tone, before vs during
- brand followers, before vs latest
- creator posts that mention the brand or campaign tags, with likes, comments, views and disclosure
- campaign tags appearing in news stories

For a running campaign, both windows use the number of days elapsed so far, so the comparison is fair. Download a **client-ready PDF** or **CSV**.

## Opportunities (Intelligence → Opportunities)
Things worth acting on, found by fixed rules, each with its numbers:
- a competitor getting mostly negative coverage this week
- a competitor's website or prices changed
- your brand's share of press attention fell 15+ points week on week
- a creator grew followers 10%+ in 30 days
- a topic's stories doubled week on week
- a creator mentioned a brand without a visible disclosure

## Scheduled reports (Reports → Scheduled reports)
Choose a brand, creator or watchlist and weekly or monthly. When collection runs (`collect_due.bat` / Task Scheduler) or you press **Run due reports now**, due reports are saved as PDF + CSV in `data\reports\<client name>\`. Nothing is emailed or uploaded.

## Ask your data (Intelligence → Ask your data)
Ask a question about one brand/creator, a watchlist or everything.
- **Claude Pro hand-off:** the app builds a pack with your question plus the stored facts (last 30 days). Copy it into a new Claude chat.
- **Local AI (Ollama):** if installed, the answer appears on the page.

Both are told to answer only from the facts and to say when something isn't in the data.

## Try it with the demo data
`python manage.py seed-sample` now also creates a sample campaign (*SAMPLE Zuma December*), sample creator posts with and without #ad, and a sample client watchlist. Everything is marked SAMPLE. Remove it all with `python manage.py clear-sample`.
