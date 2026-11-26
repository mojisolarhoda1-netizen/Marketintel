"""Topic listening: watch industry keywords and hashtags across every enabled news feed.

Unlike brand tracking, a topic is not tied to one company: "fintech", "Detty December",
"fuel price", "#BBNaija". Stories are matched by whole word, scored for tone, and
checked for which of your tracked brands they mention (co-mentions).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from .analysis.sentiment import score
from .analysis.trends import top_words
from .db import default_org_id, utcnow
from .repo import ValidationError, parse_keywords


def create(conn, name: str, keywords, country: str = "") -> int:
    name = (name or "").strip()
    kws = parse_keywords(keywords) or ([name] if name else [])
    if not name or len(name) > 80:
        raise ValidationError("Give the topic a name (up to 80 characters)")
    if conn.execute("SELECT 1 FROM topics WHERE lower(name)=lower(?)", (name,)).fetchone():
        raise ValidationError(f"A topic called '{name}' already exists")
    return conn.execute("INSERT INTO topics(org_id,name,keywords,country) VALUES (?,?,?,?)",
                        (default_org_id(conn), name, json.dumps(kws), (country or "").upper()[:2] or None)).lastrowid


def delete(conn, tid: int) -> None:
    conn.execute("DELETE FROM topics WHERE id=?", (tid,))


def get(conn, tid: int) -> dict | None:
    r = conn.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
    return dict(r) if r else None


def all_topics(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT t.*, COUNT(i.id) n, MAX(i.published_at) latest FROM topics t
           LEFT JOIN topic_items i ON i.topic_id=t.id GROUP BY t.id ORDER BY t.name COLLATE NOCASE""")]


def _match(text: str, kws: list[str]) -> str | None:
    for k in kws:
        pat = re.escape(k) if k.startswith("#") else r"(?<![\w])" + re.escape(k) + r"(?![\w])"
        if re.search(pat, text, flags=re.IGNORECASE):
            return k
    return None


def collect(conn, rss_provider) -> dict:
    """Match every active topic against every enabled feed. One fetch per feed."""
    topics = [t for t in all_topics(conn) if t["active"]]
    feeds = rss_provider.feeds(conn)
    out = {"topics": len(topics), "feeds": len(feeds), "new": 0, "errors": []}
    if not topics or not feeds:
        return out
    now = utcnow()
    for f in feeds:
        try:
            items = rss_provider.load(f["url"])
        except Exception as e:
            out["errors"].append(f"{f['name']}: {e}")
            continue
        for it in items:
            text = f"{it['title']} {it['summary']}"
            for t in topics:
                hit = _match(text, json.loads(t["keywords"] or "[]"))
                if not hit:
                    continue
                h = hashlib.sha256(f"rss_news|{it['guid'] or it['url'] or it['title']}".encode()).hexdigest()
                s, label, _ = score(text)
                cur = conn.execute(
                    """INSERT OR IGNORE INTO topic_items(topic_id,source_id,title,body,url,publisher,published_at,
                       collected_at,matched,sentiment_score,sentiment_label,content_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (t["id"], "rss_news", it["title"], it["summary"], it["url"] or None, f["name"],
                     it["published"], now, hit, s, label, h))
                out["new"] += cur.rowcount
    conn.commit()
    return out


def summary(conn, tid: int, start: str, end: str) -> dict:
    items = [dict(r) for r in conn.execute(
        """SELECT * FROM topic_items WHERE topic_id=? AND COALESCE(published_at,collected_at) BETWEEN ? AND ?
           ORDER BY COALESCE(published_at,collected_at) DESC""", (tid, start, end))]
    c = Counter(i["sentiment_label"] for i in items)
    n = len(items)
    # co-mentions: which tracked (real) brands appear in topic stories
    brands = conn.execute("SELECT id, name, keywords FROM entities WHERE kind='brand' AND is_sample=0").fetchall()
    co = Counter()
    for b in brands:
        terms = [b["name"]] + [k for k in json.loads(b["keywords"] or "[]")]
        for i in items:
            if _match(f"{i['title']} {i['body'] or ''}", terms):
                co[(b["id"], b["name"])] += 1
    days = {}
    if n:
        rows = conn.execute(
            """SELECT substr(COALESCE(published_at,collected_at),1,10) d, COUNT(*) c FROM topic_items
               WHERE topic_id=? AND COALESCE(published_at,collected_at) BETWEEN ? AND ? GROUP BY d""",
            (tid, start, end)).fetchall()
        got = {r["d"]: r["c"] for r in rows}
        days = {d: got.get(d, 0) for d in _days(start, end)}
    return {
        "items": items, "count": n,
        "positive": c.get("positive", 0), "neutral": c.get("neutral", 0), "negative": c.get("negative", 0),
        "net": round((c.get("positive", 0) - c.get("negative", 0)) / n * 100, 1) if n else None,
        "publishers": Counter(i["publisher"] for i in items).most_common(6),
        "words": top_words([i["title"] for i in items], 12),
        "co_mentions": [{"id": k[0], "name": k[1], "n": v} for k, v in co.most_common(10)],
        "days": days,
    }


def _days(start: str, end: str) -> list[str]:
    from datetime import date, timedelta
    d, e = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    out = []
    while d <= e:
        out.append(d.isoformat()); d += timedelta(days=1)
    return out


def week_change(conn, tid: int, now_iso: str, week_ago: str, two_weeks_ago: str) -> tuple[int, int]:
    q = """SELECT COUNT(*) FROM topic_items WHERE topic_id=? AND COALESCE(published_at,collected_at) BETWEEN ? AND ?"""
    return (conn.execute(q, (tid, week_ago, now_iso)).fetchone()[0],
            conn.execute(q, (tid, two_weeks_ago, week_ago)).fetchone()[0])
