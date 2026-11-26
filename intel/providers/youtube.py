"""YouTube Data API v3. Free with a Google Cloud API key.

Quota (Google's quota page, checked Nov 2026): 10,000 units/day for most methods, where
channels.list, playlistItems.list and videos.list cost 1 unit each. search.list has its OWN
bucket of 100 calls/day (1 per call). Two quota guards stop us before either runs out.

Storage rule (YouTube API Developer Policies III.E.4): data obtained without the channel
owner's authorisation must be deleted or refreshed within 30 calendar days, and subscriber
counts must not be kept longer than 30 days. See intel/retention.py.
"""
from __future__ import annotations

from ..config import env, env_int
from .base import (CollectResult, ContentObs, MetricObs, Provider, ProviderUnavailable,
                   RateLimited, TransientError)

API = "https://www.googleapis.com/youtube/v3"


def daily_budget() -> int:
    # stay under Google's 10,000 default with a safety margin
    return env_int("YOUTUBE_DAILY_UNIT_BUDGET", 8000)


def daily_search_budget() -> int:
    # search.list has its own 100-calls/day bucket
    return env_int("YOUTUBE_DAILY_SEARCH_BUDGET", 90)


class YouTubeProvider(Provider):
    id = "youtube"
    name = "YouTube Data API"
    cost_tier = "free_setup"

    def key(self) -> str:
        return env("YOUTUBE_API_KEY")

    def is_configured(self):
        return (True, "API key found") if self.key() else (False, "Add YOUTUBE_API_KEY to your .env file (free).")

    def supports(self, entity, accounts) -> bool:
        return any(a["platform"] == "youtube" for a in accounts)

    def _call(self, endpoint: str, params: dict, units: int, spend, bucket: str = "youtube") -> dict:
        if not spend(bucket, units):
            what = "search" if bucket == "youtube_search" else "quota"
            raise RateLimited(f"YouTube daily {what} budget reached; will continue tomorrow.", 3600)
        resp = self.fetch(f"{API}/{endpoint}", params={**params, "key": self.key()})
        data = resp.json() if resp.content else {}
        if resp.status_code == 403:
            reason = (data.get("error", {}).get("errors") or [{}])[0].get("reason", "")
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise RateLimited("YouTube quota exceeded", 3600)
            raise ProviderUnavailable(f"YouTube refused the request ({reason or 'forbidden'}). Check the API key.")
        if resp.status_code == 400:
            raise ProviderUnavailable(f"YouTube rejected the request: {data.get('error', {}).get('message', '')}")
        if resp.status_code != 200:
            raise TransientError(f"YouTube HTTP {resp.status_code}")
        return data

    def resolve_channel(self, handle_or_id: str, spend) -> dict | None:
        h = handle_or_id.strip()
        if "youtube.com/" in h:
            h = h.rstrip("/").split("/")[-1]
        params = {"part": "snippet,statistics,contentDetails"}
        if h.startswith("UC") and len(h) == 24:
            params["id"] = h
        else:
            params["forHandle"] = h if h.startswith("@") else "@" + h
        items = self._call("channels", params, 1, spend).get("items") or []
        return items[0] if items else None

    def collect(self, entity, accounts, ctx) -> CollectResult:
        ok, why = self.is_configured()
        if not ok:
            raise ProviderUnavailable(why)
        out = CollectResult()
        for acc in [a for a in accounts if a["platform"] == "youtube"]:
            ch = self.resolve_channel(acc["external_id"] or acc["handle"], ctx.spend_units)
            if not ch:
                out.warnings.append(f"YouTube channel '{acc['handle']}' not found.")
                continue
            if not acc["external_id"]:
                out.account_updates.append({"id": acc["id"], "external_id": ch["id"],
                                            "url": f"https://www.youtube.com/channel/{ch['id']}"})
            st = ch.get("statistics", {})
            subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))
            if subs is not None:
                out.metrics.append(MetricObs(metric="followers", value=subs, observed_at=ctx.now,
                                             platform="youtube", account_id=acc["id"]))
            else:
                out.warnings.append("Channel hides its subscriber count.")
            for name, key in (("total_views", "viewCount"), ("posts", "videoCount")):
                if key in st:
                    out.metrics.append(MetricObs(metric=name, value=int(st[key]), observed_at=ctx.now,
                                                 platform="youtube", account_id=acc["id"]))
            uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if not uploads:
                continue
            pl = self._call("playlistItems", {"part": "contentDetails", "playlistId": uploads,
                                              "maxResults": 12}, 1, ctx.spend_units)
            ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
            if not ids:
                continue
            vids = self._call("videos", {"part": "snippet,statistics", "id": ",".join(ids)}, 1,
                              ctx.spend_units).get("items", [])
            likes, comments, views = [], [], []
            for v in vids:
                s, sn = v.get("statistics", {}), v.get("snippet", {})
                lk = int(s["likeCount"]) if "likeCount" in s else None
                cm = int(s["commentCount"]) if "commentCount" in s else None
                vw = int(s["viewCount"]) if "viewCount" in s else None
                out.content.append(ContentObs(
                    kind="video", title=sn.get("title"), body=(sn.get("description") or "")[:500],
                    url=f"https://www.youtube.com/watch?v={v['id']}", external_id=v["id"],
                    author=sn.get("channelTitle"), publisher="YouTube", published_at=sn.get("publishedAt"),
                    likes=lk, comments=cm, views=vw, account_id=acc["id"],
                ))
                if lk is not None: likes.append(lk)
                if cm is not None: comments.append(cm)
                if vw is not None: views.append(vw)
            _recent_metrics(out, likes, comments, views, subs, ctx.now, "youtube", acc["id"])
        return out

    def search_channels(self, query: str, region: str, spend, max_results: int = 10) -> list[dict]:
        """Influencer discovery: 1 search call (own 100/day bucket) + 1 unit for the stats lookup."""
        ok, why = self.is_configured()
        if not ok:
            raise ProviderUnavailable(why)
        found = self._call("search", {"part": "snippet", "q": query, "type": "channel",
                                      "regionCode": region, "maxResults": max_results}, 1, spend, "youtube_search")
        ids = [i["snippet"]["channelId"] for i in found.get("items", [])]
        if not ids:
            return []
        chans = self._call("channels", {"part": "snippet,statistics", "id": ",".join(ids)}, 1, spend)
        results = []
        for c in chans.get("items", []):
            st, sn = c.get("statistics", {}), c.get("snippet", {})
            results.append({
                "channel_id": c["id"], "title": sn.get("title"), "handle": sn.get("customUrl"),
                "country": sn.get("country"), "description": (sn.get("description") or "")[:200],
                "subscribers": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
                "videos": int(st.get("videoCount", 0)), "views": int(st.get("viewCount", 0)),
            })
        return sorted(results, key=lambda r: r["subscribers"] or 0, reverse=True)


def _recent_metrics(out, likes, comments, views, followers, now, platform, account_id):
    def avg(xs):
        return sum(xs) / len(xs) if xs else None
    al, ac, av = avg(likes), avg(comments), avg(views)
    for name, val in (("avg_likes", al), ("avg_comments", ac), ("avg_views", av)):
        if val is not None:
            out.metrics.append(MetricObs(metric=name, value=round(val, 2), observed_at=now,
                                         platform=platform, account_id=account_id))
    if followers and al is not None:
        er = (al + (ac or 0)) / followers * 100
        out.metrics.append(MetricObs(metric="engagement_rate", value=round(er, 4), observed_at=now,
                                     platform=platform, account_id=account_id))
