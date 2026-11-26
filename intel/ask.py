"""Ask questions about your stored data — free.

The app gathers the stored facts for what you're asking about (one brand/creator, a
watchlist, or everything) and either:
  - answers with a local model through Ollama (free, if installed), or
  - prepares a data pack you paste into your Claude Pro chat.
The model is told to answer only from those facts and to say when something isn't there.
"""
from __future__ import annotations

import json

import requests

from .ai.engines import compact_facts, ollama_status
from .config import env
from .reports import build_facts

ASK_RULES = """You are a market-intelligence analyst. Answer the user's question using ONLY the JSON facts below.
- Quote numbers exactly as given, with their dates and sources.
- If the facts don't contain the answer, say "That isn't in the collected data" and say what data would answer it.
- Never invent numbers, competitors, events or sources. Keep the answer short and practical.
- Items marked is_sample are fictional demo data: say so if you use them.
"""


def gather(conn, entity_ids: list[int], days: int = 30) -> list[dict]:
    out = []
    for eid in entity_ids[:12]:          # keep the pack a sensible size
        f = compact_facts(build_facts(conn, eid, days))
        f["mentions"]["items"] = f["mentions"]["items"][:8]
        out.append(f)
    return out


def pack(question: str, facts: list[dict]) -> str:
    return (ASK_RULES + f"\nQUESTION: {question.strip()}\n\nFACTS (JSON):\n```json\n"
            + json.dumps(facts, indent=1, ensure_ascii=False) + "\n```\n")


def answer_with_ollama(question: str, facts: list[dict]) -> str:
    ok, msg = ollama_status()
    if not ok:
        raise RuntimeError(msg)
    url, model = env("OLLAMA_URL", "http://localhost:11434"), env("OLLAMA_MODEL", "llama3.2:3b")
    r = requests.post(f"{url}/api/generate", timeout=600, json={
        "model": model, "prompt": pack(question, facts), "stream": False, "options": {"temperature": 0.1}})
    r.raise_for_status()
    return r.json().get("response", "").strip() or "The local model returned no answer."
