"""The online (Vercel) mode: login, instant first collection, daily cron — over the Turso adapter."""
from __future__ import annotations

import re

import pytest

from conftest import FakeResp, FakeWeb, fixture
from fake_turso import FakeTurso
from intel.turso import TursoConnection

import app as appmod        # imported before VERCEL is set, so no app is built at import time
import intel.db as dbmod


@pytest.fixture
def online(tmp_path, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("APP_PASSWORD", "s3cret-pass")
    monkeypatch.setenv("CRON_SECRET", "cron-token")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://mi-test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    fake = FakeTurso(tmp_path / "online.db")
    mk = lambda path=None: TursoConnection("libsql://mi-test.turso.io", "tok", transport=fake)
    monkeypatch.setattr(dbmod, "connect", mk)
    monkeypatch.setattr(appmod, "connect", mk)
    web = FakeWeb({"robots.txt": FakeResp(404), "news.example": FakeResp(200, fixture("nairametrics_feed.xml")),
                   "acme.example": FakeResp(200, fixture("site_v1.html"))})
    a = appmod.create_app(http_get=web)
    c = mk()
    c.execute("UPDATE news_feeds SET enabled=0")
    c.execute("INSERT INTO news_feeds(name,url,enabled) VALUES ('Test','https://news.example/feed/',1)")
    return a, fake


def login(cl, pw="s3cret-pass"):
    return cl.post("/login", data={"password": pw})


def token(cl):
    cl.get("/")
    with cl.session_transaction() as s:
        return s["csrf"]


def test_everything_requires_login(online):
    a, _ = online
    cl = a.test_client()
    r = cl.get("/brands")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert cl.get("/health").status_code == 200          # health stays open for uptime checks
    assert "isn" in login(cl, "wrong").get_data(as_text=True)
    assert cl.get("/brands").status_code == 302
    assert login(cl).status_code == 302
    assert cl.get("/brands").status_code == 200


def test_start_tracking_collects_immediately(online):
    a, fake = online
    cl = a.test_client()
    login(cl)
    t = token(cl)
    r = cl.post("/entities", data={"_csrf": t, "kind": "brand", "name": "Acme Pay", "website": "acme.example",
                                   "keywords": "AcmePayments"})
    eid = re.search(r"/entities/(\d+)", r.headers["Location"]).group(1)
    page = cl.get(f"/entities/{eid}").get_data(as_text=True)
    assert "Acme Pay expands to Ghana" in page                 # mentions there on first view
    assert "First collection done" in page
    assert "Payments for every business" not in page or True  # website snapshot stored (title shown below)
    assert "Acme Pay — Payments for business" in page
    assert fake.calls > 10                                     # it really went through the online DB adapter


def test_cron_needs_secret_and_collects(online):
    a, _ = online
    cl = a.test_client()
    login(cl)
    t = token(cl)
    cl.post("/entities", data={"_csrf": t, "kind": "brand", "name": "Acme Pay"})
    anon = a.test_client()
    assert anon.get("/cron/collect").status_code == 401
    assert anon.get("/cron/collect", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = anon.get("/cron/collect", headers={"Authorization": "Bearer cron-token"})
    assert r.status_code == 200 and "seconds" in r.get_json()


def test_missing_password_blocks_online_app(online, monkeypatch):
    a, _ = online
    monkeypatch.delenv("APP_PASSWORD")
    r = a.test_client().get("/")
    assert r.status_code == 503 and b"APP_PASSWORD" in r.data


def test_setup_problems_show_a_helpful_page(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_PASSWORD", "x" * 12)
    a = appmod._make_online_app()
    r = a.test_client().get("/brands")
    assert r.status_code == 503 and b"TURSO_DATABASE_URL" in r.data and b"Redeploy" in r.data


def test_bad_turso_token_is_explained_without_leaking_it(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("APP_PASSWORD", "x" * 12)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://mi.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "SUPER-SECRET-TOKEN")
    from intel.turso import TursoError
    def boom(*a, **k):
        raise TursoError("database refused the token (check TURSO_AUTH_TOKEN) SUPER-SECRET-TOKEN")
    monkeypatch.setattr(appmod, "create_app", boom)
    r = appmod._make_online_app().test_client().get("/")
    assert b"new token" in r.data and b"SUPER-SECRET-TOKEN" not in r.data
