"""Campaign tracking: what changed during a campaign compared with the same number of
days just before it (the baseline). For a running campaign, both windows are the number of
days elapsed so far. Everything comes from stored data."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from .db import origin_of, platform_name
from .partnerships import _DISC, _find, brand_terms
from .repo import ValidationError, get_entity, parse_keywords


def _d(s: str) -> date:
    try:
        return date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        raise ValidationError(f"Date '{s}' not understood. Use YYYY-MM-DD.")


def create(conn, brand_id: int, name: str, start: str, end: str, keywords="", notes="",
           influencer_ids: list[int] | None = None) -> int:
    b = get_entity(conn, brand_id)
    if not b or b["kind"] != "brand":
        raise ValidationError("Choose the brand running the campaign")
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("Give the campaign a name")
    s, e = _d(start), _d(end)
    if e < s:
        raise ValidationError("End date is before start date")
    if (e - s).days > 366:
        raise ValidationError("Campaigns longer than a year aren't supported")
    cid = conn.execute("INSERT INTO campaigns(brand_id,name,start_date,end_date,keywords,notes) VALUES (?,?,?,?,?,?)",
                       (brand_id, name, s.isoformat(), e.isoformat(), json.dumps(parse_keywords(keywords)),
                        (notes or "").strip()[:2000] or None)).lastrowid
    for iid in influencer_ids or []:
        add_influencer(conn, cid, iid)
    return cid


def add_influencer(conn, cid: int, influencer_id: int) -> None:
    e = get_entity(conn, influencer_id)
    if not e or e["kind"] != "influencer":
        raise ValidationError("Unknown creator")
    conn.execute("INSERT OR IGNORE INTO campaign_influencers(campaign_id,influencer_id) VALUES (?,?)",
                 (cid, influencer_id))


def remove_influencer(conn, cid: int, influencer_id: int) -> None:
    conn.execute("DELETE FROM campaign_influencers WHERE campaign_id=? AND influencer_id=?", (cid, influencer_id))


def delete(conn, cid: int) -> None:
    conn.execute("DELETE FROM campaigns WHERE id=?", (cid,))


def get(conn, cid: int) -> dict | None:
    r = conn.execute("SELECT c.*, e.name brand, e.is_sample FROM campaigns c JOIN entities e ON e.id=c.brand_id "
                     "WHERE c.id=?", (cid,)).fetchone()
    return dict(r) if r else None


def all_campaigns(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT c.*, e.name brand, e.is_sample, COUNT(ci.influencer_id) creators FROM campaigns c
           JOIN entities e ON e.id=c.brand_id LEFT JOIN campaign_influencers ci ON ci.campaign_id=c.id
           GROUP BY c.id ORDER BY c.start_date DESC""")]


def influencers(conn, cid: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT e.* FROM campaign_influencers ci JOIN entities e ON e.id=ci.influencer_id
           WHERE ci.campaign_id=? ORDER BY e.name""", (cid,))]


def _pct(a, b):
    return round((a - b) / b * 100, 1) if b else None


def facts(conn, cid: int, today: date | None = None) -> dict:
    c = get(conn, cid)
    if not c:
        raise ValueError("campaign not found")
    today = today or date.today()
    s, e = date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"])
    eff_end = min(e, today)                       # a running campaign is measured up to today
    # Baseline = the same number of days as measured so far, immediately before the start,
    # so a campaign that is half-way through is compared like with like.
    length = max((eff_end - s).days + 1, 1) if today >= s else (e - s).days + 1
    b_start, b_end = s - timedelta(days=length), s - timedelta(days=1)
    W = (f"{s.isoformat()}T00:00:00Z", f"{eff_end.isoformat()}T23:59:59Z")
    B = (f"{b_start.isoformat()}T00:00:00Z", f"{b_end.isoformat()}T23:59:59Z")
    kws = json.loads(c["keywords"] or "[]")
    brand = get_entity(conn, c["brand_id"])
    terms = brand_terms(conn, brand) + kws
    missing = []

    def mentions(window):
        rows = conn.execute(
            """SELECT title, body, url, publisher, published_at, sentiment_label, sentiment_score, source_id
               FROM content_items WHERE entity_id=? AND kind IN ('mention','article')
               AND COALESCE(published_at,collected_at) BETWEEN ? AND ?
               ORDER BY published_at DESC""", (c["brand_id"], *window)).fetchall()
        return [dict(r) for r in rows]
    mw, mb = mentions(W), mentions(B)

    def sent(rows):
        if not rows:
            return None
        return round(sum(r["sentiment_score"] or 0 for r in rows) / len(rows), 3)

    # campaign hashtags / slogans in the press (brand stories + topic stories)
    kw_hits = []
    if kws:
        for r in mw:
            hit = _find(f"{r['title']} {r['body'] or ''}", kws)
            if hit:
                kw_hits.append({**r, "matched": hit})
        for r in conn.execute("""SELECT title, body, url, publisher, published_at, sentiment_label FROM topic_items
                                 WHERE COALESCE(published_at,collected_at) BETWEEN ? AND ?""", W).fetchall():
            hit = _find(f"{r['title']} {r['body'] or ''}", kws)
            if hit and not any(k["url"] == r["url"] for k in kw_hits):
                kw_hits.append({**dict(r), "matched": hit, "source_id": "rss_news"})
    else:
        missing.append("No campaign hashtags or slogans saved, so hashtag pick-up can't be measured.")

    # brand followers at start vs latest within window
    followers = []
    for (plat,) in conn.execute("SELECT DISTINCT platform FROM metric_snapshots WHERE entity_id=? AND "
                                "metric='followers'", (c["brand_id"],)).fetchall():
        before = conn.execute("""SELECT value, observed_at, source_id FROM metric_snapshots WHERE entity_id=? AND
                                 platform=? AND metric='followers' AND observed_at<=? ORDER BY observed_at DESC LIMIT 1""",
                              (c["brand_id"], plat, W[0])).fetchone()
        after = conn.execute("""SELECT value, observed_at, source_id FROM metric_snapshots WHERE entity_id=? AND
                                platform=? AND metric='followers' AND observed_at BETWEEN ? AND ?
                                ORDER BY observed_at DESC LIMIT 1""", (c["brand_id"], plat, *W)).fetchone()
        if before and after and after["observed_at"] > before["observed_at"]:
            followers.append({"platform": plat, "start": before["value"], "start_at": before["observed_at"],
                              "end": after["value"], "end_at": after["observed_at"],
                              "change": after["value"] - before["value"], "pct": _pct(after["value"], before["value"]),
                              "origin": origin_of(after["source_id"])[0]})
        else:
            missing.append(f"{plat}: need a follower number from before the campaign and one during it.")

    # creator posts during the window that mention the brand or campaign tags
    creator_rows, posts = [], []
    team = influencers(conn, cid)
    if not team:
        missing.append("No creators added to this campaign.")
    for inf in team:
        rows = conn.execute(
            """SELECT id, title, body, url, published_at, likes, comments, views, publisher, source_id FROM content_items
               WHERE entity_id=? AND kind IN ('post','video') AND COALESCE(published_at,collected_at) BETWEEN ? AND ?""",
            (inf["id"], *W)).fetchall()
        mine = []
        for r in rows:
            text = f"{r['title'] or ''}\n{r['body'] or ''}"
            hit = _find(text, terms)
            if hit:
                d = _DISC.search(text)
                mine.append({**dict(r), "matched": hit, "disclosed": bool(d), "creator": inf["name"]})
        tot = lambda k: sum((p[k] or 0) for p in mine)
        creator_rows.append({"id": inf["id"], "name": inf["name"], "posts": len(mine), "likes": tot("likes"),
                             "comments": tot("comments"), "views": tot("views"),
                             "undisclosed": sum(1 for p in mine if not p["disclosed"]),
                             "collected_posts_in_window": len(rows)})
        posts += mine
        if not rows:
            missing.append(f"{inf['name']}: no posts collected during the campaign (connect Instagram/YouTube "
                           f"or this creator didn't post).")
    posts.sort(key=lambda p: (p["likes"] or 0) + (p["comments"] or 0), reverse=True)

    status = "upcoming" if today < s else "running" if today <= e else "finished"
    return {
        "campaign": {k: c[k] for k in ("id", "name", "brand", "brand_id", "start_date", "end_date", "notes")},
        "keywords": kws, "status": status, "is_sample": bool(c["is_sample"]),
        "window": {"start": s.isoformat(), "end": eff_end.isoformat(), "days": (eff_end - s).days + 1 if today >= s else 0},
        "baseline": {"start": b_start.isoformat(), "end": b_end.isoformat(), "days": length},
        "mentions": {"campaign": len(mw), "baseline": len(mb), "pct": _pct(len(mw), len(mb)),
                     "sent_campaign": sent(mw), "sent_baseline": sent(mb), "items": mw[:15]},
        "keyword_pickup": kw_hits[:20], "keyword_pickup_count": len(kw_hits),
        "followers": followers,
        "creators": creator_rows, "posts": posts[:15],
        "totals": {"posts": len(posts), "likes": sum(p["likes"] or 0 for p in posts),
                   "comments": sum(p["comments"] or 0 for p in posts),
                   "views": sum(p["views"] or 0 for p in posts),
                   "undisclosed": sum(1 for p in posts if not p["disclosed"])},
        "missing": missing,
    }


def summary_lines(f: dict) -> list[str]:
    m, t = f["mentions"], f["totals"]
    verb = "ran" if f["status"] == "finished" else "runs"
    L = [f"{f['campaign']['name']} for {f['campaign']['brand']} {verb} {f['campaign']['start_date']} to "
         f"{f['campaign']['end_date']} ({f['status']}). Measured {f['window']['start']} to {f['window']['end']}; "
         f"baseline = the {f['baseline']['days']} days before ({f['baseline']['start']} to {f['baseline']['end']})."]
    if m["campaign"] or m["baseline"]:
        chg = f" ({m['pct']:+.0f}%)" if m["pct"] is not None else ""
        L.append(f"Press mentions: {m['campaign']} during the campaign vs {m['baseline']} in the baseline{chg}.")
    if m["sent_campaign"] is not None and m["sent_baseline"] is not None:
        L.append(f"Average tone moved from {m['sent_baseline']:+.2f} to {m['sent_campaign']:+.2f}.")
    for fo in f["followers"]:
        L.append(f"{platform_name(fo['platform'])} followers: {int(fo['start']):,} → {int(fo['end']):,} ({fo['pct']:+.1f}%).")
    if t["posts"]:
        L.append(f"{t['posts']} creator posts mentioned the brand or campaign tags: {t['likes']:,} likes, "
                 f"{t['comments']:,} comments" + (f", {t['views']:,} views" if t["views"] else "") + ".")
        if t["undisclosed"]:
            L.append(f"{t['undisclosed']} of those posts had no visible #ad / sponsorship disclosure — worth checking.")
    if f["keywords"]:
        L.append(f"Campaign tags ({', '.join(f['keywords'])}) appeared in {f['keyword_pickup_count']} news stories.")
    return L
