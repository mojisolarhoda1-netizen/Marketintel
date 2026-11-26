"""Automatic alerts from stored data: mention spikes, sentiment drops, follower jumps."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .analysis.trends import daily_counts, iso, metric_series, parse, zscore_spike


def raise_alert(conn, entity_id, kind, severity, message, data=None, dedupe_key=None) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO alerts(entity_id,kind,severity,message,data,dedupe_key) VALUES (?,?,?,?,?,?)",
        (entity_id, kind, severity, message, json.dumps(data or {}), dedupe_key))
    return cur.rowcount > 0


def detect_for_entity(conn, entity_id: int, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    name = conn.execute("SELECT name FROM entities WHERE id=?", (entity_id,)).fetchone()[0]
    raised = 0
    today = now.strftime("%Y-%m-%d")

    # 1. mention spike: today's (or latest day's) count vs the previous 29 days
    counts = daily_counts(conn, [entity_id], iso(now - timedelta(days=29)), iso(now))
    spike = zscore_spike(list(counts.values()))
    if spike:
        raised += raise_alert(conn, entity_id, "mention_spike", "warning",
                              f"{name}: {spike['last']} mentions today vs a usual {spike['mean']}/day",
                              spike, f"spike:{entity_id}:{today}")

    # 2. sentiment drop: last 7 days vs the 21 days before
    def avg_sent(a, b):
        r = conn.execute(
            """SELECT AVG(sentiment_score), COUNT(*) FROM content_items WHERE entity_id=? AND sentiment_score IS NOT NULL
               AND COALESCE(published_at, collected_at) BETWEEN ? AND ?""", (entity_id, iso(a), iso(b))).fetchone()
        return r[0], r[1]
    recent, n1 = avg_sent(now - timedelta(days=7), now)
    before, n2 = avg_sent(now - timedelta(days=28), now - timedelta(days=7))
    if n1 >= 5 and n2 >= 5 and recent is not None and before is not None and before - recent >= 0.25:
        raised += raise_alert(conn, entity_id, "sentiment_drop", "warning",
                              f"{name}: sentiment fell from {before:+.2f} to {recent:+.2f} (last 7 days, {n1} items)",
                              {"before": before, "recent": recent}, f"sent:{entity_id}:{today}")

    # 3. follower jump between the last two snapshots on any platform
    plats = [r[0] for r in conn.execute(
        "SELECT DISTINCT platform FROM metric_snapshots WHERE entity_id=? AND metric='followers'", (entity_id,))]
    for p in plats:
        s = metric_series(conn, entity_id, "followers", p)
        if len(s) < 2 or s[-2]["value"] <= 0:
            continue
        a, b = s[-2], s[-1]
        days = max((parse(b["observed_at"]) - parse(a["observed_at"])).total_seconds() / 86400, 1)
        pct = (b["value"] - a["value"]) / a["value"] * 100
        if abs(pct) / days >= 10 and abs(b["value"] - a["value"]) >= 500:
            raised += raise_alert(conn, entity_id, "follower_jump", "info",
                                  f"{name}: {p} followers changed {pct:+.1f}% "
                                  f"({int(a['value']):,} → {int(b['value']):,}) in {days:.0f} day(s)",
                                  {"platform": p, "pct": pct}, f"fjump:{entity_id}:{p}:{b['observed_at'][:10]}")
    return raised
