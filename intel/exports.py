"""CSV exports. One tidy row per data point, every row tagged with its source and origin,
so the file opens cleanly in Excel or Google Sheets."""
from __future__ import annotations

import csv
import io

from .db import origin_of

REPORT_COLUMNS = ["section", "entity", "platform", "measure", "value", "date", "source", "origin", "url", "note"]


def _writer():
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel reads ₦ and accents correctly
    return buf, csv.writer(buf)


def report_csv(facts: dict) -> str:
    buf, w = _writer()
    w.writerow(REPORT_COLUMNS)
    name = facts["entity"]["name"]
    end = facts["period"]["end"][:10]
    w.writerow(["report", name, "", "period", f"{facts['period']['start'][:10]} to {end}", end, "", "", "",
                "SAMPLE DATA - fictional" if facts["is_sample"] else ""])
    for p in facts["platforms"]:
        for m in p["metrics"]:
            g = m.get("growth") or {}
            w.writerow(["metric", name, p["platform"], m["metric"], m["latest"], m["as_of"][:10], m["source"],
                        origin_of(m["source"])[0], "",
                        f"change {g.get('change')} ({g.get('pct')}%) since {g.get('start', '')[:10]}" if g else ""])
    for plat, pts in (facts.get("series") or {}).items():
        for pt in pts:
            src = pt[2] if len(pt) > 2 else ""
            w.writerow(["follower_history", name, plat, "followers", pt[1], pt[0][:10], src,
                        origin_of(src)[0] if src else "", "", ""])
    s = facts["sentiment"]
    for k in ("positive", "neutral", "negative", "net"):
        w.writerow(["sentiment", name, "", k, s[k], end, "builtin word list", "", "", ""])
    for c in facts["competitors"]:
        w.writerow(["share_of_voice", c["name"], "", "mentions", c["mentions"], end, "news feeds", "", "",
                    f"share {c['share']}%"])
    for it in facts["mentions"]["items"]:
        w.writerow(["mention", name, "", it["sentiment_label"], it["title"], (it["published_at"] or "")[:10],
                    it["publisher"], origin_of(it.get("source_id"))[0], it.get("url") or "", ""])
    for p in facts["website_changes"]:
        w.writerow(["website_change", name, "", "page", p["title"], p["collected_at"][:10], "website", "LIVE",
                    p["url"], "prices: " + ", ".join(p["prices"])])
    for sg in facts.get("authenticity") or []:
        w.writerow(["authenticity", name, sg["platform"] or "", sg["name"], sg["level"], end, "rule-based", "", "",
                    sg["detail"]])
    for m in facts["missing"]:
        w.writerow(["missing", name, "", "", "", end, "", "", "", m])
    return buf.getvalue()


def entity_raw_csv(conn, entity_id: int) -> str:
    """Everything stored for one brand/influencer: full history, for your own analysis."""
    buf, w = _writer()
    w.writerow(["type", "platform", "metric_or_kind", "value_or_title", "observed_or_published", "collected_at",
                "source", "origin", "likes", "comments", "views", "sentiment", "url"])
    for r in conn.execute("SELECT * FROM metric_snapshots WHERE entity_id=? ORDER BY observed_at", (entity_id,)):
        w.writerow(["metric", r["platform"], r["metric"], r["value"], r["observed_at"], r["collected_at"],
                    r["source_id"], origin_of(r["source_id"])[0], "", "", "", "", ""])
    for r in conn.execute("SELECT * FROM content_items WHERE entity_id=? ORDER BY published_at", (entity_id,)):
        w.writerow(["content", r["publisher"] or "", r["kind"], r["title"], r["published_at"], r["collected_at"],
                    r["source_id"], origin_of(r["source_id"])[0], r["likes"], r["comments"], r["views"],
                    r["sentiment_label"], r["url"]])
    for r in conn.execute("SELECT * FROM page_snapshots WHERE entity_id=? ORDER BY collected_at", (entity_id,)):
        w.writerow(["page", "", "website", r["title"], "", r["collected_at"], r["source_id"],
                    origin_of(r["source_id"])[0], "", "", "", "", r["url"]])
    return buf.getvalue()


def campaign_csv(f: dict) -> str:
    buf, w = _writer()
    w.writerow(["section", "name", "measure", "baseline", "campaign", "change", "date", "url", "note"])
    c, m = f["campaign"], f["mentions"]
    w.writerow(["campaign", c["name"], "window", f["baseline"]["start"] + " to " + f["baseline"]["end"],
                c["start_date"] + " to " + c["end_date"], "", "", "", f["status"] + (" SAMPLE DATA" if f["is_sample"] else "")])
    w.writerow(["mentions", c["brand"], "press mentions", m["baseline"], m["campaign"], m["pct"], "", "", ""])
    w.writerow(["mentions", c["brand"], "average tone", m["sent_baseline"], m["sent_campaign"], "", "", "", ""])
    for fo in f["followers"]:
        w.writerow(["followers", c["brand"], fo["platform"], fo["start"], fo["end"], fo["pct"], fo["end_at"][:10], "",
                    fo["origin"]])
    for r in f["creators"]:
        w.writerow(["creator", r["name"], "posts/likes/comments/views", "", f"{r['posts']}/{r['likes']}/{r['comments']}/{r['views']}",
                    "", "", "", f"{r['undisclosed']} without disclosure"])
    for p in f["posts"]:
        w.writerow(["post", p["creator"], p["matched"], "", f"{p['likes'] or 0} likes, {p['comments'] or 0} comments",
                    "", (p["published_at"] or "")[:10], p["url"] or "", "disclosed" if p["disclosed"] else "no disclosure"])
    for k in f["keyword_pickup"]:
        w.writerow(["news_tag", k["publisher"] or "", k["matched"], "", k["title"], "", (k["published_at"] or "")[:10],
                    k["url"] or "", ""])
    for x in f["missing"]:
        w.writerow(["missing", "", "", "", "", "", "", "", x])
    return buf.getvalue()
