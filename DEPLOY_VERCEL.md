# Put Market Intel online with Vercel (doable entirely on an iPad)

When you're done, you'll have a private web address (like `market-intel-yourname.vercel.app`) that you can open on any device. Press **Start tracking** and it immediately fetches news mentions and the brand's website, plus YouTube and Instagram numbers once those keys are added. It also collects again automatically once a day.

**Cost: free** while you're testing: Vercel Hobby, Turso Free and GitHub Free. Vercel's Hobby plan is for personal, non-commercial use, so move to Vercel Pro (about $20/month) only when you start charging clients for reports from it.

Three free accounts are needed. Use the same email for all three.

---

## Step 1 — GitHub (stores the code) · 5 minutes

1. Go to **github.com** → **Sign up**. Choose a username and remember it.
2. Tap **+** (top right) → **New repository**.
   - Name: `market-intel`
   - Choose **Private**
   - Leave every box unticked (no README)
   - Tap **Create repository**
3. Tell Claude your GitHub username. Claude sends all the code into this repository for you.
   If Claude asks you to connect GitHub, follow the link it gives you and approve access to `market-intel`.

## Step 2 — Turso (stores your data) · 5 minutes

1. Go to **turso.tech** → **Sign up** (you can sign in with your GitHub account).
2. In the dashboard: **Databases → Create Database**.
   - Name: `market-intel`
   - Location: pick a **Europe** region (closest to Nigeria among the options).
3. Open the database and copy two things into your Notes app:
   - **Database URL**: starts with `libsql://`
   - **Token**: tap **Create Token** (or "Generate token"), allow read & write, no expiry, then copy it.

## Step 3 — Vercel (runs the app) · 10 minutes

1. Go to **vercel.com** → **Sign up** → **Continue with GitHub**. Choose the **Hobby** plan.
2. **Add New… → Project** → find `market-intel` → **Import**.
   (If you don't see it: "Adjust GitHub App Permissions" → allow the `market-intel` repository.)
3. Before deploying, open **Environment Variables** and add these, one at a time:

| Name | Value |
| --- | --- |
| `APP_PASSWORD` | A strong password you'll use to sign in (at least 12 characters) |
| `TURSO_DATABASE_URL` | The `libsql://…` URL from Step 2 |
| `TURSO_AUTH_TOKEN` | The token from Step 2 |
| `CRON_SECRET` | Any long random text, e.g. 30 letters and numbers. It protects the daily collection |
| `SECRET_KEY` | Another long random text (keeps you signed in) |
| `YOUTUBE_API_KEY` | Optional — your free YouTube key (see docs/DATA_SOURCES.md) |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | Optional — your free Instagram connection (see docs/DATA_SOURCES.md) |

4. Tap **Deploy**. Wait 1–3 minutes until you see "Congratulations".
5. Tap the preview to open your app. Sign in with your `APP_PASSWORD`.
6. Optional, for speed: Vercel → Project → **Settings → Functions → Function Region**. Pick the city nearest to the Turso region you chose (for example Dublin for Ireland, Frankfurt for Frankfurt, London for London). Redeploy.

Add the address to your iPad home screen: Safari → Share → **Add to Home Screen**.

---

## Using it

- **Brands → Add brand.** Fill in the name, website and handles, then press **Start tracking**. The page opens with what each source found on the first run. The **Collection log** at the bottom says exactly which sources worked and why any didn't.
- **Automatic collection** runs once a day (Vercel's free plan allows one scheduled run a day). Press **Collect now** on any brand to update it immediately.
- **Data sources** page: switch news feeds on or off, add more feeds, and see what is set up.

## What it fetches by itself, and what it can't

| Source | Automatic? |
| --- | --- |
| News mentions (Nairametrics, BusinessDay, Premium Times, ThisDay, Techpoint Africa, Legit.ng + any feed you add) | Yes |
| Brand website: titles, headings, prices (respects robots.txt) | Yes |
| YouTube subscribers, views, recent videos | Yes, once `YOUTUBE_API_KEY` is set |
| Instagram followers, posts, recent likes/comments (Business/Creator accounts) | Yes, once the Instagram keys are set |
| TikTok | No (no free commercial access) — type numbers in or import a CSV |
| X (Twitter) | No (paid only) |

Some news sites block automated readers from data-centre servers. If a feed shows an error on the Data sources page, switch it off; the others still work.

## Updating later

When Claude improves the app, it pushes the new code to your GitHub repository and Vercel redeploys by itself within a couple of minutes. Your data stays in Turso and isn't touched.

## If something goes wrong

| You see | Fix |
| --- | --- |
| "Set APP_PASSWORD…" | Add `APP_PASSWORD` in Vercel → Settings → Environment Variables, then Deployments → ⋯ → Redeploy |
| "database refused the token" | Create a new Turso token and replace `TURSO_AUTH_TOKEN`, then redeploy |
| "database unreachable" | Check `TURSO_DATABASE_URL` starts with `libsql://` and has no spaces |
| A page is slow | Set the Vercel function region next to your Turso region (Step 3.6) |
| Build failed | Send Claude the last 20 lines of the build log (Deployments → the failed one → Building) |
