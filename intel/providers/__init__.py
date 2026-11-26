"""Provider registry. Add a new data source here and nowhere else."""
from __future__ import annotations

from .base import Provider
from .instagram import InstagramProvider
from .postponed import TikTokProvider, XProvider
from .rss_news import GoogleNewsProvider, RssNewsProvider
from .website import WebsiteProvider
from .youtube import YouTubeProvider

PROVIDER_CLASSES: list[type[Provider]] = [
    RssNewsProvider,
    GoogleNewsProvider,
    WebsiteProvider,
    YouTubeProvider,
    InstagramProvider,
    TikTokProvider,
    XProvider,
]


def build_providers(http_get=None) -> dict[str, Provider]:
    return {cls.id: cls(http_get=http_get) for cls in PROVIDER_CLASSES}
