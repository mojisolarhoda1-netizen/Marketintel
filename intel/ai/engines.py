"""Free AI engines that turn report facts into a written narrative.

builtin        - always available, offline, deterministic. Writes only from facts.
ollama         - free local open-source model (https://ollama.com), if running on this laptop.
claude_handoff - builds a prompt + data pack to paste into your Claude Pro chat. No API cost.
claude_api     - PAID. Present for later; refuses to run unless ALLOW_PAID_AI=true.
"""
from __future__ import annotations

import json

import requests

from ..db import platform_name

from ..config import env, env_bool

RULES = """You are a senior market-intelligence analyst for African markets.
Write a concise report for a marketing manager using ONLY the JSON facts provided.
Rules:
- Never invent numbers, sources, dates, competitors or events. If a fact is not in the JSON, say it is not available.
- State the reporting period at the top.
- Use plain language. Short paragraphs. No hype.
- Sections: Summary; Audience and growth; Conversation and sentiment; Competitors; Website changes;
  Risks and opportunities; What's missing.
- In "What's missing", list every item from the `missing` array.
- If `is_sample` is true, open with: "SAMPLE DATA — not real market data."
"""


def _fmt(n) -> str:
    if n is None:
        return "n/a"
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.2f}"
    return f"{int(n):,}"


def builtin_narrative(f: dict) -> str:
    e, per = f["entity"], f["period"]
    L = []
    if f["is_sample"]:
        L.append("**SAMPLE DATA — not real market data.**\n")
    L.append(f"## Summary\n")
    L.append(f"Reporting period: {per['start'][:10]} to {per['end'][:10]} ({per['days']} days).")
    bits = []
    for p in f["platforms"]:
        fol = next((m for m in p["metrics"] if m["metric"] == "followers"), None)
        if fol:
            g = fol["growth"]
            if g and g["pct"] is not None:
                bits.append(f"{platform_name(p['platform'])} followers {'grew' if g['change'] >= 0 else 'fell'} "
                            f"{abs(g['pct']):.1f}% to {_fmt(fol['latest'])}")
            else:
                bits.append(f"{platform_name(p['platform'])} followers stand at {_fmt(fol['latest'])}")
    m, s = f["mentions"], f["sentiment"]
    if m["count"]:
        bits.append(f"{m['count']} press mention{'s' if m['count'] != 1 else ''} were found")
    if s["count"]:
        tone = "mostly positive" if (s["net"] or 0) >= 20 else "mostly negative" if (s["net"] or 0) <= -20 else "mixed"
        bits.append(f"the tone of coverage was {tone} (net sentiment {s['net']:+.0f})")
    L.append((f"For {e['name']}: " + "; ".join(bits) + ".") if bits else
             f"Not enough data has been collected for {e['name']} in this period to summarise.")

    L.append("\n## Audience and growth\n")
    if not any(p["metrics"] for p in f["platforms"]):
        L.append("No audience numbers are available yet.")
    for p in f["platforms"]:
        for mt in p["metrics"]:
            g = mt["growth"]
            change = (f", {'+' if g['change'] >= 0 else ''}{_fmt(g['change'])} ({g['pct']:+.1f}%) since "
                      f"{g['start'][:10]}") if g and g["pct"] is not None else ""
            L.append(f"- {platform_name(p['platform'])} · {mt['label']}: **{_fmt(mt['latest'])}** "
                     f"(as of {mt['as_of'][:10]}, source: {mt['source']}){change}")

    L.append("\n## Conversation and sentiment\n")
    if m["count"]:
        L.append(f"{m['count']} mentions: {s['positive']} positive, {s['neutral']} neutral, {s['negative']} negative.")
        if m["publishers"]:
            L.append("Most coverage came from " + ", ".join(f"{p} ({n})" for p, n in m["publishers"][:4]) + ".")
        if m["themes"]:
            L.append("Recurring words in headlines: " + ", ".join(w for w, _ in m["themes"][:8]) + ".")
        neg = [x for x in m["items"] if x["sentiment_label"] == "negative"][:3]
        if neg:
            L.append("Headlines scored negative:")
            L += [f"- {x['title']} ({x['publisher']}, {(x['published_at'] or '')[:10]})" for x in neg]
    else:
        L.append("No mentions were found in the enabled sources for this period.")
    if f["top_content"]:
        L.append("\nBest-performing recent posts by likes + comments:")
        for t in f["top_content"][:3]:
            L.append(f"- {(t['title'] or 'Untitled')[:90]} — {_fmt(t['likes'])} likes, {_fmt(t['comments'])} comments")

    if f["entity"]["kind"] == "brand":
        L.append("\n## Competitors\n")
        if f["competitors"]:
            leader = f["competitors"][0]
            L.append("Share of voice in press mentions this period:")
            for c in f["competitors"]:
                share = f"{c['share']}%" if c["share"] is not None else "n/a"
                net = c["sentiment"]["net"]
                L.append(f"- {c['name']}: {c['mentions']} mentions ({share})"
                         + (f", net sentiment {net:+.0f}" if net is not None else ""))
            if leader["mentions"]:
                L.append(f"{leader['name']} led the conversation.")
        else:
            L.append("No competitors linked yet.")
        L.append("\n## Website changes\n")
        if f["website_changes"]:
            for p in f["website_changes"]:
                pr = f" Prices seen: {', '.join(p['prices'][:6])}." if p["prices"] else ""
                L.append(f"- {p['collected_at'][:10]}: {p['url']} changed (title: {p['title'] or 'n/a'}).{pr}")
        else:
            L.append("No website changes detected in this period.")

    if f["authenticity"]:
        L.append("\n## Authenticity signals (signal, not proof)\n")
        for sg in f["authenticity"]:
            L.append(f"- {platform_name(sg['platform'])} {sg['name']}: **{sg['level']}** — {sg['detail']}")

    L.append("\n## Risks and opportunities\n")
    ro = []
    if s["count"] and s["negative"] > s["positive"]:
        ro.append("Negative coverage outweighs positive; review the negative headlines above and prepare responses.")
    if f["alerts"]:
        ro += [f"Alert: {a['message']}" for a in f["alerts"][:4]]
    if f["competitors"] and f["competitors"][0]["id"] != f["entity"]["id"] and f["competitors"][0]["mentions"]:
        ro.append(f"{f['competitors'][0]['name']} is getting more press attention; look at what they announced.")
    L += [f"- {x}" for x in ro] or ["- No notable risks detected from the data collected."]

    L.append("\n## Where the data came from\n")
    prov = f.get("provenance") or {}
    labels = {"LIVE": "collected automatically from public sources", "MANUAL": "typed in by hand",
              "IMPORTED": "imported from a CSV file", "SAMPLE": "fictional sample data"}
    L += [f"- {n} observations {labels.get(k, k)} ({k})" for k, n in sorted(prov.items())] or \
        ["- No observations in this period."]

    L.append("\n## What's missing\n")
    L += [f"- {x}" for x in f["missing"]] or ["- Nothing flagged."]
    L.append("\n## Sources\n")
    L += [f"- {u['name']}" + (f" (last collected {u['last'][:16].replace('T', ' ')} UTC)" if u.get("last") else "")
          for u in f["sources_used"]] or ["- No successful collections yet."]
    return "\n".join(L)


def compact_facts(f: dict) -> dict:
    """Smaller copy for model prompts."""
    g = json.loads(json.dumps(f))
    g["mentions"]["items"] = [{k: v for k, v in i.items() if k in ("title", "publisher", "published_at",
                                                                   "sentiment_label")} for i in g["mentions"]["items"]]
    return g


def claude_handoff_prompt(f: dict) -> str:
    return (RULES + "\nReturn the report in Markdown.\n\nFACTS (JSON):\n```json\n"
            + json.dumps(compact_facts(f), indent=1, ensure_ascii=False) + "\n```\n")


def ollama_status() -> tuple[bool, str]:
    from ..config import on_vercel
    if on_vercel() or env("OLLAMA_URL", "x").lower() in ("", "off", "none"):
        return False, "Local AI runs only on a laptop install"
    url = env("OLLAMA_URL", "http://localhost:11434")
    try:
        r = requests.get(f"{url}/api/tags", timeout=2)
        models = [m["name"] for m in r.json().get("models", [])]
        want = env("OLLAMA_MODEL", "llama3.2:3b")
        if not models:
            return False, "Ollama is running but has no models. Run: ollama pull " + want
        if want not in models:
            return False, f"Model {want} not installed. Installed: {', '.join(models)}"
        return True, f"Ollama ready ({want})"
    except Exception:
        return False, "Ollama not running (optional, free: https://ollama.com)"


def ollama_narrative(f: dict) -> str:
    url, model = env("OLLAMA_URL", "http://localhost:11434"), env("OLLAMA_MODEL", "llama3.2:3b")
    prompt = RULES + "\nFACTS (JSON):\n" + json.dumps(compact_facts(f), ensure_ascii=False)
    r = requests.post(f"{url}/api/generate", json={"model": model, "prompt": prompt, "stream": False,
                                                   "options": {"temperature": 0.2}}, timeout=600)
    r.raise_for_status()
    text = r.json().get("response", "").strip()
    if not text:
        raise RuntimeError("Ollama returned an empty answer")
    # Always append the verified missing list, whatever the model wrote.
    return text + "\n\n## What's missing (verified)\n" + "\n".join(f"- {m}" for m in f["missing"])


def claude_api_narrative(f: dict) -> str:
    if not env_bool("ALLOW_PAID_AI") or not env("ANTHROPIC_API_KEY"):
        raise PermissionError("Paid AI is switched off. Set ALLOW_PAID_AI=true and ANTHROPIC_API_KEY when approved.")
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": env("CLAUDE_MODEL", "claude-haiku-5-5"), "max_tokens": 1500, "system": RULES,
              "messages": [{"role": "user", "content": json.dumps(compact_facts(f), ensure_ascii=False)}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def available_engines() -> list[dict]:
    """Free engines only. (The paid Claude API code stays in this file, switched off and not offered.)"""
    ok, msg = ollama_status()
    return [
        {"id": "builtin", "name": "Built-in analyst (offline)", "available": True, "note": "Free, instant"},
        {"id": "ollama", "name": "Local AI via Ollama", "available": ok, "note": msg},
        {"id": "claude_handoff", "name": "Claude Pro hand-off", "available": True,
         "note": "Copy a data pack into your Claude chat"},
    ]


def write_narrative(engine: str, facts: dict) -> str:
    if engine == "builtin":
        return builtin_narrative(facts)
    if engine == "ollama":
        return ollama_narrative(facts)
    if engine == "claude_api":
        return claude_api_narrative(facts)
    if engine == "claude_handoff":
        return builtin_narrative(facts)  # stored report uses builtin text; the prompt is offered separately
    raise ValueError("unknown engine")
