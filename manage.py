"""Command line tools.

  python manage.py init-db          create / upgrade the database
  python manage.py seed-sample      add clearly-labelled fictional demo data
  python manage.py clear-sample     remove all demo data
  python manage.py validate         full self-check: database, persistence, live feeds, YouTube,
                                    Instagram, exports. Writes data/validation-report-*.txt
  python manage.py validate --enable-working   ...and switch on every news feed that works
  python manage.py check-sources    quick test of data sources only (needs internet)
  python manage.py backup           save a copy of the database to data/backups/
  python manage.py collect --due    collect for everything that's due (use with Task Scheduler / cron)
  python manage.py collect --id 3   collect for one brand/influencer now
  python manage.py scheduler        keep running and collect every hour while the laptop is on
  python manage.py report --id 3    write a report (built-in engine) and print its id
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from intel.config import load_env

load_env()

from intel.db import connect, migrate  # noqa: E402


def cmd_init(_):
    with connect() as c:
        print("Applied:", migrate(c) or "nothing new")


def cmd_seed(_):
    from intel import sample
    c = connect(); migrate(c)
    if c.execute("SELECT 1 FROM entities WHERE is_sample=1").fetchone():
        print("Sample data already present."); return
    print("Seeded:", sample.seed(c))


def cmd_clear(_):
    from intel import sample
    c = connect(); migrate(c)
    print("Removed sample entities:", sample.clear(c))


def cmd_check(_):
    from intel.ai.engines import ollama_status
    from intel.providers import build_providers
    from intel.providers.rss_news import parse_feed
    c = connect(); migrate(c)
    provs = build_providers()
    print("\nData sources")
    for pid, p in provs.items():
        ok, why = p.is_configured()
        print(f"  [{'ready' if ok else 'off  '}] {p.name:32} {why}")
    print("\nNews feeds")
    rss = provs["rss_news"]
    for f in c.execute("SELECT * FROM news_feeds ORDER BY id").fetchall():
        try:
            resp = rss.fetch(f["url"])
            items = parse_feed(resp.content) if resp.status_code == 200 else []
            ok = resp.status_code == 200 and len(items) > 0
            c.execute("UPDATE news_feeds SET verified=?, last_error=? WHERE id=?",
                      (1 if ok else 0, None if ok else f"HTTP {resp.status_code}, {len(items)} items", f["id"]))
            print(f"  [{'ok ' if ok else 'BAD'}] {f['name']:16} {len(items)} items  {f['url']}")
        except Exception as e:
            c.execute("UPDATE news_feeds SET verified=0, last_error=? WHERE id=?", (str(e)[:200], f["id"]))
            print(f"  [BAD] {f['name']:16} {e}")
    c.commit()
    print("\nAI:", ollama_status()[1])


def cmd_validate(a):
    from intel.maintenance import validate
    from intel.providers import build_providers
    c = connect(); migrate(c)
    text, path = validate(c, build_providers(), enable_working=a.enable_working)
    print(text)
    print(f"\nSaved to {path}")


def cmd_backup(_):
    from intel.maintenance import backup
    c = connect(); migrate(c)
    print("Backup saved:", backup(c))


def cmd_collect(a):
    from intel import schedules, topics
    from intel.pipeline import Pipeline, due_entities
    c = connect(); migrate(c)
    ids = [a.id] if a.id else due_entities(c)
    pipe = Pipeline(c)
    if not a.id:
        t = topics.collect(c, pipe.providers["rss_news"])
        if t["topics"]:
            print(f"Topics: {t['new']} new stories across {t['topics']} topic(s)"
                  + (f"; {len(t['errors'])} feed problem(s)" if t["errors"] else ""))
    if not ids:
        print("No brands or creators are due.")
    for eid in ids:
        name = c.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()
        print(f"\n{name[0] if name else eid}")
        for r in pipe.run_entity(eid):
            print(f"  {r['source']:12} {r['status']:12} found {r['found']:4}  new {r['new']:4}  {r['message'][:90]}")
    if not a.id:
        for path in schedules.run_due(c):
            print("Scheduled report saved:", path)


def cmd_scheduler(a):
    print(f"Scheduler running: checking every {a.minutes} minutes. Ctrl+C to stop.")
    while True:
        try:
            cmd_collect(argparse.Namespace(id=None))
        except Exception as e:  # keep going
            logging.exception("collection round failed: %s", e)
        time.sleep(a.minutes * 60)


def cmd_report(a):
    from intel.render import create_report
    c = connect(); migrate(c)
    rid = create_report(c, a.id, a.days, a.engine)
    print("Report", rid, "created. Open it at http://127.0.0.1:5000/reports/%d" % rid)


def main(argv=None):
    # Windows consoles and log files may not be UTF-8; never crash on a ₦ or an arrow.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Market Intel command line")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=cmd_init)
    sub.add_parser("seed-sample").set_defaults(fn=cmd_seed)
    sub.add_parser("clear-sample").set_defaults(fn=cmd_clear)
    sub.add_parser("check-sources").set_defaults(fn=cmd_check)
    pv = sub.add_parser("validate"); pv.add_argument("--enable-working", action="store_true")
    pv.set_defaults(fn=cmd_validate)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    pc = sub.add_parser("collect"); pc.add_argument("--id", type=int); pc.add_argument("--due", action="store_true")
    pc.set_defaults(fn=cmd_collect)
    ps = sub.add_parser("scheduler"); ps.add_argument("--minutes", type=int, default=60); ps.set_defaults(fn=cmd_scheduler)
    pr = sub.add_parser("report"); pr.add_argument("--id", type=int, required=True)
    pr.add_argument("--days", type=int, default=30); pr.add_argument("--engine", default="builtin")
    pr.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
