"""Instagram Business Discovery (Instagram API with Facebook Login). Free.

Checked against Meta's docs, Nov 2026:
- The request is made FROM your own Instagram professional (Business or Creator) account,
  using a Facebook User access token.
- Permissions: instagram_basic, instagram_manage_insights, pages_read_engagement
  (plus ads_read or ads_management if your Page role comes through Business Manager).
- TARGET accounts must be Business or Creator accounts, looked up by username.
  Personal accounts and age-gated accounts return nothing.
- Fields used: followers_count, media_count, and per recent post like_count, comments_count
  (like_count is missing when the owner hides likes). Caption/timestamp/permalink are requested
  too; if Meta rejects them we fall back to the documented minimum.
- Not available: audience demographics, reach, impressions, saves, stories, follower lists.
"""
from __future__ import annotations

from ..config import env
from .base import (CollectResult, ContentObs, MetricObs, Provider, ProviderUnavailable,
                   RateLimited, TransientError)
from .youtube import _recent_metrics

FIELDS = "followers_count,media_count,media.limit(12){id,caption,like_count,comments_count,timestamp,permalink,media_type}"
MIN_FIELDS = "followers_count,media_count,media.limit(12){id,like_count,comments_count}"


class InstagramProvider(Provider):
    id = "instagram"
    name = "Instagram Business Discovery"
    cost_tier = "free_setup"

    def creds(self):
        return env("IG_USER_ID"), env("IG_ACCESS_TOKEN"), env("IG_GRAPH_VERSION", "v26.0")

    def is_configured(self):
        uid, tok, _ = self.creds()
        if uid and tok:
            return True, "token found"
        return False, "Add IG_USER_ID and IG_ACCESS_TOKEN to .env (free Meta developer app)."

    def supports(self, entity, accounts) -> bool:
        return any(a["platform"] == "instagram" for a in accounts)

    def collect(self, entity, accounts, ctx) -> CollectResult:
        ok, why = self.is_configured()
        if not ok:
            raise ProviderUnavailable(why)
        uid, tok, ver = self.creds()
        out = CollectResult()
        for acc in [a for a in accounts if a["platform"] == "instagram"]:
            username = acc["handle"].lstrip("@").strip().rstrip("/").split("/")[-1]
            data, resp = {}, None
            for fields in (FIELDS, MIN_FIELDS):
                self.throttle("instagram", 2.0)
                resp = self.fetch(
                    f"https://graph.facebook.com/{ver}/{uid}",
                    params={"fields": f"business_discovery.username({username}){{{fields}}}", "access_token": tok},
                )
                data = resp.json() if resp.content else {}
                err = data.get("error", {})
                # code 100 with a field complaint -> retry once with the documented minimum fields
                if resp.status_code != 200 and err.get("code") == 100 and "field" in err.get("message", "").lower() \
                        and fields == FIELDS:
                    continue
                break
            if resp.status_code != 200:
                err = data.get("error", {})
                code, msg = err.get("code"), err.get("message", "")
                if code in (4, 17, 32, 613):
                    raise RateLimited("Instagram rate limit reached", 3600)
                if code == 190:
                    raise ProviderUnavailable("Instagram token expired or invalid. Create a new one.")
                if code == 110 or "Invalid user id" in msg or code == 100:
                    out.warnings.append(f"@{username}: not found or not a public professional account.")
                    continue
                raise TransientError(f"Instagram error {code}: {msg[:120]}")
            bd = data.get("business_discovery") or {}
            followers = bd.get("followers_count")
            if followers is not None:
                out.metrics.append(MetricObs(metric="followers", value=followers, observed_at=ctx.now,
                                             platform="instagram", account_id=acc["id"]))
            if bd.get("media_count") is not None:
                out.metrics.append(MetricObs(metric="posts", value=bd["media_count"], observed_at=ctx.now,
                                             platform="instagram", account_id=acc["id"]))
            likes, comments = [], []
            for m in (bd.get("media") or {}).get("data", []):
                lk, cm = m.get("like_count"), m.get("comments_count")
                out.content.append(ContentObs(
                    kind="post", title=(m.get("caption") or "")[:120] or None, body=(m.get("caption") or "")[:600],
                    url=m.get("permalink"), external_id=m.get("id"), author=username, publisher="Instagram",
                    published_at=(m.get("timestamp") or "").replace("+0000", "Z") or None,
                    likes=lk, comments=cm, account_id=acc["id"],
                ))
                if lk is not None: likes.append(lk)   # hidden like counts are simply absent
                if cm is not None: comments.append(cm)
            _recent_metrics(out, likes, comments, [], followers, ctx.now, "instagram", acc["id"])
        return out
