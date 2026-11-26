"""Turn report narratives into safe HTML and PDF, and save reports."""
from __future__ import annotations

import html
import io
import json
import re

from .ai.engines import write_narrative
from .reports import build_facts


def md_to_html(md: str) -> str:
    """Small, safe Markdown subset: headings, bullets, bold, links. Everything else is escaped."""
    out, in_list = [], False

    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener" target="_blank">\1</a>', s)
        return s

    for raw in md.splitlines():
        line = raw.rstrip()
        if re.match(r"^\s*[-*] ", line):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(re.sub(r'^\s*[-*] ', '', line))}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if not line.strip():
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            lvl = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
        else:
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def create_report(conn, entity_id: int, days: int = 30, engine: str = "builtin") -> int:
    facts = build_facts(conn, entity_id, days)
    narrative = write_narrative(engine, facts)
    title = f"{facts['entity']['name']} — {facts['entity']['kind'].title()} intelligence report"
    if facts["is_sample"]:
        title += " (SAMPLE DATA)"
    cur = conn.execute(
        """INSERT INTO reports(entity_id,kind,title,period_start,period_end,engine,facts,narrative,missing)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (entity_id, facts["entity"]["kind"], title, facts["period"]["start"], facts["period"]["end"],
         engine, json.dumps(facts, ensure_ascii=False), narrative, json.dumps(facts["missing"])))
    conn.commit()
    return cur.lastrowid
