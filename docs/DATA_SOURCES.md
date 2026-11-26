# Data sources: exactly what each gives, its limits, and how to set it up

Checked against official documentation on 20 November 2026. Platforms change their rules; `validate.bat` confirms what works on your machine today.

---

## 1. News RSS feeds — free, no setup

What it gives: headline, link, publisher, date and the short summary the publisher puts in the feed. The app keeps items that mention the brand's name or extra search terms. It never downloads full articles.

Feeds confirmed as valid RSS from an internet-connected fetcher on 20 Nov 2026 (these start **on**):
Nairametrics, BusinessDay, Premium Times, ThisDay, Techpoint Africa, Legit.ng.

Feeds that couldn't be confirmed (start **off**; `validate.bat` tests them from your laptop and switches on any that work):
TechCabal, Punch, Vanguard, The Guardian Nigeria, TheCable, Channels TV, Daily Post. Most of these refused automated readers in my check. The app identifies itself honestly and does not pretend to be a browser, so a site that refuses bots stays off.

Limits: only what publishers put in their feeds (usually the last 10–30 stories), so collect at least daily. Headlines only mention a brand when it's newsworthy; small brands may get few or no mentions.

---

## 2. Brand websites — free, no setup

What it gives: page title, description, main headings and any prices shown (₦, $, £, KES, GH₵, R), stored as a fingerprint. An alert fires when they change.

Limits: checks robots.txt first and skips sites that disallow bots. Waits between requests. Reads the page as delivered, so prices drawn by JavaScript after loading may not be seen.

---

## 3. YouTube Data API v3 — free with an API key

### What it can retrieve with a key (public data only)
| Data | Notes |
| --- | --- |
| Channel title, handle, country (if the owner set it), description | public channel info |
| Subscriber count | **rounded down by YouTube to 3 significant figures** (e.g. 1,234,567 shows as 1,230,000). Missing if the owner hides it |
| Total channel views, number of public videos | |
| Recent videos: title, description, publish date, views, likes, comments | the app reads the 12 most recent |
| Creator search by keyword and country | discovery |

### What it can't retrieve with a key
Watch time, audience age/gender/location, traffic sources, revenue, daily subscriber gains, impressions or click-through rate. These need the YouTube Analytics API **with the channel owner's permission** (a later "Creator Connect" feature). Dislike counts have been private since December 2021, and favourite counts are always 0.

### Quota (free)
| Bucket | Daily limit | App uses |
| --- | --- | --- |
| Most calls (channels, playlists, videos) | 10,000 units/day; each of these calls costs 1 unit | about 3 units per channel per refresh; the app stops at 8,000 |
| search.list (creator discovery) | its own bucket: 100 searches/day, 1 per call | stops at 90 |

Even failed requests use quota. More quota needs a free compliance audit with Google. In practice, 3 units per channel means you could refresh over 2,000 channels a day.

### Storage rule (important)
YouTube's Developer Policies (section III.E.4) say data collected **without the channel owner's authorisation** must be deleted or refreshed within 30 days, and subscriber counts must not be kept longer than 30 days. The app follows this automatically. **YouTube history in charts covers the last 30 days only.** Numbers you record yourself (manual or CSV) are not affected.

### Setup (about 5 minutes)
1. https://console.cloud.google.com → create a project.
2. APIs & Services → Library → **YouTube Data API v3** → Enable.
3. APIs & Services → Credentials → Create credentials → **API key**. Optionally restrict it to the YouTube Data API.
4. In `.env`: `YOUTUBE_API_KEY=...` → restart → `validate.bat` shows "API key works".

---

## 4. Instagram — Business Discovery (Instagram API with Facebook Login) — free

### Can it read other creators' public statistics?
**Yes, with limits.** Business Discovery lets your app read basic public metrics of *other* Instagram accounts by username, as long as **the other account is a Business or Creator (professional) account**. Personal accounts and age-gated accounts return nothing.

### What it retrieves
| Data | Notes |
| --- | --- |
| Followers count | |
| Number of posts | |
| Recent posts: like count, comment count, view count (for videos/reels) | likes are missing when the owner hides them; the app excludes those instead of guessing |
| Caption, date, link of recent posts | requested; if Meta refuses these fields, the app falls back to counts only |

### What it can't retrieve
Audience demographics, reach, impressions, saves, shares, story data, follower lists, or anything from personal accounts. These need the account owner's own login (a later "Creator Connect" feature).

### Account types
- **Your side (the requester):** your own Instagram **professional** account (Business or Creator), linked to a Facebook Page.
- **Target side:** Business or Creator accounts only.

### Permissions required (Meta reference docs)
`instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`. Also `ads_read` or `ads_management` if your role on the Facebook Page comes through Business Manager. Token: a Facebook **User** access token.

### Development mode
In Development mode, Meta lets an app request permissions only from people who have a role on the app (you). That's enough for validation: you log in as yourself and look up public professional accounts. Opening it to outside customers would need Meta's App Review later. Nothing here costs money.

### Setup (about 30–45 minutes)
1. Make sure your Instagram account is Professional and linked to a Facebook Page you manage.
2. https://developers.facebook.com → My Apps → Create app (Business type). Keep it in **Development** mode.
3. Add the **Instagram** product (Instagram API with Facebook Login).
4. Tools → **Graph API Explorer**: choose your app, add the permissions above, click Generate Access Token, and log in as yourself.
5. In the Explorer, run `me/accounts?fields=name,instagram_business_account`. The `instagram_business_account.id` is your `IG_USER_ID`.
6. Exchange the short-lived token for a long-lived one (Access Token Debugger → Extend Access Token). Long-lived tokens last about 60 days; renew when validation says the token expired.
7. In `.env`: `IG_USER_ID=...` and `IG_ACCESS_TOKEN=...` → restart → `validate.bat`.

---

## 5. Manual entry and CSV import — free

For TikTok or any platform without a free API. Every value records the date you saw it and shows as **MANUAL** (typed) or **IMPORTED** (CSV). Template: Data sources page → "Download a template".

---

## Postponed (not connected, nothing to pay)
- **TikTok:** the Research API is for non-commercial academic researchers only. Later plan: Login Kit, where creators connect their own accounts.
- **X (Twitter):** pay-per-use only.
- **Apify:** scraping-based; needs a licensing review first.
- **Google News search RSS:** included but off; review Google's terms before any commercial use.
