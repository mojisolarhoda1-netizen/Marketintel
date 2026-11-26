"""Instant research: type a brand (and competitors) and get a live picture in seconds.

Free sources, fetched live, nothing paid:
  * Google News search RSS  - what the press and blogs are saying (headline + short summary only)
  * Google search suggestions - what people are typing into Google about the brand
  * Wikipedia page views     - a public signal of how much interest a name gets (last 30 days vs the 30 before)

Everything below the fetches is rule-based (word counts, sentiment word lists, comparisons),
so the advice is explainable: every recommendation says which numbers it came from.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, quote_plus

import requests

from .analysis.sentiment import score
from .analysis.trends import top_words
from .config import USER_AGENT
from .providers.rss_news import parse_feed

TIMEOUT = 9
MAX_TERMS = 5


def _get(http_get, url, params=None):
    if http_get:
        return http_get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    return requests.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})


def _now():
    return datetime.now(timezone.utc)


def _age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (_now() - dt).total_seconds() / 86400


# ---------- sources ----------

def news(term: str, country: str = "NG", http_get=None) -> list[dict]:
    q = f'"{term}"' if " " in term.strip() else term
    cc = (country or "NG").upper()[:2]
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-{cc}&gl={cc}&ceid={cc}:en"
    r = _get(http_get, url)
    if getattr(r, "status_code", 200) != 200:
        raise RuntimeError(f"news search returned HTTP {r.status_code}")
    out = []
    for it in parse_feed(r.content)[:100]:
        title, publisher = it["title"], "Google News"
        if " - " in title:
            title, publisher = title.rsplit(" - ", 1)
        s, label, _ = score(f"{title}. {it['summary']}")
        out.append({"title": title, "publisher": publisher, "url": it["url"], "published": it["published"],
                    "age": _age_days(it["published"]), "sentiment": label, "score": s})
    out.sort(key=lambda a: a["age"] if a["age"] is not None else 9999)
    return out


def suggestions(term: str, country: str = "NG", http_get=None) -> list[str]:
    """What people type into Google about this term (public autocomplete)."""
    def one(q):
        try:
            r = _get(http_get, "https://suggestqueries.google.com/complete/search",
                     params={"client": "firefox", "q": q, "gl": country.lower(), "hl": "en"})
            if getattr(r, "status_code", 200) != 200:
                return []
            data = json.loads(r.content.decode("utf-8", "replace"))
            return data[1] if len(data) > 1 else []
        except Exception:  # noqa: BLE001 - suggestions are a bonus, never break the page
            return []
    seen, out = set(), []
    qs = (term, f"{term} ", f"is {term}", f"{term} vs", f"how to {term}")
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(one, qs))
    for res in results:
        for s in res:
            s = str(s).strip()
            if s and s.lower() != term.lower() and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out[:15]


def wiki_interest(term: str, http_get=None) -> dict | None:
    """Wikipedia page views for the closest article: last 30 days vs the 30 days before."""
    try:
        r = _get(http_get, "https://en.wikipedia.org/w/api.php",
                 params={"action": "opensearch", "search": term, "limit": 1, "namespace": 0, "format": "json"})
        data = json.loads(r.content.decode("utf-8", "replace"))
        if not data[1]:
            return None
        title = data[1][0]
        end = _now() - timedelta(days=1)
        start = end - timedelta(days=59)
        url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
               f"{quote(title.replace(' ', '_'), safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
        r = _get(http_get, url)
        if getattr(r, "status_code", 200) != 200:
            return None
        views = [i["views"] for i in json.loads(r.content)["items"]]
        if len(views) < 10:
            return None
        last, prev = sum(views[-30:]), sum(views[:-30])
        change = round((last - prev) / prev * 100) if prev else None
        return {"title": title, "last30": last, "prev30": prev, "change": change}
    except Exception:  # noqa: BLE001
        return None


def youtube(term: str, country: str = "NG", http_get=None) -> list[dict] | None:
    """Top recent YouTube videos for the term (needs the free YOUTUBE_API_KEY). None = not set up."""
    from .config import env
    key = env("YOUTUBE_API_KEY")
    if not key:
        return None
    since = (_now() - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _get(http_get, "https://www.googleapis.com/youtube/v3/search",
             params={"part": "snippet", "q": term, "type": "video", "maxResults": 12, "order": "relevance",
                     "publishedAfter": since, "regionCode": (country or "NG")[:2], "key": key})
    if getattr(r, "status_code", 200) != 200:
        return []
    items = json.loads(r.content).get("items", [])
    ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
    stats = {}
    if ids:
        r2 = _get(http_get, "https://www.googleapis.com/youtube/v3/videos",
                  params={"part": "statistics", "id": ",".join(ids), "key": key})
        if getattr(r2, "status_code", 200) == 200:
            stats = {v["id"]: v.get("statistics", {}) for v in json.loads(r2.content).get("items", [])}
    out = []
    for i in items:
        vid = i.get("id", {}).get("videoId")
        if not vid:
            continue
        sn, st = i["snippet"], stats.get(vid, {})
        out.append({"title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                    "published": sn.get("publishedAt", ""), "url": f"https://www.youtube.com/watch?v={vid}",
                    "views": int(st["viewCount"]) if st.get("viewCount", "").isdigit() else None,
                    "comments": int(st["commentCount"]) if st.get("commentCount", "").isdigit() else None})
    out.sort(key=lambda v: v["views"] or 0, reverse=True)
    return out


# ---------- analysis ----------

def _profile(term: str, country: str, http_get) -> dict:
    p = {"name": term, "error": None, "articles": [], "wiki": None, "youtube": None}
    with ThreadPoolExecutor(max_workers=3) as ex:   # each source is a different website: ask all at once
        f_news, f_wiki = ex.submit(news, term, country, http_get), ex.submit(wiki_interest, term, http_get)
        f_yt = ex.submit(youtube, term, country, http_get)
        try:
            p["articles"] = f_news.result()
        except Exception as e:  # noqa: BLE001
            p["error"] = f"News search failed: {e}"
        p["wiki"] = f_wiki.result()
        try:
            p["youtube"] = f_yt.result()
        except Exception:  # noqa: BLE001
            p["youtube"] = []
    arts = p["articles"]
    p["n"] = len(arts)
    p["n7"] = sum(1 for a in arts if a["age"] is not None and a["age"] <= 7)
    p["n30"] = sum(1 for a in arts if a["age"] is not None and a["age"] <= 30)
    cnt = Counter(a["sentiment"] for a in arts)
    p["pos"], p["neg"], p["neu"] = cnt.get("positive", 0), cnt.get("negative", 0), cnt.get("neutral", 0)
    p["pos_pct"] = round(p["pos"] / p["n"] * 100) if p["n"] else 0
    p["neg_pct"] = round(p["neg"] / p["n"] * 100) if p["n"] else 0
    own = set(re.findall(r"[a-z][a-z\-]{2,}", term.lower()))
    recent = [a["title"] for a in arts if a["age"] is None or a["age"] <= 30] or [a["title"] for a in arts]
    p["themes"] = [w for w, c in top_words(recent, 20) if w not in own and c >= 2][:10]
    p["publishers"] = [n for n, _ in Counter(a["publisher"] for a in arts).most_common(6)]
    p["negative"] = [a for a in arts if a["sentiment"] == "negative"][:4]
    p["latest"] = arts[:10]
    return p


def _advice(main: dict, comps: list[dict], sugg: list[str]) -> list[dict]:
    """Plain-language recommendations, each tied to the numbers that triggered it."""
    tips = []
    allp = [main] + comps
    leader = max(allp, key=lambda p: p["n30"]) if allp else main
    if main["n30"] == 0:
        tips.append({"title": "Get into the news",
                     "why": f"No news or blog coverage of {main['name']} found in the last 30 days.",
                     "action": "Pitch one story this week to a Nigerian business or lifestyle blog (BellaNaija, "
                               "TechCabal, Nairametrics, Pulse). A launch, a customer story or a number people "
                               "find surprising works best."})
    if comps and leader is not main and leader["n30"] > 0:
        ratio = round(leader["n30"] / max(main["n30"], 1), 1)
        themes = ", ".join(leader["themes"][:4]) or "their recent activity"
        tips.append({"title": f"{leader['name']} is louder than you",
                     "why": f"{leader['name']} had {leader['n30']} stories in 30 days vs {main['n30']} for "
                            f"{main['name']} ({ratio}×). Their coverage is about: {themes}.",
                     "action": f"Don't copy them — answer them. Post your take on '{leader['themes'][0]}' "
                               f"if it fits your brand, and give people a reason to compare you."
                               if leader["themes"] else "Study their last 5 stories and plan a response campaign."})
    yt = [(p, sum(v["views"] or 0 for v in (p.get("youtube") or []))) for p in allp]
    yt = [x for x in yt if x[1]]
    if len(yt) >= 2:
        top, views = max(yt, key=lambda x: x[1])
        mine = dict((p["name"], v) for p, v in yt).get(main["name"], 0)
        if top is not main:
            best = max(top["youtube"], key=lambda v: v["views"] or 0)
            tips.append({"title": f"{top['name']} wins on YouTube",
                         "why": f"Their top recent videos total {views:,} views vs {mine:,} for {main['name']}. "
                                f"Biggest: “{best['title'][:90]}”.",
                         "action": "Study the title and thumbnail pattern of their top 3 videos, then do the "
                                   "opposite angle on the same topic — same demand, your own voice."})
    if main["n"] and main["neg_pct"] >= 25:
        heads = "; ".join(a["title"][:80] for a in main["negative"][:2])
        tips.append({"title": "Deal with the negative talk first",
                     "why": f"{main['neg_pct']}% of {main['name']}'s coverage reads negative. E.g. {heads}",
                     "action": "Reply publicly and calmly, show what you fixed, then post 2–3 customer wins. "
                               "Going viral while people are upset makes it worse."})
    for c in comps:
        if c["n"] >= 5 and c["neg_pct"] >= 30:
            tips.append({"title": f"Opening: {c['name']} customers are unhappy",
                         "why": f"{c['neg_pct']}% of {c['name']}'s coverage is negative.",
                         "action": f"Run content showing how you do it better on the exact complaint — never "
                                   f"name-and-shame, just show the difference."})
            break
    gap = [t for c in comps for t in c["themes"][:6] if t not in main["themes"]]
    if gap:
        g = list(dict.fromkeys(gap))[:4]
        tips.append({"title": "Topics your competitors own and you don't",
                     "why": "Competitor coverage keeps mentioning: " + ", ".join(g) + ".",
                     "action": "Pick the one closest to your brand and make a short series (3 posts) on it."})
    q = [s for s in sugg if any(w in s.lower() for w in ("how", "price", "cost", "near", "vs", "review",
                                                          "is ", "best", "app", "login", "number", "customer"))]
    if q:
        tips.append({"title": "Answer what people are searching",
                     "why": "People are typing: " + "; ".join(f"“{s}”" for s in q[:4]) + ".",
                     "action": "Turn each search into one short video or carousel that answers it in the first "
                               "3 seconds. These match real demand, so they travel further."})
    if main["wiki"] and main["wiki"]["change"] is not None and abs(main["wiki"]["change"]) >= 20:
        w = main["wiki"]
        up = w["change"] > 0
        tips.append({"title": "Interest is " + ("rising" if up else "falling"),
                     "why": f"Wikipedia views for '{w['title']}' are {'up' if up else 'down'} "
                            f"{abs(w['change'])}% vs the month before.",
                     "action": "Post more now while attention is high." if up else
                               "Create a reason to talk: a giveaway, a collab or a bold opinion post."})
    return tips


def viral_ideas(main: dict, comps: list[dict], sugg: list[str]) -> list[str]:
    name = main["name"]
    ideas = []
    for s in sugg[:3]:
        ideas.append(f"“{s}?” — 15-second answer video. Hook: say the question on screen in the first second.")
    if main["themes"]:
        ideas.append(f"Trend-jack: a hot take on “{main['themes'][0]}” — what everyone is getting wrong about it.")
    if comps:
        ideas.append(f"“{name} vs {comps[0]['name']} — honest comparison” (let real customers speak; stitch-able).")
    ideas.append(f"Behind the scenes: one day inside {name}, shot on phone, captioned in Pidgin and English.")
    ideas.append(f"Customer reaction series: people trying {name} for the first time — reply-to-comment format.")
    return ideas[:7]


def run(query: str, competitors: list[str] | None = None, country: str = "NG", http_get=None) -> dict:
    query = (query or "").strip()[:80]
    comps = [c.strip()[:80] for c in (competitors or []) if c.strip()][:MAX_TERMS - 1]
    terms = [query] + comps
    with ThreadPoolExecutor(max_workers=8) as ex:
        prof_f = {t: ex.submit(_profile, t, country, http_get) for t in terms}
        sugg_f = ex.submit(suggestions, query, country, http_get)
        profiles = {t: f.result() for t, f in prof_f.items()}
        sugg = sugg_f.result()
    main, others = profiles[query], [profiles[c] for c in comps]
    did_you_mean = []
    if main["n30"] < 5:
        ql = query.lower()
        if main["wiki"] and main["wiki"]["title"].lower() != ql:
            did_you_mean.append(main["wiki"]["title"])
        did_you_mean += [s for s in sugg[:3] if not s.lower().startswith(ql)]
        did_you_mean = list(dict.fromkeys(did_you_mean))[:4]
    total = sum(p["n30"] for p in profiles.values())
    for p in profiles.values():
        p["share"] = round(p["n30"] / total * 100) if total else 0
    return {"query": query, "country": country, "main": main, "competitors": others,
            "all": [main] + others, "suggestions": sugg, "advice": _advice(main, others, sugg),
            "ideas": viral_ideas(main, others, sugg), "did_you_mean": did_you_mean,
            "youtube_on": main.get("youtube") is not None,
            "when": _now().strftime("%d %b %Y, %H:%M UTC")}
