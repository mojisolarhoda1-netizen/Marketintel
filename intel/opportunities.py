"""Opportunity feed: rule-based findings from stored data that suggest something to act on.

Every item states the rule, the numbers behind it and the period. No guessing.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .analysis.trends import iso, metric_series, sentiment_summary
from .platform_names import nice


def find(conn, include_sample: bool = False, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    w1, w2, m1 = iso(now - timedelta(days=7)), iso(now - timedelta(days=14)), iso(now - timedelta(days=30))
    end = iso(now)
    scope = "" if include_sample else "AND e.is_sample=0"
    out = []

    def add(kind, title, detail, entity=None, score=1.0):
        out.append({"kind": kind, "title": title, "detail": detail, "entity": entity, "score": score})

    # 1. A competitor's coverage turned negative -> chance to win their unhappy customers
    for r in conn.execute(f"""SELECT DISTINCT l.brand_id, b.name bname, l.competitor_id, e.name, e.is_sample
                              FROM competitor_links l JOIN entities e ON e.id=l.competitor_id
                              JOIN entities b ON b.id=l.brand_id WHERE 1=1 {scope}""").fetchall():
        s = sentiment_summary(conn, r["competitor_id"], w1, end)
        if s["count"] >= 3 and s["negative"] > s["positive"] and s["negative"] >= 2:
            add("competitor_weakness", f"{r['name']} is getting negative coverage",
                f"{s['negative']} of {s['count']} stories in the last 7 days scored negative. "
                f"{r['bname']} could speak to customers looking for an alternative.",
                {"id": r["competitor_id"], "name": r["name"], "is_sample": r["is_sample"]}, 3 + s["negative"])

    # 2. A competitor changed prices on its website
    for r in conn.execute(f"""SELECT p.entity_id, e.name, e.is_sample, p.prices, p.collected_at, p.url
                              FROM page_snapshots p JOIN entities e ON e.id=p.entity_id
                              JOIN competitor_links l ON l.competitor_id=e.id
                              WHERE p.changed=1 AND p.collected_at>=? {scope} GROUP BY p.id""", (m1,)).fetchall():
        prices = json.loads(r["prices"] or "[]")
        add("price_change", f"{r['name']} changed its website",
            f"Change detected {r['collected_at'][:10]} on {r['url']}" +
            (f"; prices now shown: {', '.join(prices[:6])}." if prices else "."),
            {"id": r["entity_id"], "name": r["name"], "is_sample": r["is_sample"]}, 2.5)

    # 3. Your brand's share of press attention dropped vs competitors (this week vs last)
    for r in conn.execute(f"""SELECT DISTINCT b.id, b.name, b.is_sample FROM competitor_links l
                              JOIN entities b ON b.id=l.brand_id JOIN entities e ON e.id=b.id WHERE 1=1 {scope}""").fetchall():
        ids = [r["id"]] + [x[0] for x in conn.execute("SELECT competitor_id FROM competitor_links WHERE brand_id=?",
                                                       (r["id"],))]
        def share(a, b):
            ph = ",".join("?" * len(ids))
            rows = dict(conn.execute(f"""SELECT entity_id, COUNT(*) FROM content_items WHERE entity_id IN ({ph})
                        AND kind IN ('mention','article') AND COALESCE(published_at,collected_at) BETWEEN ? AND ?
                        GROUP BY entity_id""", [*ids, a, b]).fetchall())
            tot = sum(rows.values())
            return (rows.get(r["id"], 0) / tot * 100, tot) if tot else (None, 0)
        now_s, n1 = share(w1, end)
        old_s, n2 = share(w2, w1)
        if now_s is not None and old_s is not None and n1 >= 5 and n2 >= 5 and old_s - now_s >= 15:
            add("share_drop", f"{r['name']} is losing share of voice",
                f"Share of press mentions fell from {old_s:.0f}% to {now_s:.0f}% week on week "
                f"({n2} → {n1} stories across the competitor set).",
                {"id": r["id"], "name": r["name"], "is_sample": r["is_sample"]}, 3)

    # 4. Fast-growing creators (30-day follower growth)
    for r in conn.execute(f"SELECT id, name, category, is_sample FROM entities e WHERE kind='influencer' {scope}").fetchall():
        for (plat,) in conn.execute("SELECT DISTINCT platform FROM metric_snapshots WHERE entity_id=? AND metric='followers'",
                                    (r["id"],)).fetchall():
            s = metric_series(conn, r["id"], "followers", plat, since=m1)
            if len(s) >= 2 and s[0]["value"] >= 1000:
                pct = (s[-1]["value"] - s[0]["value"]) / s[0]["value"] * 100
                if pct >= 10:
                    add("rising_creator", f"{r['name']} is growing fast on {nice(plat)}",
                        f"Followers up {pct:.1f}% ({int(s[0]['value']):,} → {int(s[-1]['value']):,}) between "
                        f"{s[0]['observed_at'][:10]} and {s[-1]['observed_at'][:10]}"
                        + (f". Niche: {r['category']}." if r["category"] else ".") +
                        " Check the authenticity signals before approaching.",
                        {"id": r["id"], "name": r["name"], "is_sample": r["is_sample"]}, 2 + pct / 20)

    # 5. A topic is heating up
    for t in conn.execute("SELECT id, name FROM topics WHERE active=1").fetchall():
        q = "SELECT COUNT(*) FROM topic_items WHERE topic_id=? AND COALESCE(published_at,collected_at) BETWEEN ? AND ?"
        a = conn.execute(q, (t["id"], w1, end)).fetchone()[0]
        b = conn.execute(q, (t["id"], w2, w1)).fetchone()[0]
        if a >= 5 and a >= 2 * max(b, 1):
            add("topic_rising", f"'{t['name']}' is trending in the news",
                f"{a} stories this week vs {b} last week. A timely content or PR angle.", None, 2 + a / 10)

    # 6. Undisclosed sponsored-looking posts (risk for brands)
    for r in conn.execute(f"""SELECT p.brand_id, b.name bname, e.name iname, e.is_sample, COUNT(*) n
                              FROM partnerships p JOIN entities b ON b.id=p.brand_id JOIN entities e ON e.id=p.influencer_id
                              JOIN content_items c ON c.id=p.content_id
                              WHERE p.disclosed=0 AND COALESCE(c.published_at,c.collected_at)>=? {scope}
                              GROUP BY p.brand_id, p.influencer_id""", (m1,)).fetchall():
        add("disclosure_risk", f"{r['iname']} mentioned {r['bname']} without a visible disclosure",
            f"{r['n']} post(s) in the last 30 days mention the brand without #ad / sponsorship wording. "
            "If it's a paid deal, ask for a disclosure.", {"id": r["brand_id"], "name": r["bname"],
                                                           "is_sample": r["is_sample"]}, 1.5)
    return sorted(out, key=lambda x: x["score"], reverse=True)
