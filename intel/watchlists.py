"""Watchlists: named groups of brands and creators, usually one per client."""
from __future__ import annotations

from .analysis.trends import latest_metrics, sentiment_summary
from .db import default_org_id
from .repo import ValidationError


def create(conn, name: str, notes: str = "") -> int:
    name = (name or "").strip()
    if not name or len(name) > 100:
        raise ValidationError("Give the watchlist a name (up to 100 characters)")
    if conn.execute("SELECT 1 FROM watchlists WHERE lower(name)=lower(?)", (name,)).fetchone():
        raise ValidationError(f"A watchlist called '{name}' already exists")
    return conn.execute("INSERT INTO watchlists(org_id,name,notes) VALUES (?,?,?)",
                        (default_org_id(conn), name, (notes or "").strip()[:1000] or None)).lastrowid


def delete(conn, wid: int) -> None:
    conn.execute("DELETE FROM watchlists WHERE id=?", (wid,))


def add(conn, wid: int, entity_id: int) -> None:
    if not conn.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone():
        raise ValidationError("Unknown brand or creator")
    conn.execute("INSERT OR IGNORE INTO watchlist_items(watchlist_id,entity_id) VALUES (?,?)", (wid, entity_id))


def remove(conn, wid: int, entity_id: int) -> None:
    conn.execute("DELETE FROM watchlist_items WHERE watchlist_id=? AND entity_id=?", (wid, entity_id))


def get(conn, wid: int) -> dict | None:
    r = conn.execute("SELECT * FROM watchlists WHERE id=?", (wid,)).fetchone()
    return dict(r) if r else None


def all_lists(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT w.*, COUNT(i.entity_id) n,
                  SUM(e.kind='brand') brands, SUM(e.kind='influencer') creators
           FROM watchlists w LEFT JOIN watchlist_items i ON i.watchlist_id=w.id
           LEFT JOIN entities e ON e.id=i.entity_id GROUP BY w.id ORDER BY w.name COLLATE NOCASE""")]


def members(conn, wid: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT e.* FROM watchlist_items i JOIN entities e ON e.id=i.entity_id
           WHERE i.watchlist_id=? ORDER BY e.kind, e.name COLLATE NOCASE""", (wid,))]


def lists_for_entity(conn, entity_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT w.* FROM watchlist_items i JOIN watchlists w ON w.id=i.watchlist_id
           WHERE i.entity_id=? ORDER BY w.name""", (entity_id,))]


def summary(conn, wid: int, start: str, end: str) -> list[dict]:
    rows = []
    for e in members(conn, wid):
        lm = latest_metrics(conn, e["id"])
        rows.append({
            **e,
            "followers": sum(v["value"] for (p, m), v in lm.items() if m == "followers") or None,
            "er": max((v["value"] for (p, m), v in lm.items() if m == "engagement_rate"), default=None),
            "sent": sentiment_summary(conn, e["id"], start, end),
            "mentions": conn.execute(
                """SELECT COUNT(*) FROM content_items WHERE entity_id=? AND kind IN ('mention','article')
                   AND COALESCE(published_at,collected_at) BETWEEN ? AND ?""", (e["id"], start, end)).fetchone()[0],
            "alerts": conn.execute("SELECT COUNT(*) FROM alerts WHERE entity_id=? AND is_read=0",
                                   (e["id"],)).fetchone()[0],
        })
    return rows
