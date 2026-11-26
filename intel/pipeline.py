"""Collection pipeline.

request -> pick providers -> collect (retry, rate limits, quota) -> validate ->
de-duplicate -> normalise -> save snapshots -> sentiment -> alerts -> run log.

A source that is not set up or postponed is recorded as 'unavailable' with the
reason, so the dashboard can say exactly why a number is missing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from . import insights, partnerships, retention
from .analysis.sentiment import score
from .db import utcnow
from .providers import build_providers
from .providers.base import Context, ProviderUnavailable, RateLimited, TransientError
from .providers.youtube import daily_budget, daily_search_budget
from .repo import accounts_for, get_entity

log = logging.getLogger("intel.pipeline")
MAX_ATTEMPTS = 3


def content_hash(source_id: str, obs) -> str:
    basis = obs.external_id or obs.url or f"{obs.title}|{obs.published_at}"
    return hashlib.sha256(f"{source_id}|{basis}".encode()).hexdigest()


def make_quota_guard(conn):
    budgets = {"youtube": daily_budget(), "youtube_search": daily_search_budget()}

    def spend(source_id: str, units: int) -> bool:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute("SELECT units FROM api_usage WHERE source_id=? AND day=?", (source_id, day)).fetchone()
        used = row[0] if row else 0
        if source_id in budgets and used + units > budgets[source_id]:
            return False
        conn.execute("""INSERT INTO api_usage(source_id,day,units) VALUES (?,?,?)
                        ON CONFLICT(source_id,day) DO UPDATE SET units=units+excluded.units""",
                     (source_id, day, units))
        conn.commit()
        return True
    return spend


class Pipeline:
    def __init__(self, conn, providers=None, sleep=time.sleep):
        self.conn = conn
        self.providers = providers or build_providers()
        self.sleep = sleep
        self.spend = make_quota_guard(conn)

    def enabled_sources(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT id FROM data_sources WHERE enabled=1")}

    def run_entity(self, entity_id: int, only: list[str] | None = None) -> list[dict]:
        entity = get_entity(self.conn, entity_id)
        if not entity:
            raise ValueError(f"entity {entity_id} not found")
        accounts = accounts_for(self.conn, entity_id)
        enabled = self.enabled_sources()
        results = []
        for pid, prov in self.providers.items():
            if only and pid not in only:
                continue
            if not prov.supports(entity, accounts):
                continue
            if pid not in enabled:
                continue
            results.append(self._run_one(entity, accounts, prov))
            accounts = accounts_for(self.conn, entity_id)  # pick up resolved ids
        insights.detect_for_entity(self.conn, entity_id)
        partnerships.detect(self.conn, entity_id if entity["kind"] == "influencer" else None)
        retention.enforce(self.conn)
        self.conn.commit()
        return results

    def _run_one(self, entity, accounts, prov) -> dict:
        now = utcnow()
        run_id = self.conn.execute(
            "INSERT INTO collection_runs(entity_id,source_id,started_at,status) VALUES (?,?,?, 'running')",
            (entity["id"], prov.id, now)).lastrowid
        self.conn.commit()
        status, message, found, new, attempts = "failed", "", 0, 0, 0
        ok, why = prov.is_configured()
        if not ok:
            status, message = "unavailable", why
        else:
            ctx = Context(conn=self.conn, now=now, spend_units=self.spend)
            delay = 1.0
            while attempts < MAX_ATTEMPTS:
                attempts += 1
                try:
                    res = prov.collect(entity, accounts, ctx)
                    found = res.count
                    new = self._save(entity["id"], prov.id, res, now)
                    status = "partial" if res.warnings else "ok"
                    message = "; ".join(res.warnings)[:500]
                    break
                except ProviderUnavailable as e:
                    status, message = "unavailable", str(e); break
                except RateLimited as e:
                    message = str(e)
                    if e.retry_after > 60 or attempts >= MAX_ATTEMPTS:
                        status = "skipped"; break
                    self.sleep(e.retry_after)
                except (TransientError, ValidationError) as e:
                    message = str(e)[:500]
                    if attempts < MAX_ATTEMPTS:
                        self.sleep(delay); delay *= 2
                except Exception as e:  # never let one source crash the run
                    log.exception("provider %s crashed", prov.id)
                    status, message = "failed", f"{e.__class__.__name__}: {e}"[:500]; break
        self.conn.execute(
            """UPDATE collection_runs SET finished_at=?, status=?, attempts=?, items_found=?, items_new=?, message=?
               WHERE id=?""", (utcnow(), status, max(attempts, 1), found, new, message or None, run_id))
        self.conn.execute("UPDATE data_sources SET last_status=?, last_checked=? WHERE id=?",
                          (status, utcnow(), prov.id))
        if status == "failed":
            insights.raise_alert(self.conn, entity["id"], "source_failure", "warning",
                                 f"{prov.name} failed for {entity['name']}: {message[:160]}",
                                 {"source": prov.id}, f"fail:{prov.id}:{entity['id']}:{now[:10]}")
        self.conn.commit()
        return {"source": prov.id, "status": status, "found": found, "new": new, "message": message}

    def _save(self, entity_id: int, source_id: str, res, now: str) -> int:
        new = 0
        for upd in res.account_updates:
            self.conn.execute("UPDATE social_accounts SET external_id=?, url=COALESCE(url, ?) WHERE id=?",
                              (upd.get("external_id"), upd.get("url"), upd["id"]))
        for m in res.metrics:
            self.conn.execute(
                """INSERT INTO metric_snapshots(entity_id,account_id,source_id,platform,metric,value,observed_at,
                   collected_at) VALUES (?,?,?,?,?,?,?,?)""",
                (entity_id, m.account_id, source_id, m.platform, m.metric, m.value, m.observed_at, now))
            new += 1
        for c in res.content:
            s, label, _ = score(f"{c.title or ''}. {c.body or ''}")
            h = content_hash(source_id, c)
            existing = self.conn.execute("SELECT id FROM content_items WHERE entity_id=? AND content_hash=?",
                                         (entity_id, h)).fetchone()
            if existing:  # duplicate: keep one row, refresh its engagement numbers
                self.conn.execute("UPDATE content_items SET likes=COALESCE(?,likes), comments=COALESCE(?,comments), "
                                  "views=COALESCE(?,views), collected_at=? WHERE id=?",
                                  (c.likes, c.comments, c.views, now, existing[0]))
                continue
            self.conn.execute(
                """INSERT INTO content_items(entity_id,account_id,source_id,kind,external_id,url,title,body,author,
                   publisher,published_at,collected_at,likes,comments,views,sentiment_score,sentiment_label,content_hash)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entity_id, c.account_id, source_id, c.kind, c.external_id, c.url, c.title, c.body, c.author,
                 c.publisher, c.published_at, now, c.likes, c.comments, c.views, s, label, h))
            new += 1
        for p in res.pages:
            prev = self.conn.execute(
                "SELECT * FROM page_snapshots WHERE entity_id=? AND url=? ORDER BY id DESC LIMIT 1",
                (entity_id, p.url)).fetchone()
            if prev and prev["text_hash"] == p.text_hash:
                continue  # unchanged: nothing new to store
            changed = 1 if prev else 0
            self.conn.execute(
                """INSERT INTO page_snapshots(entity_id,source_id,url,title,meta_description,headings,prices,
                   text_hash,changed,collected_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (entity_id, source_id, p.url, p.title, p.meta_description, json.dumps(p.headings),
                 json.dumps(p.prices), p.text_hash, changed, now))
            new += 1
            if changed:
                old_prices = set(json.loads(prev["prices"] or "[]"))
                added, removed = sorted(set(p.prices) - old_prices), sorted(old_prices - set(p.prices))
                what = []
                if (prev["title"] or "") != (p.title or ""): what.append("title")
                if json.loads(prev["headings"] or "[]") != p.headings: what.append("headings")
                if added or removed: what.append("prices")
                insights.raise_alert(
                    self.conn, entity_id, "page_change", "info",
                    f"Website changed ({', '.join(what) or 'content'}): {p.url}",
                    {"url": p.url, "prices_added": added, "prices_removed": removed},
                    f"page:{entity_id}:{p.text_hash[:16]}")
        return new


def due_entities(conn) -> list[int]:
    """Entities whose last successful collection is older than their refresh interval."""
    rows = conn.execute(
        """SELECT e.id, e.refresh_hours, MAX(r.finished_at) last FROM entities e
           LEFT JOIN collection_runs r ON r.entity_id=e.id AND r.status IN ('ok','partial')
           WHERE e.active=1 AND e.is_sample=0 GROUP BY e.id""").fetchall()
    now = datetime.now(timezone.utc)
    due = []
    for r in rows:
        if not r["last"]:
            due.append(r["id"]); continue
        last = datetime.fromisoformat(r["last"].replace("Z", "+00:00"))
        if now - last >= timedelta(hours=r["refresh_hours"]):
            due.append(r["id"])
    return due
