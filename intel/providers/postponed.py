"""Sources we have deliberately postponed. They exist so the app can say clearly
why data is missing, and so connecting them later is one file of work."""
from __future__ import annotations

from .base import Provider, ProviderUnavailable


class TikTokProvider(Provider):
    id = "tiktok"
    name = "TikTok"
    cost_tier = "unavailable"

    def is_configured(self):
        return False, ("TikTok's Research API is for non-commercial researchers only. "
                       "Plan: TikTok Login Kit for creators who connect their own accounts. "
                       "Until then, record TikTok numbers with Manual entry.")

    def supports(self, entity, accounts):
        return any(a["platform"] == "tiktok" for a in accounts)

    def collect(self, entity, accounts, ctx):
        raise ProviderUnavailable(self.is_configured()[1])


class XProvider(Provider):
    id = "x"
    name = "X (Twitter) API"
    cost_tier = "paid"

    def is_configured(self):
        return False, "X API is pay-per-use. Postponed until a paying customer needs X data."

    def supports(self, entity, accounts):
        return any(a["platform"] == "x" for a in accounts)

    def collect(self, entity, accounts, ctx):
        raise ProviderUnavailable(self.is_configured()[1])
