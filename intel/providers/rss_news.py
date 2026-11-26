"""News mentions from publishers' public RSS/Atom feeds. Free, no key.

We store the headline, link, publisher, date and the short summary the publisher
puts in the feed — never the full article.
"""
from __future__ import annotations

import html
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from datetime import datetime
from urllib.parse import quote_plus

from lxml import etree

from .base import CollectResult, ContentObs, Provider, ProviderUnavailable, TransientError

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s: str | None, limit: int = 600) -> str:
    if not s:
        return ""
    s = html.unescape(_TAG_RE.sub(" ", s))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def to_iso(date_str: str | None) -> str | None:
    if not date_str:
        return None
    date_str = date_str.strip()
    try:
        dt = parsedate_to_datetime(date_str)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_feed(xml_bytes: bytes) -> list[dict]:
    """Parse RSS 2.0 or Atom into simple dicts."""
    root = etree.fromstring(xml_bytes, parser=_PARSER)
    if root is None:
        raise ValueError("not an XML feed")
    items = []
    # RSS 2.0
    for it in root.iter("item"):
        items.append({
            "title": clean_text(it.findtext("title"), 300),
            "url": (it.findtext("link") or "").strip(),
            "guid": (it.findtext("guid") or "").strip(),
            "summary": clean_text(it.findtext("description")),
            "published": to_iso(it.findtext("pubDate")),
            "author": clean_text(it.findtext("{http://purl.org/dc/elements/1.1/}creator"), 120),
        })
    if items:
        return items
    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for e in root.iter(f"{ns}entry"):
        link = e.find(f"{ns}link")
        items.append({
            "title": clean_text(e.findtext(f"{ns}title"), 300),
            "url": (link.get("href") if link is not None else "") or "",
            "guid": (e.findtext(f"{ns}id") or "").strip(),
            "summary": clean_text(e.findtext(f"{ns}summary") or e.findtext(f"{ns}content")),
            "published": to_iso(e.findtext(f"{ns}published") or e.findtext(f"{ns}updated")),
            "author": clean_text(e.findtext(f"{ns}author/{ns}name"), 120),
        })
    return items


def search_terms(entity: dict) -> list[str]:
    import json
    terms = [entity["name"]]
    try:
        terms += [k for k in json.loads(entity.get("keywords") or "[]") if isinstance(k, str)]
    except ValueError:
        pass
    return [t.strip() for t in terms if t and len(t.strip()) >= 2]


def matches(text: str, terms: list[str]) -> bool:
    for t in terms:
        if re.search(r"(?<![\w])" + re.escape(t) + r"(?![\w])", text, flags=re.IGNORECASE):
            return True
    return False


class RssNewsProvider(Provider):
    id = "rss_news"
    name = "News RSS feeds"
    cost_tier = "free"
    applies_to = ("brand", "influencer")

    def __init__(self, http_get=None):
        super().__init__(http_get)
        self._cache: dict[str, list[dict]] = {}   # one fetch per feed per pipeline run

    def feeds(self, conn) -> list[dict]:
        return [dict(r) for r in conn.execute("SELECT * FROM news_feeds WHERE enabled=1 ORDER BY id")]

    def load(self, url: str) -> list[dict]:
        if url not in self._cache:
            self.throttle(url.split("/")[2], 1.0)
            resp = self.fetch(url)
            if resp.status_code != 200:
                raise TransientError(f"feed returned HTTP {resp.status_code}")
            self._cache[url] = parse_feed(resp.content)
        return self._cache[url]

    def collect(self, entity, accounts, ctx) -> CollectResult:
        feeds = self.feeds(ctx.conn)
        if not feeds:
            raise ProviderUnavailable("No news feeds enabled. Add one on the Sources page.")
        terms = search_terms(entity)
        out = CollectResult()
        failures = 0
        # Fetch every feed at the same time (each is a different website), then match.
        from concurrent.futures import ThreadPoolExecutor
        def warm(url):
            try:
                self.load(url)
            except Exception:
                pass  # the error is recorded below, on the second (cached-miss) attempt
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(warm, [f["url"] for f in feeds if f["url"] not in self._cache]))
        for f in feeds:
            try:
                items = self.load(f["url"])
                ctx.conn.execute("UPDATE news_feeds SET last_error=NULL, verified=1 WHERE id=?", (f["id"],))
            except Exception as e:  # one broken feed must not stop the rest
                failures += 1
                ctx.conn.execute("UPDATE news_feeds SET last_error=? WHERE id=?", (str(e)[:200], f["id"]))
                out.warnings.append(f"{f['name']}: {e}")
                continue
            for it in items:
                if not matches(f"{it['title']} {it['summary']}", terms):
                    continue
                out.content.append(ContentObs(
                    kind="mention", title=it["title"], body=it["summary"], url=it["url"] or None,
                    external_id=it["guid"] or it["url"] or None, author=it["author"] or None,
                    publisher=f["name"], published_at=it["published"],
                ))
        if failures == len(feeds):
            raise TransientError("every enabled feed failed: " + "; ".join(out.warnings)[:300])
        return out


class GoogleNewsProvider(RssNewsProvider):
    """Search-based news. Off by default until Google's terms are reviewed for commercial use."""
    id = "google_news"
    name = "Google News search RSS"

    def collect(self, entity, accounts, ctx) -> CollectResult:
        country = (entity.get("country") or "NG").upper()[:2]
        q = quote_plus(f'"{entity["name"]}"')
        url = f"https://news.google.com/rss/search?q={q}&hl=en-{country}&gl={country}&ceid={country}:en"
        out = CollectResult()
        for it in self.load(url):
            publisher = None
            title = it["title"]
            if " - " in title:  # Google appends " - Publisher"
                title, publisher = title.rsplit(" - ", 1)
            out.content.append(ContentObs(
                kind="mention", title=title, body=it["summary"], url=it["url"] or None,
                external_id=it["guid"] or it["url"] or None, publisher=publisher or "Google News",
                published_at=it["published"],
            ))
        return out
