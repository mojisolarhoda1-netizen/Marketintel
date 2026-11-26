"""Automatic weekly / monthly reports, saved as PDF + CSV files in data/reports/.

They run whenever `python manage.py collect --due` or the scheduler runs (e.g. through
Windows Task Scheduler with collect_due.bat). Nothing is emailed or uploaded.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import ROOT, get_setting, utcnow
from .exports import report_csv
from .pdf import build_pdf
from .render import create_report
from .repo import ValidationError

PERIOD = {"weekly": 7, "monthly": 30}


def create(conn, frequency: str, entity_id: int | None = None, watchlist_id: int | None = None) -> int:
    if frequency not in PERIOD:
        raise ValidationError("Frequency must be weekly or monthly")
    if not entity_id and not watchlist_id:
        raise ValidationError("Choose a brand, creator or watchlist")
    if entity_id:
        e = conn.execute("SELECT is_sample FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not e:
            raise ValidationError("Unknown brand or creator")
        if e[0]:
            raise ValidationError("Sample entries can't have scheduled reports")
    return conn.execute("INSERT INTO report_schedules(entity_id,watchlist_id,frequency) VALUES (?,?,?)",
                        (entity_id, watchlist_id, frequency)).lastrowid


def delete(conn, sid: int) -> None:
    conn.execute("DELETE FROM report_schedules WHERE id=?", (sid,))


def all_schedules(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT s.*, e.name ename, w.name wname FROM report_schedules s
           LEFT JOIN entities e ON e.id=s.entity_id LEFT JOIN watchlists w ON w.id=s.watchlist_id
           ORDER BY s.id""")]


def is_due(s: dict, now: datetime) -> bool:
    if not s["active"]:
        return False
    if not s["last_run"]:
        return True
    last = datetime.fromisoformat(s["last_run"].replace("Z", "+00:00"))
    return now - last >= timedelta(days=PERIOD[s["frequency"]])


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "report"


def run_due(conn, now: datetime | None = None, out_dir: Path | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    base = out_dir or ROOT / "data" / "reports"
    written = []
    prepared, footer = get_setting(conn, "report_prepared_by", ""), get_setting(conn, "report_footer", "")
    from .config import on_vercel
    files_ok = out_dir is not None or not on_vercel()   # Vercel's disk is read-only
    for s in all_schedules(conn):
        if not is_due(s, now):
            continue
        if s["entity_id"]:
            targets, folder = [s["entity_id"]], _safe(s["ename"] or "entity")
        else:
            targets = [r[0] for r in conn.execute(
                "SELECT i.entity_id FROM watchlist_items i JOIN entities e ON e.id=i.entity_id "
                "WHERE i.watchlist_id=? AND e.is_sample=0", (s["watchlist_id"],))]
            folder = _safe(s["wname"] or "watchlist")
        d = base / folder
        if files_ok:
            d.mkdir(parents=True, exist_ok=True)
        for eid in targets:
            rid = create_report(conn, eid, PERIOD[s["frequency"]], "builtin")
            if not files_ok:
                written.append(f"report #{rid} (saved in Reports)")
                continue
            r = dict(conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())
            facts = json.loads(r["facts"])
            stem = f"{_safe(facts['entity']['name'])}-{s['frequency']}-{now.strftime('%Y-%m-%d')}"
            (d / f"{stem}.pdf").write_bytes(build_pdf(r, facts, prepared, footer))
            (d / f"{stem}.csv").write_text(report_csv(facts), encoding="utf-8")
            written.append(str(d / f"{stem}.pdf"))
        conn.execute("UPDATE report_schedules SET last_run=? WHERE id=?", (utcnow(), s["id"]))
        conn.commit()
    return written
