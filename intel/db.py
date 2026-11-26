"""SQLite access and migrations.

The SQL is kept PostgreSQL-compatible so the move to Supabase later is a
migration, not a rewrite.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"


def db_path() -> Path:
    return Path(os.environ.get("INTEL_DB", ROOT / "data" / "intel.db"))


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def online_db() -> tuple[str, str] | None:
    """Turso settings when the app runs online (e.g. on Vercel)."""
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    return (url, os.environ.get("TURSO_AUTH_TOKEN", "").strip()) if url else None


def connect(path: Path | str | None = None):
    if path is None and online_db():
        from .turso import TursoConnection
        return TursoConnection(*online_db())
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session(path: Path | str | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


DEFAULT_SOURCES = [
    ("rss_news", "News RSS feeds", "free", "Headlines and links from publishers' public RSS feeds."),
    ("website", "Website monitor", "free", "Public pages of brand websites. robots.txt is respected."),
    ("manual", "Manual entry / CSV", "free", "Numbers you record yourself, with the date you saw them."),
    ("youtube", "YouTube Data API", "free_setup",
     "Needs a free Google Cloud API key. YouTube's rules: data is deleted or refreshed after 30 days."),
    ("instagram", "Instagram Business Discovery", "free_setup",
     "Needs a free Meta developer app, your Instagram professional account and a token. Reads other "
     "public Business/Creator accounts."),
    ("google_news", "Google News search RSS", "free",
     "Off by default: review Google's terms before any commercial use."),
    ("tiktok", "TikTok", "unavailable",
     "Research API is closed to commercial companies; Login Kit needs an approved app. Postponed."),
    ("x", "X (Twitter) API", "paid", "Pay-per-use credits. Postponed until a customer needs X data."),
    ("sample", "Sample data (development)", "free", "Fake demo data. Always labelled. Never use in client reports."),
    ("csv_import", "CSV import", "free", "Numbers imported from a spreadsheet you supplied."),
]

# Publisher feeds. verified=1: fetched successfully as a valid RSS feed on 20 Nov 2026 from an
# internet-connected fetcher; these start switched ON. verified=0: could not be confirmed (the site
# refused automated readers or the check tool could not reach it); these start OFF until
# `python manage.py check-sources` succeeds for them on your laptop.
DEFAULT_FEEDS = [
    ("Nairametrics", "https://nairametrics.com/feed/", "NG", 1),
    ("BusinessDay", "https://businessday.ng/feed/", "NG", 1),
    ("Premium Times", "https://www.premiumtimesng.com/feed", "NG", 1),
    ("ThisDay", "https://www.thisdaylive.com/index.php/feed/", "NG", 1),
    ("Techpoint Africa", "https://techpoint.africa/feed/", "NG", 1),
    ("Legit.ng", "https://www.legit.ng/rss/all.rss", "NG", 1),
    ("TechCabal", "https://techcabal.com/feed/", "NG", 0),
    ("Punch", "https://punchng.com/feed/", "NG", 0),
    ("Vanguard", "https://www.vanguardngr.com/feed/", "NG", 0),
    ("The Guardian Nigeria", "https://guardian.ng/feed/", "NG", 0),
    ("TheCable", "https://www.thecable.ng/feed", "NG", 0),
    ("Channels TV", "https://www.channelstv.com/feed/", "NG", 0),
    ("Daily Post", "https://dailypost.ng/feed/", "NG", 0),
]

# Where a number came from, as shown in the interface and reports.
ORIGINS = {
    "sample": ("SAMPLE", "Fictional demo data"),
    "manual": ("MANUAL", "Typed in by you"),
    "csv_import": ("IMPORTED", "From a CSV you imported"),
}


PLATFORM_NAMES = {"tiktok": "TikTok", "youtube": "YouTube", "x": "X", "linkedin": "LinkedIn",
                  "instagram": "Instagram", "facebook": "Facebook", "other": "Other"}


def platform_name(p: str | None) -> str:
    return PLATFORM_NAMES.get((p or "").lower(), (p or "").title())


def origin_of(source_id: str | None) -> tuple[str, str]:
    return ORIGINS.get(source_id or "", ("LIVE", "Collected automatically from a public source"))


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, "
        "applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')))"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.stem in done:
            continue
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (f.stem,))  # two instances may start at once
        applied.append(f.stem)
    if applied or not conn.execute("SELECT 1 FROM organisations LIMIT 1").fetchone():
        _seed_defaults(conn)
    conn.commit()
    return applied


def _seed_defaults(conn: sqlite3.Connection) -> None:
    for sid, name, tier, notes in DEFAULT_SOURCES:
        conn.execute(
            "INSERT INTO data_sources(id,name,cost_tier,notes,enabled) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, cost_tier=excluded.cost_tier, notes=excluded.notes",
            (sid, name, tier, notes, 0 if sid == "google_news" else 1),
        )
    for name, url, country, verified in DEFAULT_FEEDS:
        conn.execute(
            "INSERT OR IGNORE INTO news_feeds(name,url,country,verified,enabled) VALUES (?,?,?,?,?)",
            (name, url, country, verified, verified),
        )
    if not conn.execute("SELECT 1 FROM organisations").fetchone():
        conn.execute("INSERT INTO organisations(name) VALUES ('My workspace')")


def default_org_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT id FROM organisations ORDER BY id LIMIT 1").fetchone()[0]


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
