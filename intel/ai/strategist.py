"""AI strategist for the Research page.

Uses Groq's free tier (no credit card; Groq's terms say API data isn't used for training).
Set GROQ_API_KEY in Vercel. Without a key, the page still works: it shows the rule-based
advice and a ready-to-paste pack for Claude Pro.

The model only sees the facts we collected on the page (headlines, searches, numbers),
and is told not to invent anything beyond them.
"""
from __future__ import annotations

import json

import requests

from ..config import env

API = "https://api.groq.com/openai/v1"
PREFERRED = ("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b",
             "meta-llama/llama-4-maverick-17b-128e-instruct", "meta-llama/llama-4-scout-17b-16e-instruct",
             "qwen/qwen3-32b", "llama-3.1-8b-instant")

SYSTEM = """You are a senior brand strategist and social media growth expert for African markets
(Nigeria first). You advise agencies that sell to brands, shows, podcasts and creators.
Use ONLY the facts in the JSON (headlines, search phrases, video titles, counts). Never invent numbers,
followers, views or events. If something isn't in the facts, say "not in the data".
Write in plain, confident English a client would pay for. Short sections, bullets, no fluff.
Sections, in this order, with these exact headings:
## The situation in one paragraph
## Why the leader is ahead
(what they do, how often, which themes and formats win attention — cite headlines/titles as evidence)
## Gaps you can own
(themes, audiences or formats nobody in the set covers well — so the client doesn't look like a copy)
## Content plan for the next 2 weeks
(8 specific posts. For each: format (Reel/TikTok/carousel/YouTube Short/X thread), the hook as the first line,
why it fits the data. Make them clearly different from the competitors' style.)
## What people are asking (search demand)
(turn the search phrases into content or offers)
## Risks to manage
## 3 numbers to track weekly
If there are no competitors, compare against the category instead and say so."""


def available() -> bool:
    return bool(env("GROQ_API_KEY"))


def facts_from(r: dict) -> dict:
    def slim(p):
        return {"name": p["name"], "stories_7d": p["n7"], "stories_30d": p["n30"], "share_of_voice_pct": p.get("share"),
                "positive_pct": p["pos_pct"], "negative_pct": p["neg_pct"], "themes": p["themes"],
                "top_publishers": p["publishers"],
                "recent_headlines": [f"{a['title']} ({a['publisher']}, {(a['published'] or '')[:10]}, {a['sentiment']})"
                                     for a in p["latest"][:8]],
                "negative_headlines": [a["title"] for a in p["negative"][:3]],
                "wikipedia_interest": p.get("wiki"),
                "youtube": [{"title": v["title"], "channel": v["channel"], "views": v.get("views"),
                             "published": v.get("published", "")[:10]} for v in (p.get("youtube") or [])[:6]]}
    return {"client_brand": slim(r["main"]), "competitors": [slim(c) for c in r["competitors"]],
            "google_search_phrases": r["suggestions"], "country": r["country"], "collected": r["when"]}


def handoff_prompt(r: dict) -> str:
    return SYSTEM + "\n\nFACTS (JSON):\n" + json.dumps(facts_from(r), ensure_ascii=False, indent=1)


def _pick_model(key: str, get=requests.get) -> str:
    if env("GROQ_MODEL"):
        return env("GROQ_MODEL")
    try:
        r = get(f"{API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=8)
        ids = {m["id"] for m in r.json().get("data", [])}
        for m in PREFERRED:
            if m in ids:
                return m
        chat = sorted(i for i in ids if "whisper" not in i and "guard" not in i and "tts" not in i)
        if chat:
            return chat[0]
    except Exception:  # noqa: BLE001
        pass
    return PREFERRED[0]


def write_strategy(r: dict, post=requests.post, get=requests.get) -> str:
    key = env("GROQ_API_KEY")
    if not key:
        raise RuntimeError("AI strategist is off: add a free GROQ_API_KEY in Vercel settings.")
    model = _pick_model(key, get)
    body = {"model": model, "temperature": 0.6, "max_tokens": 2200,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": "FACTS (JSON):\n" + json.dumps(facts_from(r), ensure_ascii=False)}]}
    resp = post(f"{API}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=40)
    if resp.status_code == 401:
        raise RuntimeError("Groq refused the key — check GROQ_API_KEY.")
    if resp.status_code == 429:
        raise RuntimeError("AI is busy (free-tier limit). Try again in a minute.")
    if resp.status_code != 200:
        raise RuntimeError(f"AI service error {resp.status_code}.")
    return resp.json()["choices"][0]["message"]["content"].strip()
