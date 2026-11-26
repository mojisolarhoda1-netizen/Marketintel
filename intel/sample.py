"""Development sample data. Fictional names, every row flagged is_sample=1 and
labelled SAMPLE in the interface. Remove it any time with `python manage.py clear-sample`."""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone

from .analysis.sentiment import score
from .repo import add_account, create_entity, link_competitor

BRANDS = [
    ("SAMPLE Zuma Foods", "Packaged foods", ["Zuma Foods", "Zuma snacks"]),
    ("SAMPLE Kola Snacks", "Packaged foods", []),
    ("SAMPLE Ofada Kitchen", "Restaurants", []),
]
INFLUENCERS = [
    ("SAMPLE Ada Styles", "Fashion", 48_000, 3.1),
    ("SAMPLE Tunde Eats", "Food", 126_000, 2.2),
    ("SAMPLE Naija Tech Tips", "Technology", 310_000, 1.4),
    ("SAMPLE Kemi Glow", "Beauty", 22_000, 6.5),
    ("SAMPLE Lagos Hustle", "Business", 85_000, 0.4),   # deliberately low engagement
]
HEADLINES = [
    ("{b} launches new snack range in Lagos", 1), ("{b} expands distribution to Abuja and Port Harcourt", 1),
    ("Customers complain about {b} delivery delays", -1), ("{b} wins regional packaging award", 1),
    ("{b} raises prices as input costs rise", -1), ("{b} partners with local farmers on cassava supply", 1),
    ("{b} announces quarterly results", 0), ("Social media users praise {b} customer service", 1),
    ("{b} faces backlash over product recall", -1), ("{b} opens new factory, creating 200 jobs", 1),
]


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(conn, days: int = 60, seed_value: int = 7) -> dict:
    rnd = random.Random(seed_value)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ids = {}
    for name, cat, kw in BRANDS:
        eid = create_entity(conn, "brand", name, "NG", cat, "", kw, "Fictional sample brand.", 24, True)
        ids[name] = eid
        add_account(conn, eid, "instagram", name.lower().replace("sample ", "").replace(" ", "_"))
        base = rnd.randint(20_000, 90_000)
        for d in range(days, -1, -3):
            t = now - timedelta(days=d)
            val = int(base * (1 + 0.004 * (days - d)) + rnd.randint(-200, 200))
            _metric(conn, eid, "instagram", "followers", val, t)
        # mentions
        for d in range(days, -1, -1):
            for _ in range(rnd.choice([0, 0, 1, 1, 2]) + (6 if name.startswith("SAMPLE Zuma") and d == 0 else 0)):
                tpl, _tone = rnd.choice(HEADLINES)
                title = tpl.format(b=name.replace("SAMPLE ", ""))
                t = now - timedelta(days=d, hours=rnd.randint(0, 20))
                s, label, _ = score(title)
                h = hashlib.sha256(f"sample|{eid}|{title}|{_iso(t)}".encode()).hexdigest()
                conn.execute(
                    """INSERT OR IGNORE INTO content_items(entity_id,source_id,kind,title,body,url,publisher,published_at,
                       collected_at,sentiment_score,sentiment_label,content_hash,is_sample)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (eid, "sample", "mention", title, "Sample headline for development.", None,
                     rnd.choice(["Sample Daily", "Sample Business News", "Sample Tech Wire"]), _iso(t), _iso(now),
                     s, label, h))
    zuma = ids["SAMPLE Zuma Foods"]
    link_competitor(conn, zuma, ids["SAMPLE Kola Snacks"])
    link_competitor(conn, zuma, ids["SAMPLE Ofada Kitchen"])
    conn.execute(
        """INSERT INTO page_snapshots(entity_id,source_id,url,title,headings,prices,text_hash,changed,collected_at,is_sample)
           VALUES (?,?,?,?,?,?,?,?,?,1)""",
        (ids["SAMPLE Kola Snacks"], "sample", "https://example.com/kola-snacks", "Kola Snacks — New prices",
         json.dumps(["Our range"]), json.dumps(["₦1,200", "₦2,500"]), "samplehash", 1, _iso(now - timedelta(days=2))))

    for name, niche, followers, er in INFLUENCERS:
        eid = create_entity(conn, "influencer", name, "NG", niche, "", [], "Fictional sample creator.", 24, True)
        handle = name.lower().replace("sample ", "").replace(" ", "")
        add_account(conn, eid, "instagram", handle)
        for d in range(days, -1, -6):
            t = now - timedelta(days=d)
            f = int(followers * (1 - 0.002 * d) + rnd.randint(-150, 150))
            if name == "SAMPLE Lagos Hustle" and d < 12:
                f = int(f * 1.35)  # sudden jump, to demonstrate the authenticity signal
            _metric(conn, eid, "instagram", "followers", f, t)
        f = followers * (1.35 if name == "SAMPLE Lagos Hustle" else 1)
        likes = f * er / 100 * 0.94
        comments = f * er / 100 * 0.06
        _metric(conn, eid, "instagram", "avg_likes", round(likes, 1), now)
        _metric(conn, eid, "instagram", "avg_comments", round(comments, 1), now)
        _metric(conn, eid, "instagram", "engagement_rate", round(er, 2), now)
    # sample creator posts mentioning the sample brand, a sample campaign and a sample client watchlist
    ada = conn.execute("SELECT id FROM entities WHERE name='SAMPLE Ada Styles'").fetchone()[0]
    tunde = conn.execute("SELECT id FROM entities WHERE name='SAMPLE Tunde Eats'").fetchone()[0]
    posts = [
        (tunde, "Trying the new Zuma Foods plantain chips #ad #ZumaDecember", 5, 4100, 210),
        (tunde, "Weekend food crawl in Lekki", 9, 2600, 120),
        (ada, "Outfit of the day with my favourite snack from Zuma Foods", 3, 1900, 95),   # no disclosure
        (ada, "Paid partnership with @kola_snacks — new flavours #sponsored", 12, 1500, 60),
    ]
    for eid, cap, d_ago, likes, comments in posts:
        t = now - timedelta(days=d_ago)
        h = hashlib.sha256(f"sample-post|{eid}|{cap}".encode()).hexdigest()
        conn.execute(
            """INSERT OR IGNORE INTO content_items(entity_id,source_id,kind,title,body,publisher,published_at,collected_at,
               likes,comments,content_hash,is_sample) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
            (eid, "sample", "post", cap[:120], cap, "Instagram", _iso(t), _iso(now), likes, comments, h))
    from .partnerships import detect
    detect(conn)
    from . import campaigns as _camp
    from . import watchlists as _wl
    start = (now - timedelta(days=10)).date().isoformat()
    end = (now + timedelta(days=10)).date().isoformat()
    _camp.create(conn, zuma, "SAMPLE Zuma December", start, end, "#ZumaDecember", "Fictional sample campaign.",
                 [tunde, ada])
    wid = _wl.create(conn, "SAMPLE client: Zuma Foods", "Fictional sample watchlist.")
    for name in ("SAMPLE Zuma Foods", "SAMPLE Kola Snacks", "SAMPLE Ofada Kitchen", "SAMPLE Tunde Eats", "SAMPLE Ada Styles"):
        _wl.add(conn, wid, conn.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()[0])

    from .insights import detect_for_entity
    for (eid,) in conn.execute("SELECT id FROM entities WHERE is_sample=1").fetchall():
        detect_for_entity(conn, eid)
    conn.commit()
    return {"brands": len(BRANDS), "influencers": len(INFLUENCERS)}


def _metric(conn, eid, platform, metric, value, t):
    conn.execute(
        """INSERT INTO metric_snapshots(entity_id,source_id,platform,metric,value,observed_at,collected_at,is_sample)
           VALUES (?,?,?,?,?,?,?,1)""", (eid, "sample", platform, metric, value, _iso(t), _iso(t)))


def clear(conn) -> int:
    n = conn.execute("DELETE FROM entities WHERE is_sample=1").rowcount
    conn.execute("DELETE FROM watchlists WHERE name LIKE 'SAMPLE %'")
    conn.execute("DELETE FROM metric_snapshots WHERE is_sample=1")
    conn.commit()
    return n
