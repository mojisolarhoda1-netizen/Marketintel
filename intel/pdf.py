"""Client-ready PDF report built from stored facts (reportlab, free).

Layout: title band → summary → key numbers table → follower chart → conversation &
sentiment → top headlines → competitors → website changes → authenticity (influencers)
→ analyst commentary → what's missing → sources, provenance and method.
Sample-data reports carry a diagonal SAMPLE watermark on every page.
"""
from __future__ import annotations

import html
import io
import re
from datetime import datetime, timezone

from reportlab.graphics.charts.lineplots import LinePlot

from .db import platform_name
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK = colors.HexColor("#111827")
INK2 = colors.HexColor("#4b5563")
LINE = colors.HexColor("#e5e7eb")
ACCENT = colors.HexColor("#2457d6")
SOFT = colors.HexColor("#f3f6fd")
POS, NEG = colors.HexColor("#15803d"), colors.HexColor("#c2410c")
SERIES_COLOURS = [ACCENT, colors.HexColor("#e07b39"), colors.HexColor("#2a9d8f"), colors.HexColor("#9b5de5")]

ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["BodyText"], fontName="Helvetica", fontSize=10, leading=14, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11.5, textColor=INK2)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.5, leading=11)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.white)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=12,
                    spaceAfter=5, textColor=INK)
BAND_SUB = ParagraphStyle("bandsub", parent=BODY, fontSize=9.5, leading=13, textColor=colors.HexColor("#dbe4ff"))


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def num(v, dp=0) -> str:
    if v is None:
        return "—"
    v = float(v)
    if dp == 0 and v.is_integer():
        return f"{int(v):,}"
    return f"{v:,.{dp or 2}f}"


def inline_md(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<link href="\2" color="#2457d6">\1</link>', s)


def table(rows, widths, header=True, zebra=True):
    data = [[c if not isinstance(c, str) else Paragraph(c, CELL) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), SOFT), ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT)]
    t.setStyle(TableStyle(style))
    return t


def follower_chart(series: dict, width=170 * mm, height=62 * mm):
    d = Drawing(width, height)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 38, 22, width - 60, height - 40
    data, names = [], []
    for name, pts in series.items():
        xy = [(datetime.fromisoformat(p[0].replace("Z", "+00:00")).timestamp() / 86400, p[1]) for p in pts]
        if len(xy) >= 2:
            data.append(xy); names.append(name)
    if not data:
        return None
    lp.data = data
    for i in range(len(data)):
        lp.lines[i].strokeColor = SERIES_COLOURS[i % len(SERIES_COLOURS)]
        lp.lines[i].strokeWidth = 1.6
    xs = [x for s in data for x, _ in s]
    ys = [y for s in data for _, y in s]
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = min(xs), max(xs)
    span = (max(ys) - min(ys)) or max(ys) * 0.05 or 1
    lp.yValueAxis.valueMin, lp.yValueAxis.valueMax = min(ys) - span * 0.1, max(ys) + span * 0.1
    lp.xValueAxis.labelTextFormat = lambda v: datetime.fromtimestamp(v * 86400, tz=timezone.utc).strftime("%d %b")
    lp.yValueAxis.labelTextFormat = lambda v: f"{v/1000:.1f}k" if abs(v) >= 1000 else f"{v:.0f}"
    for ax in (lp.xValueAxis, lp.yValueAxis):
        ax.labels.fontName, ax.labels.fontSize, ax.labels.fillColor = "Helvetica", 7, INK2
        ax.strokeColor = LINE
    lp.yValueAxis.visibleGrid, lp.yValueAxis.gridStrokeColor = True, LINE
    lp.xValueAxis.maximumTicks, lp.yValueAxis.maximumTicks = 6, 5
    d.add(lp)
    x = 40
    for i, n in enumerate(names):
        d.add(String(x, 4, f"— {platform_name(n)}", fontName="Helvetica", fontSize=7.5,
                     fillColor=SERIES_COLOURS[i % len(SERIES_COLOURS)]))
        x += 90
    return d


def narrative_sections(md: str) -> dict[str, list[str]]:
    out, cur = {}, "_intro"
    for line in md.splitlines():
        m = re.match(r"^#{1,4}\s+(.*)", line)
        if m:
            cur = m.group(1).strip().lower(); out.setdefault(cur, []); continue
        if line.strip():
            out.setdefault(cur, []).append(line)
    return out


def lines_to_flow(lines: list[str]) -> list:
    flow = []
    for ln in lines:
        if re.match(r"^\s*[-*] ", ln):
            flow.append(Paragraph(inline_md(re.sub(r"^\s*[-*] ", "", ln)), BODY, bulletText="•"))
        else:
            flow.append(Paragraph(inline_md(ln), BODY))
    return flow


def build_pdf(report: dict, facts: dict, prepared_by: str = "", footer: str = "") -> bytes:
    buf = io.BytesIO()
    sample = bool(facts.get("is_sample"))
    e, per = facts["entity"], facts["period"]

    def on_page(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(INK2)
        left = footer or "Confidential"
        canvas.drawString(18 * mm, 10 * mm, left[:110])
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"{prepared_by + ' · ' if prepared_by else ''}Page {doc.page}")
        if sample:
            canvas.setFillColor(colors.Color(0.85, 0.35, 0.1, alpha=0.13))
            canvas.setFont("Helvetica-Bold", 64)
            canvas.translate(w / 2, h / 2); canvas.rotate(35)
            canvas.drawCentredString(0, 0, "SAMPLE DATA")
            canvas.setFont("Helvetica-Bold", 20)
            canvas.drawCentredString(0, -34, "fictional — not real market data")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm,
                            bottomMargin=18 * mm, title=report["title"], author=prepared_by or "Market Intel")
    W = A4[0] - 36 * mm
    story = []

    # ---- title band ----
    sub = (f"{e['kind'].title()} intelligence report · {per['start'][:10]} to {per['end'][:10]} ({per['days']} days)"
           f"<br/>{esc(e.get('category') or '')}{' · ' if e.get('category') else ''}{esc(e.get('country') or '')}"
           f" · generated {report['generated_at'][:10]}{(' · prepared by ' + esc(prepared_by)) if prepared_by else ''}")
    band = Table([[Paragraph(esc(e["name"]), H1)], [Paragraph(sub, BAND_SUB)]], colWidths=[W])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#14213d")),
                              ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (0, 0), 12),
                              ("BOTTOMPADDING", (0, -1), (-1, -1), 12)]))
    story += [band, Spacer(1, 8)]
    if sample:
        story.append(Paragraph("<b>SAMPLE DATA — fictional, for demonstration only. Do not share with clients.</b>",
                               ParagraphStyle("w", parent=BODY, textColor=NEG)))

    sections = narrative_sections(report["narrative"])
    # ---- summary ----
    story.append(Paragraph("Summary", H2))
    story += lines_to_flow([l for l in sections.get("summary", []) if not l.startswith("**SAMPLE")]) or \
        [Paragraph("Not enough data collected yet to summarise.", BODY)]

    # ---- key numbers ----
    rows = [["Platform", "Measure", "Latest", "Change in period", "As of", "Origin"]]
    for p in facts["platforms"]:
        for m in p["metrics"]:
            g = m.get("growth")
            dp = 2 if m["metric"] == "engagement_rate" else 0
            chg = (f"{'+' if g['change'] >= 0 else ''}{num(g['change'], dp)} ({g['pct']:+.1f}%)"
                   if g and g.get("pct") is not None else "—")
            latest = num(m["latest"], dp) + ("%" if m["metric"] == "engagement_rate" else "")
            rows.append([esc(platform_name(p["platform"])), esc(m["label"]), latest, chg,
                         esc(m["as_of"][:10]), esc(m.get("origin", ""))])
    story.append(Paragraph("Key numbers", H2))
    if len(rows) > 1:
        story.append(table(rows, [22 * mm, 53 * mm, 24 * mm, 32 * mm, 22 * mm, W - 153 * mm]))
    else:
        story.append(Paragraph("No audience numbers collected yet.", BODY))
    chart = follower_chart(facts.get("series") or {})
    if chart:
        story += [Spacer(1, 6), KeepTogether([Paragraph("Followers over the period", SMALL), chart])]

    # ---- conversation ----
    s, m = facts["sentiment"], facts["mentions"]
    story.append(Paragraph("Conversation and sentiment", H2))
    if m["count"]:
        story.append(table([["Mentions", "Positive", "Neutral", "Negative", "Net sentiment"],
                            [num(m["count"]), num(s["positive"]), num(s["neutral"]), num(s["negative"]),
                             f"{s['net']:+.0f}" if s["net"] is not None else "—"]], [W / 5] * 5))
        if m.get("publishers"):
            story.append(Paragraph("Top publishers: " + ", ".join(f"{esc(p)} ({n})" for p, n in m["publishers"][:5]),
                                   SMALL))
        hl = [["Date", "Publisher", "Headline", "Tone", "Origin"]]
        from .db import origin_of
        for it in m["items"][:12]:
            title = esc(it["title"])
            if it.get("url"):
                title = f'<link href="{esc(it["url"])}" color="#2457d6">{title}</link>'
            hl.append([esc((it["published_at"] or "")[:10]), esc(it["publisher"] or ""), title,
                       esc(it["sentiment_label"] or ""), origin_of(it.get("source_id"))[0]])
        story += [Spacer(1, 6), table(hl, [22 * mm, 28 * mm, W - 88 * mm, 18 * mm, 20 * mm])]
    else:
        story.append(Paragraph("No mentions found in the enabled sources for this period.", BODY))
    if facts.get("top_content"):
        tc = [["Recent post / video", "Likes", "Comments", "Views"]]
        for t in facts["top_content"][:5]:
            title = esc((t["title"] or "Untitled")[:90])
            if t.get("url"):
                title = f'<link href="{esc(t["url"])}" color="#2457d6">{title}</link>'
            tc.append([title, num(t["likes"]), num(t["comments"]), num(t["views"])])
        story += [Spacer(1, 6), table(tc, [W - 66 * mm, 22 * mm, 22 * mm, 22 * mm])]

    # ---- competitors ----
    if e["kind"] == "brand":
        story.append(Paragraph("Competitors", H2))
        if facts["competitors"]:
            cr = [["Brand", "Mentions", "Share of voice", "Net sentiment", "Followers (all platforms)"]]
            for c in facts["competitors"]:
                cr.append([("<b>%s</b>" % esc(c["name"])) if c["id"] == e["id"] else esc(c["name"]), num(c["mentions"]),
                           f"{c['share']}%" if c["share"] is not None else "—",
                           f"{c['sentiment']['net']:+.0f}" if c["sentiment"]["net"] is not None else "—",
                           num(c.get("followers_total"))])
            story.append(table(cr, [W - 120 * mm, 22 * mm, 30 * mm, 28 * mm, 40 * mm]))
        else:
            story.append(Paragraph("No competitors linked.", BODY))
        story.append(Paragraph("Website changes", H2))
        if facts["website_changes"]:
            wr = [["Date", "Page", "Title", "Prices seen"]]
            for p in facts["website_changes"][:8]:
                wr.append([esc(p["collected_at"][:10]), esc(p["url"]), esc(p["title"] or ""), esc(", ".join(p["prices"][:6]))])
            story.append(table(wr, [22 * mm, 50 * mm, W - 122 * mm, 50 * mm]))
        else:
            story.append(Paragraph("No website changes detected in this period.", BODY))

    if facts.get("authenticity"):
        story.append(Paragraph("Authenticity signals (signal, not proof)", H2))
        ar = [["Platform", "Check", "Result", "Detail"]]
        for sg in facts["authenticity"]:
            ar.append([esc(platform_name(sg["platform"])), esc(sg["name"]), esc(sg["level"]), esc(sg["detail"])])
        story.append(table(ar, [22 * mm, 42 * mm, 18 * mm, W - 82 * mm]))

    # ---- commentary ----
    if report["engine"] != "builtin":
        body = [l for k, v in sections.items() if k not in ("summary",) for l in
                ([f"**{k.title()}**"] if k != "_intro" else []) + v]
        story.append(Paragraph("Analyst commentary", H2))
        story += lines_to_flow(body)
    else:
        story.append(Paragraph("Risks and opportunities", H2))
        story += lines_to_flow(sections.get("risks and opportunities", [])) or [Paragraph("None flagged.", BODY)]

    story.append(Paragraph("What's missing", H2))
    story += lines_to_flow([f"- {x}" for x in facts["missing"]]) or [Paragraph("Nothing flagged.", BODY)]

    story.append(Paragraph("Sources, data origin and method", H2))
    src = "; ".join(esc(u["name"]) + (f" (last collected {esc(u['last'][:10])})" if u.get("last") else "")
                    for u in facts["sources_used"]) or "No successful collections yet."
    prov = facts.get("provenance") or {}
    origin_txt = ", ".join(f"{n} {k.lower()}" for k, n in sorted(prov.items())) or "none"
    story += [Paragraph(f"<b>Sources:</b> {src}", SMALL),
              Paragraph(f"<b>Observations in period by origin:</b> {origin_txt}. LIVE = collected automatically from "
                        "public sources; MANUAL = typed in; IMPORTED = from a CSV; SAMPLE = fictional.", SMALL),
              Paragraph("<b>Method:</b> every figure is read from data stored with its source and collection date. "
                        "Growth compares the first and last observation in the period. Sentiment is scored by a "
                        "transparent word list (English and Nigerian Pidgin), not a trained AI model, and is a signal "
                        "rather than a verdict. Authenticity checks are rule-based comparisons, not proof of fraud. "
                        f"Report engine: {esc(report['engine'])}.", SMALL)]
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def build_campaign_pdf(f: dict, summary: list[str], prepared_by: str = "", footer: str = "") -> bytes:
    """Client-ready campaign report from campaigns.facts()."""
    buf = io.BytesIO()
    sample = f.get("is_sample")

    def on_page(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFont("Helvetica", 7.5); canvas.setFillColor(INK2)
        canvas.drawString(18 * mm, 10 * mm, (footer or "Confidential")[:110])
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"{prepared_by + ' · ' if prepared_by else ''}Page {doc.page}")
        if sample:
            canvas.setFillColor(colors.Color(0.85, 0.35, 0.1, alpha=0.13)); canvas.setFont("Helvetica-Bold", 64)
            canvas.translate(w / 2, h / 2); canvas.rotate(35); canvas.drawCentredString(0, 0, "SAMPLE DATA")
        canvas.restoreState()

    c = f["campaign"]
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm,
                            bottomMargin=18 * mm, title=f"{c['name']} campaign report")
    W = A4[0] - 36 * mm
    sub = (f"Campaign report · {esc(c['brand'])} · {c['start_date']} to {c['end_date']} ({esc(f['status'])})"
           f"<br/>Baseline: {f['baseline']['start']} to {f['baseline']['end']}"
           f"{(' · prepared by ' + esc(prepared_by)) if prepared_by else ''}")
    band = Table([[Paragraph(esc(c["name"]), H1)], [Paragraph(sub, BAND_SUB)]], colWidths=[W])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#14213d")),
                              ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (0, 0), 12),
                              ("BOTTOMPADDING", (0, -1), (-1, -1), 12)]))
    story = [band, Spacer(1, 8), Paragraph("Summary", H2)] + [Paragraph(esc(s), BODY) for s in summary]
    m = f["mentions"]
    story += [Paragraph("Campaign vs baseline", H2),
              table([["Measure", "Baseline", "Campaign", "Change"],
                     ["Press mentions", num(m["baseline"]), num(m["campaign"]),
                      f"{m['pct']:+.0f}%" if m["pct"] is not None else "—"],
                     ["Average tone (-1 to +1)", f"{m['sent_baseline']:+.2f}" if m["sent_baseline"] is not None else "—",
                      f"{m['sent_campaign']:+.2f}" if m["sent_campaign"] is not None else "—", ""]]
                    + [[f"{platform_name(fo['platform'])} followers", num(fo["start"]), num(fo["end"]),
                        f"{fo['pct']:+.1f}%"] for fo in f["followers"]],
                    [W - 105 * mm, 35 * mm, 35 * mm, 35 * mm])]
    if f["creators"]:
        rows = [["Creator", "Posts about the brand", "Likes", "Comments", "Views", "No disclosure"]]
        for r in f["creators"]:
            rows.append([esc(r["name"]), num(r["posts"]), num(r["likes"]), num(r["comments"]), num(r["views"]),
                         num(r["undisclosed"])])
        t = f["totals"]
        rows.append(["<b>Total</b>", num(t["posts"]), num(t["likes"]), num(t["comments"]), num(t["views"]),
                     num(t["undisclosed"])])
        story += [Paragraph("Creators", H2), table(rows, [W - 125 * mm, 30 * mm, 22 * mm, 25 * mm, 22 * mm, 26 * mm])]
    if f["posts"]:
        rows = [["Date", "Creator", "Post", "Likes", "Comments", "Disclosed"]]
        for p in f["posts"][:12]:
            title = esc((p["title"] or "Untitled")[:80])
            if p.get("url"):
                title = f'<link href="{esc(p["url"])}" color="#2457d6">{title}</link>'
            rows.append([esc((p["published_at"] or "")[:10]), esc(p["creator"]), title, num(p["likes"]),
                         num(p["comments"]), "yes" if p["disclosed"] else "no"])
        story += [Paragraph("Top creator posts", H2),
                  table(rows, [22 * mm, 30 * mm, W - 116 * mm, 20 * mm, 22 * mm, 22 * mm])]
    if f["keyword_pickup"]:
        rows = [["Date", "Publisher", "Story", "Tag"]]
        for k in f["keyword_pickup"][:12]:
            title = esc(k["title"])
            if k.get("url"):
                title = f'<link href="{esc(k["url"])}" color="#2457d6">{title}</link>'
            rows.append([esc((k["published_at"] or "")[:10]), esc(k["publisher"] or ""), title, esc(k["matched"])])
        story += [Paragraph("Campaign tags in the news", H2), table(rows, [22 * mm, 30 * mm, W - 82 * mm, 30 * mm])]
    story.append(Paragraph("What's missing", H2))
    story += lines_to_flow([f"- {x}" for x in f["missing"]]) or [Paragraph("Nothing flagged.", BODY)]
    story.append(Paragraph("Method: campaign window compared with the same number of days immediately before it. "
                           "Creator posts count when the caption or description mentions the brand, its handle or the "
                           "campaign tags. Disclosure = #ad, #sponsored, 'paid partnership' or similar wording found. "
                           "All figures come from data stored with its source and date.", SMALL))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
