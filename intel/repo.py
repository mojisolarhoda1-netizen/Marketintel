"""Data access helpers: entities, accounts, competitors, manual records, CSV import."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from .db import default_org_id, utcnow

PLATFORMS = ("instagram", "youtube", "tiktok", "x", "facebook", "linkedin", "other")
MANUAL_METRICS = ("followers", "posts", "avg_likes", "avg_comments", "avg_views", "engagement_rate", "total_views")


class ValidationError(ValueError):
    pass


def _clean(s, maxlen=200) -> str:
    s = (s or "").strip()
    if len(s) > maxlen:
        raise ValidationError(f"'{s[:30]}…' is longer than {maxlen} characters")
    return s


def parse_keywords(raw: str | list | None) -> list[str]:
    if isinstance(raw, list):
        items = raw
    else:
        items = re.split(r"[,\n]", raw or "")
    out = []
    for k in items:
        k = str(k).strip()
        if k and k.lower() not in [o.lower() for o in out]:
            out.append(_clean(k, 80))
    return out[:20]


def create_entity(conn, kind: str, name: str, country: str = "", category: str = "", website: str = "",
                  keywords=None, notes: str = "", refresh_hours: int = 24, is_sample: bool = False) -> int:
    if kind not in ("brand", "influencer"):
        raise ValidationError("kind must be brand or influencer")
    name = _clean(name, 120)
    if not name:
        raise ValidationError("Name is required")
    website = _clean(website, 300)
    if website and not re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/.*)?$", website, re.I):
        raise ValidationError("Website doesn't look like a web address")
    try:
        refresh_hours = int(refresh_hours)
    except (TypeError, ValueError):
        raise ValidationError("Refresh interval must be a number of hours")
    if not 1 <= refresh_hours <= 720:
        raise ValidationError("Refresh interval must be between 1 and 720 hours")
    exists = conn.execute("SELECT id FROM entities WHERE org_id=? AND kind=? AND lower(name)=lower(?)",
                          (default_org_id(conn), kind, name)).fetchone()
    if exists:
        raise ValidationError(f"A {kind} called '{name}' is already tracked")
    cur = conn.execute(
        """INSERT INTO entities(org_id,kind,name,country,category,website,keywords,notes,refresh_hours,is_sample)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (default_org_id(conn), kind, name, _clean(country, 60).upper()[:2] or None, _clean(category, 80) or None,
         website or None, json.dumps(parse_keywords(keywords)), _clean(notes, 2000) or None, refresh_hours,
         1 if is_sample else 0))
    return cur.lastrowid


def update_entity(conn, entity_id: int, **fields) -> None:
    allowed = {"country", "category", "website", "keywords", "notes", "refresh_hours", "active"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "keywords":
            v = json.dumps(parse_keywords(v))
        elif k == "country":
            v = _clean(v, 60).upper()[:2] or None
        sets.append(f"{k}=?"); args.append(v)
    if sets:
        conn.execute(f"UPDATE entities SET {', '.join(sets)} WHERE id=?", [*args, entity_id])


def delete_entity(conn, entity_id: int) -> None:
    conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))


def get_entity(conn, entity_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    return dict(r) if r else None


def list_entities(conn, kind: str | None = None, country: str = "", category: str = "", platform: str = "",
                  q: str = "", include_sample: bool = True) -> list[dict]:
    sql = "SELECT DISTINCT e.* FROM entities e LEFT JOIN social_accounts a ON a.entity_id=e.id WHERE 1=1"
    args: list = []
    if not include_sample:
        sql += " AND e.is_sample=0"
    if kind:
        sql += " AND e.kind=?"; args.append(kind)
    if country:
        sql += " AND e.country=?"; args.append(country.upper()[:2])
    if category:
        sql += " AND lower(e.category)=lower(?)"; args.append(category)
    if platform:
        sql += " AND a.platform=?"; args.append(platform)
    if q:
        sql += " AND (e.name LIKE ? OR e.category LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY e.is_sample, e.name COLLATE NOCASE"
    return [dict(r) for r in conn.execute(sql, args)]


def accounts_for(conn, entity_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM social_accounts WHERE entity_id=? ORDER BY platform", (entity_id,))]


def normalise_handle(platform: str, handle: str) -> str:
    h = _clean(handle, 200)
    if "://" in h:
        h = h.rstrip("/").split("/")[-1]
    h = h.split("?")[0]
    if platform in ("instagram", "tiktok", "x"):
        h = h.lstrip("@")
    if platform == "youtube" and not h.startswith(("@", "UC")):
        h = "@" + h
    if not re.match(r"^[\w.@\-]{1,100}$", h):
        raise ValidationError(f"'{handle}' doesn't look like a valid {platform} handle")
    return h


def add_account(conn, entity_id: int, platform: str, handle: str) -> int:
    if platform not in PLATFORMS:
        raise ValidationError("Unknown platform")
    h = normalise_handle(platform, handle)
    cur = conn.execute("INSERT OR IGNORE INTO social_accounts(entity_id,platform,handle) VALUES (?,?,?)",
                       (entity_id, platform, h))
    if cur.lastrowid and cur.rowcount:
        return cur.lastrowid
    return conn.execute("SELECT id FROM social_accounts WHERE entity_id=? AND platform=? AND handle=?",
                        (entity_id, platform, h)).fetchone()[0]


def remove_account(conn, account_id: int) -> None:
    conn.execute("DELETE FROM social_accounts WHERE id=?", (account_id,))


def link_competitor(conn, brand_id: int, competitor_id: int) -> None:
    if brand_id == competitor_id:
        raise ValidationError("A brand can't be its own competitor")
    conn.execute("INSERT OR IGNORE INTO competitor_links(brand_id,competitor_id) VALUES (?,?)",
                 (brand_id, competitor_id))


def unlink_competitor(conn, brand_id: int, competitor_id: int) -> None:
    conn.execute("DELETE FROM competitor_links WHERE brand_id=? AND competitor_id=?", (brand_id, competitor_id))


def competitors_of(conn, brand_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT e.* FROM competitor_links l JOIN entities e ON e.id=l.competitor_id
           WHERE l.brand_id=? ORDER BY e.name""", (brand_id,))]


def _norm_date(s: str | None) -> str:
    if not s:
        return utcnow()
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    raise ValidationError(f"Date '{s}' not understood. Use YYYY-MM-DD.")


def record_manual_metric(conn, entity_id: int, platform: str, metric: str, value, observed_on: str | None,
                         is_sample: bool = False, source_id: str = "manual") -> None:
    ent = conn.execute("SELECT is_sample FROM entities WHERE id=?", (entity_id,)).fetchone()
    if ent is None:
        raise ValidationError("Unknown brand or influencer")
    if ent[0] and not is_sample:
        raise ValidationError("This is a SAMPLE (fictional) entry. Real numbers can't be added to it — "
                              "add the real brand or creator instead.")
    if platform not in PLATFORMS:
        raise ValidationError("Unknown platform")
    if metric not in MANUAL_METRICS:
        raise ValidationError(f"Metric must be one of: {', '.join(MANUAL_METRICS)}")
    try:
        v = float(str(value).replace(",", "").strip())
    except ValueError:
        raise ValidationError(f"'{value}' is not a number")
    if v < 0:
        raise ValidationError("Values can't be negative")
    observed = _norm_date(observed_on)
    if observed > utcnow():
        raise ValidationError("Date can't be in the future")
    acc = conn.execute("SELECT id FROM social_accounts WHERE entity_id=? AND platform=? ORDER BY id LIMIT 1",
                       (entity_id, platform)).fetchone()
    conn.execute(
        """INSERT INTO metric_snapshots(entity_id,account_id,source_id,platform,metric,value,observed_at,
           collected_at,is_sample) VALUES (?,?,?,?,?,?,?,?,?)""",
        (entity_id, acc[0] if acc else None, "sample" if is_sample else source_id, platform, metric, v,
         observed, utcnow(), 1 if is_sample else 0))


CSV_HEADERS = ["name", "kind", "platform", "handle", "metric", "value", "date"]


def import_metrics_csv(conn, text: str) -> dict:
    """CSV columns: name,kind,platform,handle,metric,value,date. Creates entities/accounts if new."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [h for h in CSV_HEADERS if h not in (reader.fieldnames or [])]
    if missing:
        raise ValidationError(f"CSV is missing columns: {', '.join(missing)}")
    done, errors = 0, []
    for i, row in enumerate(reader, start=2):
        if i > 5002:
            errors.append("Stopped at 5,000 rows."); break
        try:
            kind = (row["kind"] or "influencer").strip().lower()
            name = (row["name"] or "").strip()
            r = conn.execute("SELECT id, is_sample FROM entities WHERE kind=? AND lower(name)=lower(?)",
                             (kind, name)).fetchone()
            if r and r[1]:
                raise ValidationError(f"'{name}' is a SAMPLE entry; real data is not mixed into sample data")
            eid = r[0] if r else create_entity(conn, kind, name)
            platform = row["platform"].strip().lower()
            if row.get("handle"):
                add_account(conn, eid, platform, row["handle"])
            record_manual_metric(conn, eid, platform, row["metric"].strip(), row["value"], row["date"],
                                 source_id="csv_import")
            done += 1
        except (ValidationError, KeyError, AttributeError) as e:
            errors.append(f"Row {i}: {e}")
    return {"imported": done, "errors": errors[:50]}
