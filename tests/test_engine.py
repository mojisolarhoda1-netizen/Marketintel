"""Providers, pipeline, analysis and reports — all offline on fixtures."""
from __future__ import annotations

import json

import pytest
import requests

from conftest import FakeResp, FakeWeb, fixture
from intel import repo
from intel.analysis.sentiment import score
from intel.analysis.trends import growth, zscore_spike
from intel.db import migrate
from intel.pipeline import Pipeline
from intel.providers import build_providers
from intel.providers.rss_news import matches, parse_feed
from intel.pdf import build_pdf
from intel.render import create_report, md_to_html
from intel.reports import build_facts


def only_feed(conn, url="https://news.example/feed/"):
    conn.execute("UPDATE news_feeds SET enabled=0")
    conn.execute("INSERT INTO news_feeds(name,url,enabled) VALUES ('Test News', ?, 1)", (url,))


def pipe(conn, web):
    return Pipeline(conn, build_providers(web), sleep=lambda s: None)


# ---------------- database ----------------

def test_migrations_are_idempotent(conn):
    assert migrate(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM data_sources").fetchone()[0] >= 8


# ---------------- RSS ----------------

def test_parse_rss_and_atom():
    rss = parse_feed(fixture("nairametrics_feed.xml"))
    assert len(rss) == 4 and rss[0]["published"] == "2026-11-20T20:16:39Z"
    assert "love the new app" in rss[0]["summary"] and "<p>" not in rss[0]["summary"]
    atom = parse_feed(fixture("atom_feed.xml"))
    assert atom[0]["url"] == "https://atom.example/a1"


def test_keyword_matching_is_whole_word():
    assert matches("Acme Pay expands", ["Acme Pay"])
    assert not matches("Acmeville council", ["Acme"])


def test_rss_collects_dedupes_and_scores(conn):
    only_feed(conn)
    eid = repo.create_entity(conn, "brand", "Acme Pay", "NG", keywords="AcmePayments")
    web = FakeWeb({"news.example/feed": FakeResp(200, fixture("nairametrics_feed.xml"))})
    r = pipe(conn, web).run_entity(eid, only=["rss_news"])[0]
    assert r["status"] == "ok" and r["new"] == 3          # 2 by name + 1 by keyword, not "Acmeville"
    labels = dict(conn.execute("SELECT external_id, sentiment_label FROM content_items").fetchall())
    assert labels["https://news.example/?p=1001"] == "positive"
    assert labels["https://news.example/?p=1002"] == "negative"
    r2 = pipe(conn, web).run_entity(eid, only=["rss_news"])[0]
    assert r2["new"] == 0                                    # de-duplicated
    assert conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0] == 3


def test_one_broken_feed_does_not_stop_others(conn):
    only_feed(conn)
    conn.execute("INSERT INTO news_feeds(name,url,enabled) VALUES ('Broken', 'https://broken.example/feed', 1)")
    eid = repo.create_entity(conn, "brand", "Acme Pay")
    web = FakeWeb({"news.example/feed": FakeResp(200, fixture("nairametrics_feed.xml")),
                   "broken.example": FakeResp(500, b"")})
    r = pipe(conn, web).run_entity(eid, only=["rss_news"])[0]
    assert r["status"] == "partial" and r["new"] == 2
    assert conn.execute("SELECT last_error FROM news_feeds WHERE name='Broken'").fetchone()[0]


def test_retries_transient_errors_then_succeeds(conn):
    only_feed(conn)
    eid = repo.create_entity(conn, "brand", "Acme Pay")
    calls = {"n": 0}

    def flaky(url, params):
        calls["n"] += 1
        return requests.ConnectionError("down") if calls["n"] == 1 else FakeResp(200, fixture("nairametrics_feed.xml"))
    r = pipe(conn, FakeWeb({"news.example": flaky})).run_entity(eid, only=["rss_news"])[0]
    assert r["status"] == "ok"
    assert calls["n"] == 2          # the failed fetch was retried and the second one succeeded


# ---------------- YouTube ----------------

def yt_web():
    return FakeWeb({
        "youtube/v3/channels": FakeResp(200, fixture("yt_channel.json")),
        "youtube/v3/playlistItems": FakeResp(200, fixture("yt_playlist.json")),
        "youtube/v3/videos": FakeResp(200, fixture("yt_videos.json")),
        "youtube/v3/search": FakeResp(200, fixture("yt_search.json")),
    })


def test_youtube_unavailable_without_key(conn):
    eid = repo.create_entity(conn, "influencer", "Test Creator")
    repo.add_account(conn, eid, "youtube", "@testcreator")
    r = pipe(conn, yt_web()).run_entity(eid, only=["youtube"])[0]
    assert r["status"] == "unavailable" and "YOUTUBE_API_KEY" in r["message"]


def test_youtube_collects_metrics_and_videos(conn, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    eid = repo.create_entity(conn, "influencer", "Test Creator")
    repo.add_account(conn, eid, "youtube", "testcreator")
    web = yt_web()
    r = pipe(conn, web).run_entity(eid, only=["youtube"])[0]
    assert r["status"] == "ok"
    m = dict(conn.execute("SELECT metric, value FROM metric_snapshots WHERE entity_id=?", (eid,)).fetchall())
    assert m["followers"] == 120000 and m["posts"] == 240
    assert m["avg_likes"] == 2000 and m["avg_comments"] == 200       # vid2 hides comments -> excluded
    assert round(m["engagement_rate"], 4) == round((2000 + 200) / 120000 * 100, 4)
    assert conn.execute("SELECT external_id FROM social_accounts").fetchone()[0] == "UCabcdefghijklmnopqrstuv"
    assert web.calls[0][1]["forHandle"] == "@testcreator"
    assert conn.execute("SELECT units FROM api_usage WHERE source_id='youtube'").fetchone()[0] == 3


def test_youtube_quota_guard_stops_calls(conn, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setenv("YOUTUBE_DAILY_UNIT_BUDGET", "1")
    eid = repo.create_entity(conn, "influencer", "Q")
    repo.add_account(conn, eid, "youtube", "@q")
    web = yt_web()
    pipe(conn, web).run_entity(eid, only=["youtube"])   # first call (1 unit) fits, second does not
    r = conn.execute("SELECT status, message FROM collection_runs ORDER BY id DESC").fetchone()
    assert r["status"] == "skipped" and "quota" in r["message"].lower()
    assert len(web.calls) == 1


def test_youtube_discovery_search(conn, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    from intel.pipeline import make_quota_guard
    res = build_providers(yt_web())["youtube"].search_channels("lagos food", "NG", make_quota_guard(conn))
    assert res[0]["subscribers"] == 120000
    usage = dict(conn.execute("SELECT source_id, units FROM api_usage").fetchall())
    assert usage == {"youtube_search": 1, "youtube": 1}    # search has its own 100/day bucket


# ---------------- Instagram ----------------

def test_instagram_business_discovery(conn, monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "999"); monkeypatch.setenv("IG_ACCESS_TOKEN", "tok")
    eid = repo.create_entity(conn, "influencer", "Ada")
    repo.add_account(conn, eid, "instagram", "https://instagram.com/adastyles/")
    web = FakeWeb({"graph.facebook.com": FakeResp(200, fixture("ig_business_discovery.json"))})
    r = pipe(conn, web).run_entity(eid, only=["instagram"])[0]
    assert r["status"] == "ok"
    assert "business_discovery.username(adastyles)" in web.calls[0][1]["fields"]
    m = dict(conn.execute("SELECT metric, value FROM metric_snapshots").fetchall())
    assert m["followers"] == 48000 and m["avg_likes"] == 1500   # post with hidden likes excluded
    assert conn.execute("SELECT COUNT(*) FROM content_items WHERE kind='post'").fetchone()[0] == 2


def test_instagram_expired_token_is_reported(conn, monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "1"); monkeypatch.setenv("IG_ACCESS_TOKEN", "bad")
    eid = repo.create_entity(conn, "influencer", "Ada")
    repo.add_account(conn, eid, "instagram", "adastyles")
    web = FakeWeb({"graph.facebook.com": FakeResp(400, json.dumps({"error": {"code": 190, "message": "expired"}}))})
    r = pipe(conn, web).run_entity(eid, only=["instagram"])[0]
    assert r["status"] == "unavailable" and "token" in r["message"].lower()


# ---------------- website ----------------

def test_website_respects_robots(conn):
    eid = repo.create_entity(conn, "brand", "Acme", website="acme.example")
    web = FakeWeb({"robots.txt": FakeResp(200, b"User-agent: *\nDisallow: /"),
                   "acme.example": FakeResp(200, fixture("site_v1.html"))})
    r = pipe(conn, web).run_entity(eid, only=["website"])[0]
    assert r["status"] == "unavailable" and "robots" in r["message"]
    assert not any(u == "https://acme.example" for u, _ in web.calls)


def test_website_change_detection_raises_alert(conn):
    eid = repo.create_entity(conn, "brand", "Acme", website="https://acme.example")
    page = {"v": "site_v1.html"}
    web = FakeWeb({"robots.txt": FakeResp(404), "acme.example": lambda u, p: FakeResp(200, fixture(page["v"]))})
    p = pipe(conn, web)
    p.providers["website"].throttle = lambda *a: None
    assert p.run_entity(eid, only=["website"])[0]["new"] == 1
    assert p.run_entity(eid, only=["website"])[0]["new"] == 0            # unchanged -> nothing stored
    page["v"] = "site_v2.html"
    assert p.run_entity(eid, only=["website"])[0]["new"] == 1
    a = conn.execute("SELECT kind, data FROM alerts WHERE kind='page_change'").fetchone()
    assert a and json.loads(a["data"])["prices_added"] == ["₦12,000"]


# ---------------- postponed sources ----------------

def test_tiktok_and_x_say_why_they_are_missing(conn):
    eid = repo.create_entity(conn, "influencer", "T")
    repo.add_account(conn, eid, "tiktok", "@t"); repo.add_account(conn, eid, "x", "@t")
    res = {r["source"]: r for r in pipe(conn, FakeWeb()).run_entity(eid, only=["tiktok", "x"])}
    assert res["tiktok"]["status"] == "unavailable" and "Research API" in res["tiktok"]["message"]
    assert res["x"]["status"] == "unavailable"


# ---------------- analysis ----------------

@pytest.mark.parametrize("text,label", [
    ("Customers love the new app, very reliable", "positive"),
    ("This bank na scam, too much wahala", "negative"),
    ("Network no good at all", "negative"),
    ("Company announces quarterly results", "neutral"),
    ("Their delivery e sweet die", "positive"),
])
def test_sentiment(text, label):
    assert score(text)[1] == label


def test_growth_and_spike_maths():
    s = [{"observed_at": "2026-11-01T00:00:00Z", "value": 100}, {"observed_at": "2026-11-10T00:00:00Z", "value": 125}]
    assert growth(s)["pct"] == 25.0
    assert growth(s[:1]) is None
    assert zscore_spike([1, 0, 2, 1, 1, 0, 1, 2, 9])["last"] == 9
    assert zscore_spike([1, 2, 1, 2, 1, 2, 1, 2, 2]) is None


def test_manual_entry_validation(conn):
    eid = repo.create_entity(conn, "influencer", "M")
    repo.record_manual_metric(conn, eid, "tiktok", "followers", "15,400", "2026-11-01")
    assert conn.execute("SELECT value, source_id FROM metric_snapshots").fetchone()[:] == (15400.0, "manual")
    with pytest.raises(repo.ValidationError):
        repo.record_manual_metric(conn, eid, "tiktok", "followers", "-5", "2026-11-01")
    with pytest.raises(repo.ValidationError):
        repo.record_manual_metric(conn, eid, "tiktok", "followers", "10", "2999-01-01")
    with pytest.raises(repo.ValidationError):
        repo.create_entity(conn, "influencer", "m")   # duplicate, case-insensitive


def test_csv_import(conn):
    text = "name,kind,platform,handle,metric,value,date\nKemi,influencer,tiktok,kemi,followers,22000,2026-11-01\n" \
           "Kemi,influencer,tiktok,kemi,followers,abc,2026-11-02\n"
    res = repo.import_metrics_csv(conn, text)
    assert res["imported"] == 1 and len(res["errors"]) == 1


def test_authenticity_signals_need_enough_data(conn):
    from intel.analysis import authenticity
    eid = repo.create_entity(conn, "influencer", "A")
    sig = authenticity.signals(conn, eid)
    assert sig[0]["level"] == "unknown"


# ---------------- reports ----------------

def test_report_uses_only_stored_facts_and_lists_missing(conn):
    only_feed(conn)
    eid = repo.create_entity(conn, "brand", "Acme Pay")
    pipe(conn, FakeWeb({"news.example": FakeResp(200, fixture("nairametrics_feed.xml"))})).run_entity(eid)
    facts = build_facts(conn, eid, 3650)
    assert facts["mentions"]["count"] == 2
    assert any("No social accounts" in m for m in facts["missing"])
    assert any("No competitors" in m for m in facts["missing"])
    rid = create_report(conn, eid, 3650, "builtin")
    r = dict(conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())
    assert "Reporting period" in r["narrative"] and "What's missing" in r["narrative"]
    assert "No social accounts" in r["narrative"]
    assert build_pdf(r, json.loads(r["facts"]), "Dexia Global")[:4] == b"%PDF"


def test_paid_ai_refuses_without_approval(conn):
    from intel.ai.engines import claude_api_narrative
    with pytest.raises(PermissionError):
        claude_api_narrative({})


def test_markdown_renderer_escapes_html():
    out = md_to_html("## Title\n- <script>alert(1)</script> **bold**\n[x](javascript:alert(1))")
    assert "<script>" not in out and "&lt;script&gt;" in out and "<strong>bold</strong>" in out
    assert 'href="javascript' not in out


def test_migrate_survives_two_instances_starting_at_once(tmp_path):
    from intel.db import connect, migrate
    p = str(tmp_path / "race.db")
    a, b = connect(p), connect(p)
    a.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT)")
    a.commit()
    migrate(a)
    b.execute("DELETE FROM schema_migrations WHERE version LIKE '003%'")  # b thinks 003 still needs doing...
    b.commit()
    a.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES ('003_phase3')"); a.commit()  # ...a finishes first
    migrate(b)   # must not crash
