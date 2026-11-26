"""Server-drawn SVG charts. No JavaScript libraries, works offline."""
from __future__ import annotations

import html
from datetime import datetime

from markupsafe import Markup

PALETTE = ["#2457d6", "#e07b39", "#2a9d8f", "#9b5de5", "#d64550", "#6c757d"]


def _fmt(v: float) -> str:
    if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if abs(v) >= 1_000: return f"{v/1_000:.1f}k"
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.2f}"


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def line_chart(series: dict[str, list[dict]], height: int = 220, width: int = 640, empty: str = "No data yet") -> Markup:
    """series: {label: [{'observed_at':..., 'value':...}, ...]}"""
    pts = [p for s in series.values() for p in s]
    if len(pts) < 2:
        return Markup(f'<div class="chart-empty">{html.escape(empty)}</div>')
    pad_l, pad_r, pad_t, pad_b = 52, 16, 14, 28
    xs = [_ts(p["observed_at"]) for p in pts]
    ys = [p["value"] for p in pts]
    x0, x1 = min(xs), max(xs) or 1
    y0, y1 = min(ys), max(ys)
    if y0 == y1:
        y0, y1 = y0 * 0.95, (y1 * 1.05) or 1
    span = y1 - y0
    y0 -= span * 0.08; y1 += span * 0.08
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    X = lambda t: pad_l + (t - x0) / ((x1 - x0) or 1) * W
    Y = lambda v: pad_t + H - (v - y0) / ((y1 - y0) or 1) * H
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="line chart">']
    for i in range(5):
        v = y0 + (y1 - y0) * i / 4
        y = Y(v)
        out.append(f'<line x1="{pad_l}" x2="{width-pad_r}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" class="tick" text-anchor="end">{_fmt(v)}</text>')
    for frac in (0, 0.5, 1):
        t = x0 + (x1 - x0) * frac
        lbl = datetime.fromtimestamp(t).strftime("%d %b")
        anchor = "start" if frac == 0 else "end" if frac == 1 else "middle"
        out.append(f'<text x="{X(t):.1f}" y="{height-8}" class="tick" text-anchor="{anchor}">{lbl}</text>')
    for i, (label, s) in enumerate(series.items()):
        if not s:
            continue
        c = PALETTE[i % len(PALETTE)]
        d = " ".join(f"{'M' if j == 0 else 'L'}{X(_ts(p['observed_at'])):.1f},{Y(p['value']):.1f}" for j, p in enumerate(s))
        out.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2"/>')
        last = s[-1]
        out.append(f'<circle cx="{X(_ts(last["observed_at"])):.1f}" cy="{Y(last["value"]):.1f}" r="3.5" fill="{c}">'
                   f'<title>{html.escape(label)}: {_fmt(last["value"])} on {last["observed_at"][:10]}</title></circle>')
    out.append("</svg>")
    if len(series) > 1:
        legend = "".join(f'<span><i style="background:{PALETTE[i % len(PALETTE)]}"></i>{html.escape(k)}</span>'
                         for i, k in enumerate(series))
        out.append(f'<div class="legend">{legend}</div>')
    return Markup("".join(out))


def bar_chart(days: dict[str, int], height: int = 140, width: int = 640) -> Markup:
    if not days or not any(days.values()):
        return Markup('<div class="chart-empty">No mentions in this period</div>')
    keys = list(days)
    mx = max(days.values()) or 1
    pad_l, pad_b, pad_t = 30, 22, 8
    W, H = width - pad_l - 8, height - pad_b - pad_t
    bw = W / len(keys)
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="mentions per day">',
           f'<text x="{pad_l-6}" y="{pad_t+8}" class="tick" text-anchor="end">{mx}</text>',
           f'<line x1="{pad_l}" x2="{width-8}" y1="{pad_t+H}" y2="{pad_t+H}" class="grid"/>']
    for i, k in enumerate(keys):
        v = days[k]
        h = v / mx * H
        out.append(f'<rect x="{pad_l+i*bw+1:.1f}" y="{pad_t+H-h:.1f}" width="{max(bw-2,1):.1f}" height="{h:.1f}" '
                   f'rx="1.5" class="bar"><title>{k}: {v}</title></rect>')
    for i in (0, len(keys) // 2, len(keys) - 1):
        lbl = datetime.fromisoformat(keys[i]).strftime("%d %b")
        out.append(f'<text x="{pad_l+i*bw+bw/2:.1f}" y="{height-6}" class="tick" text-anchor="middle">{lbl}</text>')
    out.append("</svg>")
    return Markup("".join(out))


def split_bar(pos: int, neu: int, neg: int) -> Markup:
    total = pos + neu + neg
    if not total:
        return Markup('<div class="chart-empty small">No scored items</div>')
    seg = lambda n, cls, lab: (f'<span class="{cls}" style="width:{n/total*100:.1f}%" title="{lab}: {n}"></span>'
                               if n else "")
    return Markup(f'<div class="splitbar">{seg(pos,"pos","Positive")}{seg(neu,"neu","Neutral")}{seg(neg,"neg","Negative")}</div>')
