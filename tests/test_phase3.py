"""Watchlists, topics, partnerships, campaigns, opportunities, schedules, ask-your-data."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import FakeResp, FakeWeb, fixture
from intel import ask, campaigns, opportunities, partnerships, repo, sample, schedules, topics, watchlists
from intel.exports import campaign_csv
from intel.pdf import build_campaign_pdf
from intel.providers import build_providers


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def add_content(conn, eid, kind, title, when, likes=None, comments=None, source="rss_news", label=None, s=None):
    import hashlib
    h = hashlib.sha256(f"{eid}|{title}|{when}".encode()).hexdigest()
    cur = conn.execute("""INSERT INTO content_items(entity_id,source_id,kind,title,body,publisher,published_at,collected_at,
                    likes,comments,content_hash,sentiment_label,sentiment_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (eid, source, kind, title, title, "Test", when, when, likes, comments, h, label, s))
    return cur.lastrowid


# ---------------- watchlists ----------------

def test_watchlist_lifecycle(conn):
    wid = watchlists.create(conn, "Client: Acme")
    b = repo.create_entity(conn, "brand", "Acme Pay")
    i = repo.create_entity(conn, "influencer", "Ada")
    watchlists.add(conn, wid, b); watchlists.add(conn, wid, i); watchlists.add(conn, wid, i)  # idempotent
    assert [m["name"] for m in watchlists.members(conn, wid)] == ["Acme Pay", "Ada"]
    assert watchlists.all_lists(conn)[0]["brands"] == 1
    with pytest.raises(repo.ValidationError):
        watchlists.create(conn, "client: acme")
    repo.delete_entity(conn, i)
    assert len(watchlists.members(conn, wid)) == 1          # deleting an entity cleans the list


# ---------------- topics ----------------

def test_topic_collect_dedupe_and_comentions(conn):
    conn.execute("UPDATE news_feeds SET enabled=0")
    conn.execute("INSERT INTO news_feeds(name,url,enabled) VALUES ('T','https://news.example/feed/',1)")
    repo.create_entity(conn, "brand", "Acme Pay")
    tid = topics.create(conn, "Payments trouble", "outage, failed transfers")
    prov = build_providers(FakeWeb({"news.example": FakeResp(200, fixture("nairametrics_feed.xml"))}))["rss_news"]
    r = topics.collect(conn, prov)
    assert r["new"] == 1
    assert topics.collect(conn, build_providers(
        FakeWeb({"news.example": FakeResp(200, fixture("nairametrics_feed.xml"))}))["rss_news"])["new"] == 0
    s = topics.summary(conn, tid, "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z")
    assert s["count"] == 1 and s["negative"] == 1
    assert s["co_mentions"][0]["name"] == "Acme Pay"


def test_topic_hashtag_and_whole_word():
    assert topics._match("Big #DettyDecember plans", ["#dettydecember"]) == "#dettydecember"
    assert topics._match("Fintechs rally", ["fintech"]) is None


# ---------------- partnerships ----------------

def test_partnership_detection_and_disclosure(conn):
    b = repo.create_entity(conn, "brand", "Acme Pay")
    repo.add_account(conn, b, "instagram", "acmepay")
    i = repo.create_entity(conn, "influencer", "Ada")
    now = iso(datetime.now(timezone.utc))
    add_content(conn, i, "post", "Loving my new card from @acmepay #ad", now, 100, 5, "instagram")
    add_content(conn, i, "post", "Acme Pay saved my weekend", now, 50, 2, "instagram")
    add_content(conn, i, "post", "Acmeville trip", now, 10, 1, "instagram")
    assert partnerships.detect(conn) == 2
    rows = {p["matched"]: p["disclosed"] for p in partnerships.for_brand(conn, b)}
    assert rows == {"@acmepay": 1, "Acme Pay": 0}
    assert partnerships.detect(conn) == 0                   # no duplicates


def test_partnerships_never_link_sample_and_real(conn):
    sample.seed(conn, days=10)
    real = repo.create_entity(conn, "influencer", "Real Creator")
    add_content(conn, real, "post", "Snacking on Zuma Foods today", iso(datetime.now(timezone.utc)), 1, 1, "instagram")
    partnerships.detect(conn)
    assert partnerships.for_influencer(conn, real) == []


# ---------------- campaigns ----------------

def test_campaign_facts_compare_like_with_like(conn):
    today = date(2026, 11, 26)
    b = repo.create_entity(conn, "brand", "Acme Pay")
    i = repo.create_entity(conn, "influencer", "Ada")
    cid = campaigns.create(conn, b, "Launch", "2026-11-17", "2026-11-26", "#AcmeLaunch", influencer_ids=[i])
    # baseline window 2026-11-07..16: 2 mentions; campaign window: 4 mentions
    for d in ("2026-11-08", "2026-11-12"):
        add_content(conn, b, "mention", f"Acme Pay story {d}", f"{d}T10:00:00Z", s=0.0, label="neutral")
    for d in ("2026-11-18", "2026-11-19", "2026-11-20", "2026-11-25"):
        add_content(conn, b, "mention", f"Acme Pay #AcmeLaunch story {d}", f"{d}T10:00:00Z", s=0.5, label="positive")
    repo.record_manual_metric(conn, b, "instagram", "followers", 1000, "2026-11-15")
    repo.record_manual_metric(conn, b, "instagram", "followers", 1100, "2026-11-25")
    add_content(conn, i, "post", "Try Acme Pay! #AcmeLaunch", "2026-11-19T12:00:00Z", 300, 20, "instagram")
    add_content(conn, i, "post", "Unrelated outfit", "2026-11-20T12:00:00Z", 999, 99, "instagram")
    f = campaigns.facts(conn, cid, today=today)
    assert f["baseline"] == {"start": "2026-11-07", "end": "2026-11-16", "days": 10}
    assert f["mentions"]["campaign"] == 4 and f["mentions"]["baseline"] == 2 and f["mentions"]["pct"] == 100.0
    assert f["followers"][0]["pct"] == 10.0
    assert f["totals"] == {"posts": 1, "likes": 300, "comments": 20, "views": 0, "undisclosed": 1}
    assert f["keyword_pickup_count"] == 4
    lines = campaigns.summary_lines(f)
    assert any("4 during the campaign vs 2" in l for l in lines)
    assert build_campaign_pdf(f, lines, "Dexia Global")[:4] == b"%PDF"
    assert "press mentions,2,4,100.0" in campaign_csv(f)


def test_campaign_validation(conn):
    b = repo.create_entity(conn, "brand", "B")
    with pytest.raises(repo.ValidationError):
        campaigns.create(conn, b, "X", "2026-11-20", "2026-11-01")
    with pytest.raises(repo.ValidationError):
        campaigns.create(conn, b, "X", "not-a-date", "2026-11-01")


# ---------------- opportunities ----------------

def test_opportunities_rules(conn):
    now = datetime.now(timezone.utc)
    b = repo.create_entity(conn, "brand", "Acme Pay")
    rival = repo.create_entity(conn, "brand", "Bolt Bank")
    repo.link_competitor(conn, b, rival)
    for k in range(3):
        add_content(conn, rival, "mention", f"Bolt Bank outage {k}", iso(now - timedelta(days=1, hours=k)),
                    s=-0.5, label="negative")
    c = repo.create_entity(conn, "influencer", "Rising")
    repo.record_manual_metric(conn, c, "tiktok", "followers", 10000, (now - timedelta(days=20)).strftime("%Y-%m-%d"))
    repo.record_manual_metric(conn, c, "tiktok", "followers", 12500, now.strftime("%Y-%m-%d"))
    kinds = {o["kind"] for o in opportunities.find(conn)}
    assert {"competitor_weakness", "rising_creator"} <= kinds


def test_opportunities_hide_sample_by_default(conn):
    sample.seed(conn, days=20)
    assert opportunities.find(conn) == []
    assert opportunities.find(conn, include_sample=True)


# ---------------- scheduled reports ----------------

def test_schedules_write_files_once_per_period(conn, tmp_path):
    e = repo.create_entity(conn, "brand", "Acme Pay")
    wid = watchlists.create(conn, "Client")
    watchlists.add(conn, wid, e)
    schedules.create(conn, "weekly", watchlist_id=wid)
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    files = schedules.run_due(conn, now=now, out_dir=tmp_path)
    assert len(files) == 1 and files[0].endswith("acme-pay-weekly-2026-11-26.pdf")
    assert (tmp_path / "client" / "acme-pay-weekly-2026-11-26.csv").exists()
    assert schedules.run_due(conn, now=now + timedelta(days=3), out_dir=tmp_path) == []
    assert len(schedules.run_due(conn, now=datetime.now(timezone.utc) + timedelta(days=8), out_dir=tmp_path)) == 1


def test_schedules_refuse_sample(conn):
    sample.seed(conn, days=5)
    with pytest.raises(repo.ValidationError):
        schedules.create(conn, "weekly", entity_id=1)


# ---------------- ask your data ----------------

def test_ask_pack_contains_rules_question_and_facts(conn):
    e = repo.create_entity(conn, "brand", "Acme Pay")
    facts = ask.gather(conn, [e])
    p = ask.pack("Who leads?", facts)
    assert "QUESTION: Who leads?" in p and '"name": "Acme Pay"' in p and "ONLY the JSON facts" in p
    with pytest.raises(RuntimeError):
        ask.answer_with_ollama("x", facts)   # Ollama not running in tests -> clear error


# ---------------- web pages ----------------

def test_new_pages_render(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "t")
    from app import create_app
    from intel.db import connect
    app = create_app(str(tmp_path / "w.db"))
    app.config.update(TESTING_NO_CSRF=True, SYNC_JOBS=True)
    c = connect(app.config["DB_FILE"]); sample.seed(c, days=15); c.close()
    cl = app.test_client()
    for path in ["/watchlists", "/watchlists/1", "/topics", "/campaigns", "/campaigns/1", "/insights", "/ask",
                 "/influencers/compare?ids=4&ids=5", "/entities/1", "/entities/5", "/reports"]:
        r = cl.get(path)
        assert r.status_code == 200, path
    assert cl.get("/campaigns/1.pdf").data[:4] == b"%PDF"
    assert b"press mentions" in cl.get("/campaigns/1.csv").data
    r = cl.post("/topics", data={"name": "Snacks", "keywords": "snack, chips"}, follow_redirects=True)
    assert "Snacks" in r.get_data(as_text=True)
    r = cl.post("/ask", data={"question": "Which brand leads?", "scope": "w:1", "engine": "claude_handoff"})
    assert "QUESTION: Which brand leads?" in r.get_data(as_text=True)
    page = cl.get("/influencers/compare?ids=4&ids=5").get_data(as_text=True)
    assert "SAMPLE Ada Styles" in page and "SAMPLE Tunde Eats" in page
