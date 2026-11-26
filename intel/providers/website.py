"""Website monitor: watches public pages for changes in title, headings and prices.

Polite by design: checks robots.txt, identifies itself, waits between requests,
and stores only a fingerprint plus a few extracted facts — not copies of pages.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib import robotparser
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import USER_AGENT
from .base import CollectResult, PageObs, Provider, ProviderUnavailable, TransientError

PRICE_RE = re.compile(
    r"(?:₦|NGN\s?|N(?=\d)|\$|USD\s?|£|GBP\s?|KES\s?|GH₵|R(?=\s?\d))\s?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?",
)


def extract_page(url: str, html_text: str) -> PageObs:
    soup = BeautifulSoup(html_text, "lxml")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else None
    meta = soup.find("meta", attrs={"name": "description"})
    desc = meta.get("content", "").strip() if meta else None
    headings = [h.get_text(" ", strip=True)[:160] for h in soup.find_all(["h1", "h2"])][:20]
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    prices = sorted(set(m.group(0).strip() for m in PRICE_RE.finditer(text)))[:40]
    fingerprint = json.dumps({"t": title, "d": desc, "h": headings, "p": prices}, sort_keys=True)
    return PageObs(
        url=url, title=title, meta_description=desc or None, headings=headings, prices=prices,
        text_hash=hashlib.sha256(fingerprint.encode()).hexdigest(),
    )


class WebsiteProvider(Provider):
    id = "website"
    name = "Website monitor"
    cost_tier = "free"
    applies_to = ("brand",)

    def __init__(self, http_get=None):
        super().__init__(http_get)
        self._robots: dict[str, robotparser.RobotFileParser] = {}

    def supports(self, entity, accounts) -> bool:
        return entity["kind"] == "brand"

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.fetch(urljoin(base, "/robots.txt"))
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:  # no robots.txt = allowed
                    rp.parse([])
            except TransientError:
                rp.parse(["User-agent: *", "Disallow: /"])  # can't check -> don't crawl
            self._robots[base] = rp
        return self._robots[base].can_fetch(USER_AGENT, url)

    def collect(self, entity, accounts, ctx) -> CollectResult:
        site = (entity.get("website") or "").strip()
        if not site:
            raise ProviderUnavailable("No website saved for this brand.")
        if not site.startswith(("http://", "https://")):
            site = "https://" + site
        if urlparse(site).scheme not in ("http", "https"):
            raise ProviderUnavailable("Website must be an http(s) address.")
        out = CollectResult()
        if not self.allowed(site):
            raise ProviderUnavailable("The site's robots.txt does not allow automated visits to this page.")
        self.throttle(urlparse(site).netloc, 3.0)
        resp = self.fetch(site)
        if resp.status_code != 200:
            raise TransientError(f"website returned HTTP {resp.status_code}")
        ctype = resp.headers.get("Content-Type", "text/html") if hasattr(resp, "headers") else "text/html"
        if "html" not in ctype:
            raise ProviderUnavailable(f"Page is not HTML ({ctype}).")
        out.pages.append(extract_page(site, resp.text[:2_000_000]))
        return out
