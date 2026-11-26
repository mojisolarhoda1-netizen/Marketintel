"""Explainable fake-engagement signals for influencers.

Each signal says what was measured, against what, and why it matters.
They are compared only with creators YOU track (no invented industry benchmarks),
and every result is labelled "signal, not proof".
"""
from __future__ import annotations

import statistics

from .trends import latest_metrics, metric_series, parse


def size_band(followers: float) -> str:
    if followers < 10_000: return "nano (<10k)"
    if followers < 100_000: return "micro (10k–100k)"
    if followers < 1_000_000: return "mid (100k–1M)"
    return "macro (1M+)"


def _peer_er(conn, platform: str, band: str, exclude_id: int) -> list[float]:
    vals = []
    for (eid,) in conn.execute("SELECT id FROM entities WHERE kind='influencer' AND id<>?", (exclude_id,)):
        lm = latest_metrics(conn, eid)
        f, er = lm.get((platform, "followers")), lm.get((platform, "engagement_rate"))
        if f and er and size_band(f["value"]) == band:
            vals.append(er["value"])
    return vals


def signals(conn, entity_id: int) -> list[dict]:
    out = []
    lm = latest_metrics(conn, entity_id)
    platforms = sorted({p for (p, m) in lm if m == "followers" and p})
    if not platforms:
        return [{"platform": None, "name": "No follower data yet", "level": "unknown",
                 "detail": "Collect or record follower counts first."}]
    for p in platforms:
        f = lm[(p, "followers")]["value"]
        band = size_band(f)
        er = lm.get((p, "engagement_rate"))
        # 1. engagement rate vs peers of the same size
        if er:
            peers = _peer_er(conn, p, band, entity_id)
            if len(peers) >= 3:
                med = statistics.median(peers)
                ratio = er["value"] / med if med else None
                level = "watch" if ratio is not None and (ratio < 0.3 or ratio > 4) else "normal"
                out.append({"platform": p, "name": "Engagement vs similar-size creators", "level": level,
                            "detail": f"{er['value']:.2f}% vs median {med:.2f}% across {len(peers)} tracked "
                                      f"{band} creators on {p}. Very low can mean bought followers; very high "
                                      f"can mean engagement pods."})
            else:
                out.append({"platform": p, "name": "Engagement vs similar-size creators", "level": "unknown",
                            "detail": f"Engagement {er['value']:.2f}%. Track at least 3 other {band} creators on "
                                      f"{p} to compare."})
        # 2. sudden follower jumps between snapshots
        s = metric_series(conn, entity_id, "followers", p)
        if len(s) >= 3:
            jumps = []
            for a, b in zip(s, s[1:]):
                days = max((parse(b["observed_at"]) - parse(a["observed_at"])).total_seconds() / 86400, 1 / 24)
                if a["value"] > 0:
                    jumps.append(((b["value"] - a["value"]) / a["value"] * 100 / days, b["observed_at"]))
            daily = [j for j, _ in jumps]
            biggest = max(jumps, key=lambda j: j[0])
            med = statistics.median(daily)
            flag = biggest[0] > max(5.0, 5 * abs(med) + 1)
            out.append({"platform": p, "name": "Sudden follower jumps", "level": "watch" if flag else "normal",
                        "detail": f"Largest daily growth {biggest[0]:.1f}% (on {biggest[1][:10]}) vs typical "
                                  f"{med:.2f}%/day over {len(s)} snapshots."})
        else:
            out.append({"platform": p, "name": "Sudden follower jumps", "level": "unknown",
                        "detail": f"{len(s)} snapshot(s) so far; need 3 or more."})
        # 3. comments-to-likes ratio
        al, ac = lm.get((p, "avg_likes")), lm.get((p, "avg_comments"))
        if al and ac and al["value"] > 0:
            r = ac["value"] / al["value"] * 100
            level = "watch" if r < 0.2 or r > 25 else "normal"
            out.append({"platform": p, "name": "Comments per 100 likes", "level": level,
                        "detail": f"{r:.1f} comments per 100 likes on recent posts. Extremes in either direction "
                                  f"deserve a manual look at the comments."})
    return out
