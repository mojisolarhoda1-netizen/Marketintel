# Installing Market Intel on Windows

Everything here is free. You need about 20 minutes and an internet connection for the first setup. After that, the app runs on your laptop, and only data collection needs internet.

## What you need

| Item | Where | Cost |
| --- | --- | --- |
| Windows 10 or 11 laptop | — | — |
| Python 3.12 (3.10 or newer works) | https://www.python.org/downloads/windows/ | Free |
| A web browser (Chrome, Edge) | already installed | Free |
| About 300 MB free disk space | — | — |

You do **not** need Node.js, Git, a database server, or any paid account.

## Step 1 — Install Python (once)

1. Open https://www.python.org/downloads/windows/ and download the **Windows installer (64-bit)** for Python 3.12.
2. Run it. On the **first screen, tick "Add python.exe to PATH"** at the bottom. This matters.
3. Click **Install Now**, then wait for "Setup was successful", then close the installer.

Check it worked: press the Windows key, type `cmd`, press Enter, and type:

```
py --version
```

You should see something like `Python 3.12.x`.

If Windows opens the Microsoft Store instead, Python isn't installed properly. Rerun the installer and make sure "Add python.exe to PATH" is ticked.

## Step 2 — Unzip the project

1. Right-click `market-intel.zip` → **Extract All…**
2. Choose a simple location, e.g. `C:\Users\<you>\Documents\market-intel`
3. Avoid OneDrive-synced folders if you can (syncing can lock the database file).

## Step 3 — Run setup (once)

Open the `market-intel` folder and double-click **`setup.bat`**.

It creates a private Python environment in the folder, downloads 6 free packages (Flask, requests, BeautifulSoup, lxml, pydantic, ReportLab) plus pytest for the tests, creates your `.env` settings file, and creates the database.

If Windows shows "Windows protected your PC", click **More info → Run anyway**. Windows shows this for any script you downloaded.

## Step 4 — Validate (do this first)

Double-click **`validate.bat`**. It checks, on your laptop:

- Python and packages
- The database: it can write, and nothing was lost since the last check
- Sample data is kept separate from real data
- **Every news feed, live over your internet.** Feeds that work are switched on automatically
- Your YouTube and Instagram keys, if you've added them
- That a PDF and a CSV report can be produced

The full result is saved as `data\validation-report-<date>.txt`. Send me that file and I'll read it with you.

## Step 5 — Start the app

Double-click **`run.bat`**. A black window opens; keep it open. Your browser opens http://127.0.0.1:5000 after a few seconds. To stop, close the black window.

The app only listens on your own laptop (127.0.0.1). Nobody else on your Wi-Fi can open it.

## Step 6 — Run the automated tests (optional)

Double-click **`run_tests.bat`**. You should see `60 passed`.

## Optional free keys (add later)

Open `.env` in Notepad and fill in:

```
YOUTUBE_API_KEY=your-key
IG_USER_ID=your-instagram-professional-account-id
IG_ACCESS_TOKEN=your-token
```

Save, close the app window, and run `run.bat` again. See `docs\DATA_SOURCES.md` for step-by-step key setup and exactly what each source gives you.

## Everyday use

| Task | How |
| --- | --- |
| Start the app | `run.bat` |
| Collect data automatically | Windows Task Scheduler → Create Basic Task → Daily → Start a program → choose `collect_due.bat` |
| Back up your data | `backup.bat` (the app also makes one backup per day when it starts) |
| Restore a backup | Close the app, copy a file from `data\backups\` to `data\intel.db` |
| Remove demo data | open a terminal in the folder: `.venv\Scripts\activate` then `python manage.py clear-sample` |

## Where your data lives

All of it is in **`data\intel.db`**. Restarting the app or the laptop doesn't erase anything. Deleting the `data` folder does, so copy `data\backups\` to Google Drive or a flash drive now and then.

## If something goes wrong

| Problem | Fix |
| --- | --- |
| "Python was not found" | Redo Step 1 with "Add python.exe to PATH" ticked |
| Package install failed | Check your internet, then run `setup.bat` again |
| Browser says "can't reach this page" | Wait 5 seconds and refresh; make sure the black window is still open |
| "Address already in use" | The app is already running in another window, or change `PORT=5001` in `.env` |
| A news feed fails in validation | That publisher blocks automated readers or is down. Leave it off; the others still work |
