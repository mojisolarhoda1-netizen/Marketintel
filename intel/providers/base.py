"""The contract every data source follows.

To add a new source later (TikTok Login Kit, X, a licensed data vendor), create one
module with a Provider subclass and register it in providers/__init__.py.
Nothing else in the app needs to change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from pydantic import BaseModel, Field, field_validator

from ..config import USER_AGENT


# ---------- what providers return (validated before anything is saved) ----------

class MetricObs(BaseModel):
    metric: str = Field(min_length=1, max_length=64)
    value: float
    observed_at: str
    platform: str | None = None
    account_id: int | None = None

    @field_validator("value")
    @classmethod
    def finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")) or v < 0:
            raise ValueError("metric value must be a finite, non-negative number")
        return v


class ContentObs(BaseModel):
    kind: str = Field(pattern="^(mention|post|video|article)$")
    title: str | None = None
    body: str | None = None
    url: str | None = None
    external_id: str | None = None
    author: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    account_id: int | None = None


class PageObs(BaseModel):
    url: str
    title: str | None = None
    meta_description: str | None = None
    headings: list[str] = []
    prices: list[str] = []
    text_hash: str


@dataclass
class CollectResult:
    metrics: list[MetricObs] = field(default_factory=list)
    content: list[ContentObs] = field(default_factory=list)
    pages: list[PageObs] = field(default_factory=list)
    account_updates: list[dict] = field(default_factory=list)  # e.g. resolved external ids
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.metrics) + len(self.content) + len(self.pages)


# ---------- errors the pipeline understands ----------

class ProviderUnavailable(Exception):
    """Source not set up, postponed, or not applicable. Shown to the user, never retried."""


class RateLimited(Exception):
    def __init__(self, msg: str, retry_after: float = 5.0):
        super().__init__(msg)
        self.retry_after = retry_after


class TransientError(Exception):
    """Network hiccup or 5xx: worth retrying."""


# ---------- base class ----------

class Provider:
    id: str = ""
    name: str = ""
    cost_tier: str = "free"          # free | free_setup | paid | unavailable
    applies_to: tuple[str, ...] = ("brand", "influencer")

    def __init__(self, http_get: Callable[..., Any] | None = None):
        # http_get is injectable so tests run on recorded fixtures with no network.
        self._get = http_get or self._default_get
        self._last_call: dict[str, float] = {}

    # -- override these --
    def is_configured(self) -> tuple[bool, str]:
        return True, "ready"

    def supports(self, entity: dict, accounts: list[dict]) -> bool:
        return entity["kind"] in self.applies_to

    def collect(self, entity: dict, accounts: list[dict], ctx: "Context") -> CollectResult:  # noqa: F821
        raise NotImplementedError

    # -- helpers --
    def throttle(self, key: str, min_interval: float) -> None:
        last = self._last_call.get(key)
        if last is not None:
            wait = min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_call[key] = time.monotonic()

    @staticmethod
    def _default_get(url: str, params: dict | None = None, timeout: int = 20, headers: dict | None = None):
        h = {"User-Agent": USER_AGENT}
        if headers:
            h.update(headers)
        return requests.get(url, params=params, timeout=timeout, headers=h)

    def fetch(self, url: str, params: dict | None = None, headers: dict | None = None):
        try:
            resp = self._get(url, params=params, timeout=20, headers=headers)
        except requests.RequestException as e:  # network trouble
            raise TransientError(f"network error: {e.__class__.__name__}") from e
        status = getattr(resp, "status_code", 200)
        if status == 429:
            ra = resp.headers.get("Retry-After", "10") if hasattr(resp, "headers") else "10"
            raise RateLimited(f"{self.name} rate limit reached", float(ra) if str(ra).isdigit() else 10.0)
        if 500 <= status < 600:
            raise TransientError(f"{self.name} server error {status}")
        return resp


@dataclass
class Context:
    """What a provider may need from the app while collecting."""
    conn: Any
    now: str
    spend_units: Callable[[str, int], bool]   # quota guard: returns False if budget exhausted
