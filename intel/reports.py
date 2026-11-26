"""Report facts: every number a report may use, computed from stored data only.

The AI engines receive this dict and nothing else. Anything we could not
measure goes into `missing`, and the report prints it.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from .analysis import authenticity
from .analysis.trends import (growth, iso, latest_metrics, metric_series, sentiment_summary,
                              share_of_voice, top_words)
from .db import origin_of
from .repo import accounts_for, competitors_of, get_entity

METRIC_LABELS = {
    "followers": "Followers / subscribers", "posts": "Posts / videos", "total_views": "Total views",
    "avg_likes": "Avg likes (recent posts)", "avg_comments": "Avg comments (recent posts)",
    "avg_views": "Avg views (recent posts)", "engagement_rate": "Engagement rate % (likes+comments ÷ followers)",
}
PROVIDER_FOR_PLATFORM = {"youtube": "youtube", "instagram": "instagram", "tiktok": "tiktok", "x": "x"}


def build_facts(conn, entity_id: int, days: int = 30, now: datetime | None = None) -> dict:
    e = get_entity(conn, entity_id)
    if not e:
        raise ValueError("entity not found")
    now = now or datetime.now(timezone.utc)
    start, end = iso(now - timedelta(days=days)), iso(now)
    accounts = accounts_for(conn, entity_id)
    missing: list[str] = []

    # ---- metrics per platform ----
    platforms = []
    lm = latest_metrics(conn, entity_id)
    for plat in sorted({p for (p, _m) in lm} | {a["platform"] for a in accounts}):
        rows = []
        for metric, label in METRIC_LABELS.items():
            latest = lm.get((plat, metric))
            if not latest:
                continue
            g = growth(metric_series(conn, entity_id, metric, plat, since=start))
            rows.append({"metric": metric, "label": label, "latest": latest["value"],
                         "as_of": latest["observed_at"], "source": latest["source_id"],
                         "origin": origin_of(latest["source_id"])[0], "growth": g})
        if not rows:
            missing.append(f"No {plat} numbers collected yet.")
        elif not any(r["metric"] == "followers" and r["growth"] for r in rows):
            missing.append(f"Only one {plat} follower snapshot in this period, so growth can't be measured yet.")
        platforms.append({"platform": plat, "metrics": rows})
    if not accounts:
        missing.append("No social accounts saved for this " + e["kind"] + ".")

    # follower history for charts (period only)
    series = {}
    for p in platforms:
        s = metric_series(conn, entity_id, "followers", p["platform"], since=start)
        if len(s) >= 2:
            series[p["platform"]] = [[x["observed_at"], x["value"], x["source_id"]] for x in s]
    if any(p["platform"] == "youtube" for p in platforms):
        missing.append("YouTube history is limited to the last 30 days: YouTube's API rules require data "
                       "collected without the channel owner's permission to be deleted or refreshed within 30 days.")

    # ---- mentions & content ----
    mentions = [dict(r) for r in conn.execute(
        """SELECT title, url, publisher, published_at, sentiment_label, sentiment_score, source_id, is_sample
           FROM content_items WHERE entity_id=? AND kind IN ('mention','article')
           AND COALESCE(published_at, collected_at) BETWEEN ? AND ? ORDER BY published_at DESC""",
        (entity_id, start, end))]
    posts = [dict(r) for r in conn.execute(
        """SELECT title, url, publisher, published_at, likes, comments, views, kind FROM content_items
           WHERE entity_id=? AND kind IN ('post','video') AND COALESCE(published_at, collected_at) BETWEEN ? AND ?
           ORDER BY COALESCE(likes,0)+COALESCE(comments,0) DESC LIMIT 5""", (entity_id, start, end))]
    if not mentions:
        missing.append("No press mentions found in the enabled news feeds for this period.")

    sentiment = sentiment_summary(conn, entity_id, start, end)
    publishers = Counter(m["publisher"] or "Unknown" for m in mentions).most_common(6)
    themes = top_words([m["title"] for m in mentions], 10)

    # ---- competitors ----
    comp = []
    if e["kind"] == "brand":
        rivals = competitors_of(conn, entity_id)
        if rivals:
            comp = share_of_voice(conn, [entity_id] + [r["id"] for r in rivals], start, end)
            for c in comp:
                c["sentiment"] = sentiment_summary(conn, c["id"], start, end)
                f = [v for (p, m), v in latest_metrics(conn, c["id"]).items() if m == "followers"]
                c["followers_total"] = sum(x["value"] for x in f) if f else None
        else:
            missing.append("No competitors linked, so there is no share-of-voice comparison.")

    # ---- website changes ----
    pages = [dict(r) for r in conn.execute(
        """SELECT url, title, prices, changed, collected_at FROM page_snapshots WHERE entity_id=?
           AND collected_at BETWEEN ? AND ? AND changed=1 ORDER BY collected_at DESC""", (entity_id, start, end))]
    for p in pages:
        p["prices"] = json.loads(p["prices"] or "[]")
    if e["kind"] == "brand" and not e.get("website"):
        missing.append("No website saved, so website changes are not monitored.")

    alerts = [dict(r) for r in conn.execute(
        "SELECT kind, severity, message, created_at FROM alerts WHERE entity_id=? AND created_at BETWEEN ? AND ? "
        "ORDER BY created_at DESC LIMIT 10", (entity_id, start, end))]

    signals = authenticity.signals(conn, entity_id) if e["kind"] == "influencer" else []

    # ---- sources used & unavailable ----
    used = [dict(r) for r in conn.execute(
        """SELECT r.source_id, d.name, MAX(r.finished_at) last, r.status FROM collection_runs r
           JOIN data_sources d ON d.id=r.source_id WHERE r.entity_id=? AND r.status IN ('ok','partial')
           GROUP BY r.source_id""", (entity_id,))]
    unavailable = [dict(r) for r in conn.execute(
        """SELECT r.source_id, d.name, r.message FROM collection_runs r JOIN data_sources d ON d.id=r.source_id
           WHERE r.entity_id=? AND r.status='unavailable' AND r.id IN
             (SELECT MAX(id) FROM collection_runs WHERE entity_id=? GROUP BY source_id)""", (entity_id, entity_id))]
    for u in unavailable:
        missing.append(f"{u['name']}: {u['message']}")
    for sid, label in (("manual", "Manual entry (typed in)"), ("csv_import", "CSV import")):
        last = conn.execute("SELECT MAX(collected_at) FROM metric_snapshots WHERE entity_id=? AND source_id=?",
                            (entity_id, sid)).fetchone()[0]
        if last:
            used.append({"source_id": sid, "name": label, "last": last, "status": "ok"})

    # ---- provenance: how many observations came from where ----
    prov = {}
    for table, col in (("metric_snapshots", "observed_at"), ("content_items", "COALESCE(published_at, collected_at)")):
        for r in conn.execute(f"SELECT source_id, COUNT(*) n FROM {table} WHERE entity_id=? AND {col} BETWEEN ? AND ? "
                              f"GROUP BY source_id", (entity_id, start, end)):
            o = origin_of(r["source_id"])[0]
            prov[o] = prov.get(o, 0) + r["n"]
    sample_flag = bool(e["is_sample"])
    if sample_flag:
        used.append({"source_id": "sample", "name": "Sample data (fictional)", "last": None, "status": "ok"})
    sample = sample_flag or conn.execute(
        "SELECT 1 FROM metric_snapshots WHERE entity_id=? AND is_sample=1 LIMIT 1", (entity_id,)).fetchone() is not None

    return {
        "entity": {"id": e["id"], "name": e["name"], "kind": e["kind"], "country": e["country"],
                   "category": e["category"], "website": e["website"]},
        "period": {"start": start, "end": end, "days": days},
        "generated_at": iso(now),
        "is_sample": sample,
        "platforms": platforms,
        "series": series,
        "provenance": prov,
        "mentions": {"count": len(mentions), "items": mentions[:15], "publishers": publishers, "themes": themes},
        "sentiment": sentiment,
        "top_content": posts,
        "competitors": comp,
        "website_changes": pages,
        "alerts": alerts,
        "authenticity": signals,
        "sources_used": used,
        "missing": missing,
    }
