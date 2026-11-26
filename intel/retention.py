"""Data-retention rules required by platform terms.

YouTube API Services Developer Policies, section III.E.4 (checked Nov 2026): data obtained
without the channel owner's authorisation may be stored for no longer than 30 calendar days,
after which it must be deleted or refreshed; subscriber counts in particular must not be
stored for more than 30 days. We only use an API key (no owner authorisation), so every
YouTube-sourced row older than 30 days since its last refresh is deleted.

Consequence: YouTube growth charts cover at most the last 30 days. Numbers you record
yourself (manual entry / CSV) are your own records and are not affected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

RULES = {"youtube": 30}   # source_id -> max age in days since last refresh


def enforce(conn, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    removed = {}
    for source_id, days in RULES.items():
        cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        m = conn.execute("DELETE FROM metric_snapshots WHERE source_id=? AND collected_at<?", (source_id, cutoff)).rowcount
        c = conn.execute("DELETE FROM content_items WHERE source_id=? AND collected_at<?", (source_id, cutoff)).rowcount
        removed[source_id] = m + c
    return removed
