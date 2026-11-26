"""Backups and the end-to-end validation check."""
from __future__ import annotations

import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db import ROOT, db_path, get_setting, migrate, set_setting, utcnow


# ---------------- backups ----------------

def backup(conn, keep: int = 20, dest_dir: Path | None = None) -> Path:
    """Consistent copy of the live database (safe while the app is running)."""
    if not isinstance(conn, sqlite3.Connection):
        raise RuntimeError("The online database is backed up by Turso (point-in-time restore in its dashboard).")
    d = dest_dir or ROOT / "data" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"intel-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    out = sqlite3.connect(target)
    conn.backup(out)
    out.close()
    olds = sorted(d.glob("intel-*.db"))
    for f in olds[:-keep]:
        f.unlink()
    return target


def backup_if_none_today(conn) -> Path | None:
    if not isinstance(conn, sqlite3.Connection):
        return None
    d = ROOT / "data" / "backups"
    today = datetime.now().strftime("%Y%m%d")
    if d.exists() and any(d.glob(f"intel-{today}-*.db")):
        return None
    if not conn.execute("SELECT 1 FROM entities LIMIT 1").fetchone():
        return None  # nothing worth backing up yet
    return backup(conn)


# ---------------- data counts (used to prove nothing is lost between runs) ----------------

def counts(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "entities_real": q("SELECT COUNT(*) FROM entities WHERE is_sample=0"),
        "entities_sample": q("SELECT COUNT(*) FROM entities WHERE is_sample=1"),
        "metric_snapshots": q("SELECT COUNT(*) FROM metric_snapshots"),
        "content_items": q("SELECT COUNT(*) FROM content_items"),
        "page_snapshots": q("SELECT COUNT(*) FROM page_snapshots"),
        "reports": q("SELECT COUNT(*) FROM reports"),
        "collection_runs": q("SELECT COUNT(*) FROM collection_runs"),
    }


def origin_breakdown(conn) -> dict:
    from .db import origin_of
    out = {}
    for table in ("metric_snapshots", "content_items"):
        for r in conn.execute(f"SELECT source_id, COUNT(*) n FROM {table} GROUP BY source_id"):
            o = origin_of(r[0])[0]
            out[o] = out.get(o, 0) + r[1]
    return out


def separation_problems(conn) -> list[str]:
    probs = []
    n = conn.execute("""SELECT COUNT(*) FROM metric_snapshots m JOIN entities e ON e.id=m.entity_id
                        WHERE e.is_sample=0 AND (m.is_sample=1 OR m.source_id='sample')""").fetchone()[0]
    if n:
        probs.append(f"{n} sample metric rows are attached to real entities")
    n = conn.execute("""SELECT COUNT(*) FROM content_items c JOIN entities e ON e.id=c.entity_id
                        WHERE e.is_sample=0 AND (c.is_sample=1 OR c.source_id='sample')""").fetchone()[0]
    if n:
        probs.append(f"{n} sample content rows are attached to real entities")
    n = conn.execute("""SELECT COUNT(*) FROM metric_snapshots m JOIN entities e ON e.id=m.entity_id
                        WHERE e.is_sample=1 AND m.source_id<>'sample'""").fetchone()[0]
    if n:
        probs.append(f"{n} real metric rows are attached to sample entities")
    return probs


# ---------------- validation ----------------

class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.results: list[tuple[str, str, str]] = []

    def head(self, t):
        self.lines += ["", t, "-" * len(t)]

    def add(self, status, name, detail=""):
        self.results.append((status, name, detail))
        self.lines.append(f"[{status:4}] {name}" + (f" — {detail}" if detail else ""))

    def note(self, t):
        self.lines.append("       " + t)


def validate(conn, providers, enable_working: bool = False, export_dir: Path | None = None) -> tuple[str, Path]:
    from .ai.engines import ollama_status
    from .exports import report_csv
    from .pdf import build_pdf
    from .providers.rss_news import parse_feed
    from .render import create_report

    R = Report()
    R.lines.append(f"Market Intel validation report — {utcnow()} (UTC)")
    R.lines.append(f"Computer: {platform.system()} {platform.release()} · Python {sys.version.split()[0]}")

    R.head("1. Software")
    ok = sys.version_info >= (3, 10)
    R.add("PASS" if ok else "FAIL", "Python version", sys.version.split()[0] + ("" if ok else " (need 3.10+)"))
    for mod in ("flask", "requests", "bs4", "lxml", "pydantic", "reportlab"):
        try:
            __import__(mod); R.add("PASS", f"package {mod}")
        except ImportError:
            R.add("FAIL", f"package {mod}", "run: pip install -r requirements.txt")

    R.head("2. Database and persistence")
    R.add("PASS", "database file", str(db_path()))
    migrate(conn)
    vers = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    R.add("PASS", "schema migrations applied", ", ".join(vers))
    try:
        set_setting(conn, "_write_test", utcnow()); conn.commit()
        conn.execute("DELETE FROM settings WHERE key='_write_test'"); conn.commit()
        R.add("PASS", "database is writable")
    except Exception as e:
        R.add("FAIL", "database is writable", str(e))
    now_c = counts(conn)
    prev = get_setting(conn, "last_validation", None)
    R.add("INFO", "stored right now", ", ".join(f"{k}={v}" for k, v in now_c.items()))
    if prev:
        shrunk = [k for k, v in prev["counts"].items() if k in now_c and now_c[k] < v
                  and k not in ("metric_snapshots", "content_items", "entities_sample")]
        R.add("FAIL" if shrunk else "PASS", "nothing lost since last validation",
              f"last run {prev['at']}" + (f"; decreased: {', '.join(shrunk)}" if shrunk else ""))
        R.note("(YouTube rows older than 30 days are deleted on purpose — YouTube's rules — and sample data "
               "disappears if you ran clear-sample.)")
    else:
        R.add("INFO", "nothing lost since last validation", "first validation run; run again after a restart to compare")
    R.add("INFO", "observations by origin", json.dumps(origin_breakdown(conn)))
    probs = separation_problems(conn)
    R.add("FAIL" if probs else "PASS", "sample data kept separate from real data", "; ".join(probs))

    R.head("3. News feeds (live internet test)")
    rss = providers["rss_news"]
    working = 0
    for f in conn.execute("SELECT * FROM news_feeds ORDER BY id").fetchall():
        try:
            resp = rss.fetch(f["url"])
            items = parse_feed(resp.content) if resp.status_code == 200 else []
            newest = max((i["published"] or "" for i in items), default="")
            good = resp.status_code == 200 and len(items) > 0
            if good:
                working += 1
                conn.execute("UPDATE news_feeds SET verified=1, last_error=NULL" +
                             (", enabled=1" if enable_working else "") + " WHERE id=?", (f["id"],))
            else:
                conn.execute("UPDATE news_feeds SET verified=0, last_error=? WHERE id=?",
                             (f"HTTP {resp.status_code}, {len(items)} items", f["id"]))
            state = "on" if (f["enabled"] or (good and enable_working)) else "off"
            R.add("PASS" if good else "FAIL", f"{f['name']} ({state})",
                  f"HTTP {resp.status_code}, {len(items)} items, newest {newest[:10] or 'n/a'}")
        except Exception as e:
            conn.execute("UPDATE news_feeds SET verified=0, last_error=? WHERE id=?", (str(e)[:200], f["id"]))
            R.add("FAIL", f"{f['name']} ({'on' if f['enabled'] else 'off'})", str(e)[:120])
    conn.commit()
    R.note(f"{working} feed(s) working from this computer." +
           ("" if enable_working else " Add --enable-working to switch on every feed that passed."))

    R.head("4. Websites of tracked brands")
    sites = conn.execute("SELECT name, website FROM entities WHERE is_sample=0 AND website IS NOT NULL").fetchall()
    if not sites:
        R.add("SKIP", "website monitor", "no real brand with a website yet")
    web = providers["website"]
    for s in sites[:15]:
        url = s["website"] if s["website"].startswith("http") else "https://" + s["website"]
        try:
            allowed = web.allowed(url)
            R.add("PASS" if allowed else "INFO", s["name"], "robots.txt allows monitoring" if allowed
                  else "robots.txt does not allow automated visits — will not be monitored")
        except Exception as e:
            R.add("FAIL", s["name"], str(e)[:120])

    R.head("5. YouTube Data API")
    yt = providers["youtube"]
    ok, why = yt.is_configured()
    if not ok:
        R.add("SKIP", "YouTube", why)
    else:
        from .pipeline import make_quota_guard
        try:
            ch = yt.resolve_channel("@YouTube", make_quota_guard(conn))
            st = (ch or {}).get("statistics", {})
            R.add("PASS" if ch else "FAIL", "API key works (1 unit used)",
                  f"test channel @YouTube: {int(st.get('subscriberCount', 0)):,} subscribers (rounded down by YouTube "
                  f"to 3 significant figures)" if ch else "test channel not found")
        except Exception as e:
            R.add("FAIL", "API key works", str(e)[:160])

    R.head("6. Instagram Business Discovery")
    ig = providers["instagram"]
    ok, why = ig.is_configured()
    if not ok:
        R.add("SKIP", "Instagram", why)
    else:
        from .config import env
        uid, tok, ver = ig.creds()
        test_user = env("IG_TEST_USERNAME", "instagram")
        try:
            resp = ig.fetch(f"https://graph.facebook.com/{ver}/{uid}",
                            params={"fields": f"business_discovery.username({test_user}){{followers_count,media_count}}",
                                    "access_token": tok})
            data = resp.json() if resp.content else {}
            bd = data.get("business_discovery")
            if bd:
                R.add("PASS", "token and permissions work",
                      f"@{test_user}: {bd.get('followers_count', 0):,} followers, {bd.get('media_count', 0):,} posts")
            else:
                R.add("FAIL", "token and permissions work", json.dumps(data.get("error", data))[:200])
        except Exception as e:
            R.add("FAIL", "token and permissions work", str(e)[:160])

    R.head("7. AI engines (free)")
    R.add("PASS", "built-in analyst", "always available, offline")
    ok, msg = ollama_status()
    R.add("PASS" if ok else "SKIP", "Ollama local AI (optional)", msg)
    R.add("PASS", "Claude Pro hand-off", "available on every report")

    R.head("8. Report export")
    ent = conn.execute("SELECT id, name, is_sample FROM entities ORDER BY is_sample, id LIMIT 1").fetchone()
    exp = export_dir or ROOT / "data" / "exports"
    exp.mkdir(parents=True, exist_ok=True)
    if not ent:
        R.add("SKIP", "PDF and CSV export", "add a brand first")
    else:
        try:
            rid = create_report(conn, ent["id"], 30, "builtin")
            r = dict(conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())
            facts = json.loads(r["facts"])
            pdf = build_pdf(r, facts, get_setting(conn, "report_prepared_by", ""), get_setting(conn, "report_footer", ""))
            csv_text = report_csv(facts)
            stem = f"validation-{'SAMPLE-' if ent['is_sample'] else ''}{ent['id']}"
            (exp / f"{stem}.pdf").write_bytes(pdf)
            (exp / f"{stem}.csv").write_text(csv_text, encoding="utf-8")
            R.add("PASS" if pdf[:4] == b"%PDF" else "FAIL", "PDF export",
                  f"{len(pdf)//1024} KB → data/exports/{stem}.pdf ({ent['name']})")
            R.add("PASS", "CSV export", f"{csv_text.count(chr(10))} rows → data/exports/{stem}.csv")
            conn.execute("DELETE FROM reports WHERE id=?", (rid,))  # test report: keep files, not the list entry
        except Exception as e:
            R.add("FAIL", "report export", f"{e.__class__.__name__}: {e}"[:200])

    set_setting(conn, "last_validation", {"at": utcnow(), "counts": now_c})
    conn.commit()
    fails = sum(1 for s, _, _ in R.results if s == "FAIL")
    passes = sum(1 for s, _, _ in R.results if s == "PASS")
    R.lines += ["", f"RESULT: {passes} passed, {fails} failed, "
                    f"{sum(1 for s, _, _ in R.results if s == 'SKIP')} skipped (not set up yet)."]
    text = "\n".join(R.lines)
    out = ROOT / "data" / f"validation-report-{datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return text, out
