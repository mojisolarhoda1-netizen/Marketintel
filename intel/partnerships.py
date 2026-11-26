"""Influencer–brand partnership detection.

Reads the captions / video descriptions already collected for your tracked creators and
looks for your tracked brands: the brand name, its extra search terms, its Instagram handle
(@handle) and a hashtag form (#BrandName). It also notes whether the post carries a
disclosure (#ad, #sponsored, "paid partnership", ...).

It is a text match on public posts — a lead to check, not proof of a paid deal.
"""
from __future__ import annotations

import json
import re

DISCLOSURES = [
    r"#ad\b", r"#advert\b", r"#sponsored\b", r"#spon\b", r"#partner\b", r"#partnership\b", r"#collab\b",
    r"#gifted\b", r"#brandambassador\b", r"#ambassador\b", r"paid partnership", r"in partnership with",
    r"sponsored by", r"brand ambassador", r"thanks to @\w+ for", r"use (?:my )?code\b", r"promo code",
]
_DISC = re.compile("|".join(DISCLOSURES), re.IGNORECASE)


def brand_terms(conn, brand: dict) -> list[str]:
    terms = [brand["name"]] + [k for k in json.loads(brand["keywords"] or "[]") if len(k) >= 3]
    compact = re.sub(r"[^A-Za-z0-9]", "", brand["name"])
    if len(compact) >= 3:
        terms.append("#" + compact)
    for (h,) in conn.execute("SELECT handle FROM social_accounts WHERE entity_id=? AND platform IN "
                             "('instagram','tiktok','x')", (brand["id"],)):
        terms.append("@" + h.lstrip("@"))
    return list(dict.fromkeys(t for t in terms if t))


def _find(text: str, terms: list[str]) -> str | None:
    for t in terms:
        if t.startswith(("@", "#")):
            pat = re.escape(t) + r"(?![\w])"
        else:
            pat = r"(?<![\w@#])" + re.escape(t) + r"(?![\w])"
        if re.search(pat, text, flags=re.IGNORECASE):
            return t
    return None


def detect(conn, influencer_id: int | None = None) -> int:
    """Scan creators' posts for tracked brands. Returns number of new partnership rows."""
    brands = [dict(r) for r in conn.execute("SELECT * FROM entities WHERE kind='brand'")]
    if not brands:
        return 0
    terms = {b["id"]: brand_terms(conn, b) for b in brands}
    sample_of = {b["id"]: b["is_sample"] for b in brands}
    sql = """SELECT c.id, c.entity_id, c.title, c.body, e.is_sample FROM content_items c
             JOIN entities e ON e.id=c.entity_id WHERE e.kind='influencer' AND c.kind IN ('post','video')"""
    args = []
    if influencer_id:
        sql += " AND c.entity_id=?"; args.append(influencer_id)
    new = 0
    for c in conn.execute(sql, args).fetchall():
        text = f"{c['title'] or ''}\n{c['body'] or ''}"
        disc = _DISC.search(text)
        for bid, t in terms.items():
            if sample_of[bid] != c["is_sample"]:
                continue  # never link sample data to real data
            hit = _find(text, t)
            if not hit:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO partnerships(influencer_id,brand_id,content_id,matched,disclosed,disclosure) "
                "VALUES (?,?,?,?,?,?)", (c["entity_id"], bid, c["id"], hit, 1 if disc else 0,
                                          disc.group(0) if disc else None))
            new += cur.rowcount
    conn.commit()
    return new


def for_brand(conn, brand_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT p.*, e.name influencer, c.title, c.url, c.published_at, c.likes, c.comments, c.views, c.publisher
           FROM partnerships p JOIN entities e ON e.id=p.influencer_id JOIN content_items c ON c.id=p.content_id
           WHERE p.brand_id=? ORDER BY c.published_at DESC""", (brand_id,))]


def for_influencer(conn, influencer_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT p.*, e.name brand, c.title, c.url, c.published_at, c.likes, c.comments, c.views, c.publisher
           FROM partnerships p JOIN entities e ON e.id=p.brand_id JOIN content_items c ON c.id=p.content_id
           WHERE p.influencer_id=? ORDER BY c.published_at DESC""", (influencer_id,))]
