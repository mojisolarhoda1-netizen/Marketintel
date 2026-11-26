"""Trend maths on stored observations. Pure functions + small DB queries.

Nothing here estimates or fills gaps: if there isn't enough data, functions
return None and the caller shows "not enough data yet".
"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def metric_series(conn, entity_id: int, metric: str, platform: str | None = None,
                  since: str | None = None) -> list[dict]:
    q = ("SELECT observed_at, value, platform, source_id, is_sample FROM metric_snapshots "
         "WHERE entity_id=? AND metric=?")
    args: list = [entity_id, metric]
    if platform:
        q += " AND platform=?"; args.append(platform)
    if since:
        q += " AND observed_at>=?"; args.append(since)
    q += " ORDER BY observed_at"
    return [dict(r) for r in conn.execute(q, args)]


def latest_metrics(conn, entity_id: int) -> dict[tuple[str, str], dict]:
    rows = conn.execute(
        """SELECT m.* FROM metric_snapshots m
           JOIN (SELECT platform, metric, MAX(observed_at) mx FROM metric_snapshots
                 WHERE entity_id=? GROUP BY platform, metric) l
             ON m.platform IS l.platform AND m.metric=l.metric AND m.observed_at=l.mx
           WHERE m.entity_id=? ORDER BY m.id DESC""", (entity_id, entity_id)).fetchall()
    out = {}
    for r in rows:
        out.setdefault((r["platform"] or "", r["metric"]), dict(r))
    return out


def growth(series: list[dict]) -> dict | None:
    """Change between first and last observation in the series."""
    if len(series) < 2:
        return None
    first, last = series[0], series[-1]
    if first["value"] == 0:
        pct = None
    else:
        pct = (last["value"] - first["value"]) / first["value"] * 100
    return {"from": first["value"], "to": last["value"], "change": last["value"] - first["value"],
            "pct": None if pct is None else round(pct, 2),
            "start": first["observed_at"], "end": last["observed_at"]}


def daily_counts(conn, entity_ids: list[int], start: str, end: str) -> dict[str, int]:
    if not entity_ids:
        return {}
    ph = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"""SELECT substr(COALESCE(published_at, collected_at),1,10) d, COUNT(*) c FROM content_items
            WHERE entity_id IN ({ph}) AND kind IN ('mention','article')
              AND COALESCE(published_at, collected_at) BETWEEN ? AND ? GROUP BY d""",
        [*entity_ids, start, end]).fetchall()
    counts = {r["d"]: r["c"] for r in rows}
    days, d = {}, parse(start).date()
    while d <= parse(end).date():
        days[d.isoformat()] = counts.get(d.isoformat(), 0)
        d += timedelta(days=1)
    return days


def zscore_spike(values: list[float], threshold: float = 2.5, min_points: int = 7) -> dict | None:
    """Is the last value unusually high versus the ones before it?"""
    if len(values) < min_points + 1:
        return None
    history, last = values[:-1], values[-1]
    mean = statistics.mean(history)
    sd = statistics.pstdev(history)
    if sd == 0:
        return {"z": None, "mean": mean, "last": last} if last > mean + 2 and last >= 3 else None
    z = (last - mean) / sd
    return {"z": round(z, 2), "mean": round(mean, 2), "last": last} if z >= threshold and last >= 3 else None


def sentiment_summary(conn, entity_id: int, start: str, end: str) -> dict:
    rows = conn.execute(
        """SELECT sentiment_label l, sentiment_score s FROM content_items
           WHERE entity_id=? AND COALESCE(published_at, collected_at) BETWEEN ? AND ?
             AND sentiment_label IS NOT NULL""", (entity_id, start, end)).fetchall()
    c = Counter(r["l"] for r in rows)
    n = len(rows)
    return {
        "count": n,
        "positive": c.get("positive", 0), "neutral": c.get("neutral", 0), "negative": c.get("negative", 0),
        "avg": round(sum(r["s"] for r in rows) / n, 3) if n else None,
        "net": round((c.get("positive", 0) - c.get("negative", 0)) / n * 100, 1) if n else None,
    }


def share_of_voice(conn, entity_ids: list[int], start: str, end: str) -> list[dict]:
    if not entity_ids:
        return []
    ph = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"""SELECT e.id, e.name, COUNT(c.id) n FROM entities e
            LEFT JOIN content_items c ON c.entity_id=e.id AND c.kind IN ('mention','article')
                 AND COALESCE(c.published_at, c.collected_at) BETWEEN ? AND ?
            WHERE e.id IN ({ph}) GROUP BY e.id ORDER BY n DESC""", [start, end, *entity_ids]).fetchall()
    total = sum(r["n"] for r in rows)
    return [{"id": r["id"], "name": r["name"], "mentions": r["n"],
             "share": round(r["n"] / total * 100, 1) if total else None} for r in rows]


def top_words(texts: list[str], n: int = 12) -> list[tuple[str, int]]:
    import re
    stop = set("""a an the and or of to in on for with at by from is are was were be been it its this that
    as but not no will has have had he she they we you i his her their our your said says new over after
    into than more about up out also can would could all which who what when how why there here just like
    one two nigeria nigerian lagos company firm year years ngn naira million billion per cent percent""".split())
    words = Counter()
    for t in texts:
        for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (t or "").lower()):
            if w not in stop:
                words[w] += 1
    return words.most_common(n)
