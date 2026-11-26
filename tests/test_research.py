"""Research page on recorded responses (no network)."""
import json
from datetime import datetime, timedelta, timezone


class R:
    def __init__(self, body, status=200):
        self.content = body if isinstance(body, bytes) else body.encode()
        self.status_code = status
        self.headers = {}


def _rss(items):
    now = datetime.now(timezone.utc)
    xs = "".join(f"<item><title>{t} - Punch</title><link>https://ex.com/{i}</link><guid>{i}</guid>"
                 f"<pubDate>{(now - timedelta(days=d)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
                 f"<description>{t}</description></item>" for i, (t, d) in enumerate(items))
    return f"<rss><channel>{xs}</channel></rss>"


def fake_get(url, params=None, timeout=None, headers=None):
    if "news.google.com" in url:
        if "Rival" in url:
            return R(_rss([("Rival wins award for great delivery", 1), ("Rival delivery expansion", 2),
                           ("Rival delivery app launch", 3), ("Rival scam complaints grow", 4)]))
        return R(_rss([("Acme launches new app", 1), ("Acme customers complain of failed transfers", 2)]))
    if "suggestqueries" in url:
        return R(json.dumps([params["q"], [f"{params['q'].strip()} customer care number", "acme app login"]]))
    if "w/api.php" in url:
        return R(json.dumps(["Acme", ["Acme Corp"], [""], [""]]))
    if "pageviews" in url:
        return R(json.dumps({"items": [{"views": 10}] * 30 + [{"views": 20}] * 30}))
    return R("", 404)


def test_research_finds_coverage_and_gives_advice():
    from intel.research import run
    r = run("Acme", ["Rival"], "NG", fake_get)
    m = r["main"]
    assert m["n30"] == 2 and m["neg"] == 1 and m["publishers"] == ["Punch"]
    assert m["wiki"]["change"] == 100
    assert r["competitors"][0]["n30"] == 4 and "delivery" in r["competitors"][0]["themes"]
    titles = [t["title"] for t in r["advice"]]
    assert any("louder" in t for t in titles) and any("searching" in t for t in titles)
    assert r["suggestions"] and r["ideas"]


def test_research_survives_every_source_failing():
    from intel.research import run
    r = run("Nobody", [], "NG", lambda *a, **k: R("", 503))
    assert r["main"]["n"] == 0 and r["main"]["error"] and r["suggestions"] == []


def test_research_page_and_track(tmp_path):
    from app import create_app
    a = create_app(str(tmp_path / "t.db"), http_get=fake_get)
    a.config["TESTING_NO_CSRF"] = True
    a.config["SYNC_JOBS"] = True
    cl = a.test_client()
    html = cl.get("/research?q=Acme&vs=Rival").get_data(as_text=True)
    assert "What to do next" in html and "Acme launches new app" in html and "customer care number" in html
    r = cl.post("/research/track", data={"q": "Acme", "vs": "Rival", "country": "NG"})
    assert r.status_code == 302
    assert "Acme" in cl.get("/competitors").get_data(as_text=True)
