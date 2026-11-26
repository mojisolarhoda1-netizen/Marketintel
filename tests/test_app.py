"""Web app: pages render, forms validate, CSRF protection works."""
from __future__ import annotations

import re

import pytest

from conftest import FakeResp, FakeWeb, fixture


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    from app import create_app
    web = FakeWeb({"robots.txt": FakeResp(404), "news.example": FakeResp(200, fixture("nairametrics_feed.xml")),
                   "acme.example": FakeResp(200, fixture("site_v1.html"))})
    app = create_app(str(tmp_path / "app.db"), http_get=web)
    app.config["SYNC_JOBS"] = True
    with app.app_context():
        from intel.db import connect
        c = connect(app.config["DB_FILE"])
        c.execute("UPDATE news_feeds SET enabled=0")
        c.execute("INSERT INTO news_feeds(name,url) VALUES ('Test','https://news.example/feed/')")
        c.commit(); c.close()
    cl = app.test_client()
    html = cl.get("/").get_data(as_text=True)
    cl.token = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html).group(1)
    return cl


def post(cl, url, data=None, **kw):
    d = dict(data or {}); d["_csrf"] = cl.token
    return cl.post(url, data=d, **kw)


def test_all_pages_render_empty(client):
    for p in ["/", "/brands", "/influencers", "/competitors", "/listening", "/discover", "/reports", "/alerts",
              "/sources", "/health", "/api/entities"]:
        assert client.get(p).status_code == 200, p


def test_csrf_blocks_forged_posts(client):
    assert client.post("/entities", data={"kind": "brand", "name": "X"}).status_code == 400


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Frame-Options"] == "DENY" and "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_full_brand_flow(client):
    r = post(client, "/entities", {"kind": "brand", "name": "Acme Pay", "country": "ng", "website": "acme.example",
                                   "handle_tiktok": "@acmepay"})
    assert r.status_code == 302
    eid = int(re.search(r"/entities/(\d+)", r.headers["Location"]).group(1))
    assert post(client, f"/entities/{eid}/collect").status_code == 302
    page = client.get(f"/entities/{eid}").get_data(as_text=True)
    assert "Acme Pay expands to Ghana" in page                       # real pipeline ran on fixtures
    assert "TikTok" in page and "unavailable" in page                 # postponed source explained
    assert post(client, f"/entities/{eid}/competitors", {"new_name": "Bolt Bank"}).status_code == 302
    assert "Bolt Bank" in client.get("/competitors").get_data(as_text=True)
    assert post(client, f"/entities/{eid}/manual", {"platform": "tiktok", "metric": "followers", "value": "9000",
                                                    "observed_on": "2026-11-01"}).status_code == 302
    r = post(client, "/reports", {"entity_id": eid, "days": 3650, "engine": "builtin"})
    rid = int(r.headers["Location"].rsplit("/", 1)[1])
    body = client.get(f"/reports/{rid}").get_data(as_text=True)
    assert re.search(r"What(&#39;|&#x27;|')s missing", body)
    assert client.get(f"/reports/{rid}.pdf").data[:4] == b"%PDF"
    assert b"followers" in client.get(f"/reports/{rid}.csv").data
    assert "FACTS (JSON)" in client.get(f"/reports/{rid}/handoff").get_data(as_text=True)


def test_validation_messages(client):
    r = post(client, "/entities", {"kind": "brand", "name": ""}, follow_redirects=True)
    assert "Name is required" in r.get_data(as_text=True)
    r = post(client, "/entities", {"kind": "brand", "name": "Bad", "website": "not a site"}, follow_redirects=True)
    assert "doesn" in r.get_data(as_text=True)


def test_unavailable_engine_rejected(client):
    post(client, "/entities", {"kind": "brand", "name": "A"})
    r = post(client, "/reports", {"entity_id": 1, "days": 30, "engine": "claude_api"}, follow_redirects=True)
    assert re.search(r"isn(&#39;|&#x27;|')t available", r.get_data(as_text=True))


def test_csv_import_route(client):
    import io
    data = {"_csrf": client.token, "file": (io.BytesIO(b"name,kind,platform,handle,metric,value,date\n"
                                                       b"Kemi,influencer,tiktok,kemi,followers,22000,2026-11-01\n"),
                                            "m.csv")}
    r = client.post("/import", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "Imported 1 rows" in r.get_data(as_text=True)


def test_404(client):
    assert client.get("/entities/999").status_code == 404
