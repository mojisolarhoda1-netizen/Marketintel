"""Persistence across restarts, history, data-origin separation, retention, exports."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeResp, FakeWeb, fixture
from intel import repo, retention, sample
from intel.db import connect, migrate, origin_of
from intel.exports import entity_raw_csv, report_csv
from intel.maintenance import backup, counts, separation_problems
from intel.pdf import build_pdf
from intel.pipeline import Pipeline
from intel.providers import build_providers
from intel.render import create_report


def test_data_survives_restart_and_remigration(tmp_path):
    path = tmp_path / "persist.db"
    c = connect(path); migrate(c)
    eid = repo.create_entity(c, "brand", "Acme Pay", "NG")
    repo.record_manual_metric(c, eid, "instagram", "followers", 1000, "2026-11-01")
    c.commit(); c.close()
    # "restart": new connection, migrations run again, app factory runs again
    c2 = connect(path); assert migrate(c2) == []
    assert repo.get_entity(c2, eid)["name"] == "Acme Pay"
    assert c2.execute("SELECT value FROM metric_snapshots").fetchone()[0] == 1000
    c2.close()
    from app import create_app
    app = create_app(str(path))
    assert "Acme Pay" in app.test_client().get("/brands").get_data(as_text=True)


def test_each_collection_appends_history(conn, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    eid = repo.create_entity(conn, "influencer", "Creator")
    repo.add_account(conn, eid, "youtube", "@c")
    web = FakeWeb({"youtube/v3/channels": FakeResp(200, fixture("yt_channel.json")),
                   "playlistItems": FakeResp(200, fixture("yt_playlist.json")),
                   "youtube/v3/videos": FakeResp(200, fixture("yt_videos.json"))})
    p = Pipeline(conn, build_providers(web), sleep=lambda s: None)
    p.run_entity(eid); p.run_entity(eid)
    n = conn.execute("SELECT COUNT(*) FROM metric_snapshots WHERE metric='followers'").fetchone()[0]
    assert n == 2                                                            # two snapshots, nothing overwritten
    assert conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0] == 2  # videos not duplicated


def test_origin_labels():
    assert origin_of("sample")[0] == "SAMPLE"
    assert origin_of("manual")[0] == "MANUAL"
    assert origin_of("csv_import")[0] == "IMPORTED"
    assert origin_of("rss_news")[0] == "LIVE" and origin_of("youtube")[0] == "LIVE"


def test_csv_import_is_labelled_imported_not_manual(conn):
    repo.import_metrics_csv(conn, "name,kind,platform,handle,metric,value,date\nK,influencer,tiktok,k,followers,5,2026-11-01\n")
    assert conn.execute("SELECT source_id FROM metric_snapshots").fetchone()[0] == "csv_import"


def test_real_data_cannot_be_mixed_into_sample(conn):
    sample.seed(conn, days=10)
    sid = conn.execute("SELECT id FROM entities WHERE is_sample=1 LIMIT 1").fetchone()[0]
    with pytest.raises(repo.ValidationError):
        repo.record_manual_metric(conn, sid, "instagram", "followers", 10, "2026-11-01")
    res = repo.import_metrics_csv(conn, "name,kind,platform,handle,metric,value,date\n"
                                        "SAMPLE Ada Styles,influencer,tiktok,x,followers,5,2026-11-01\n")
    assert res["imported"] == 0 and "SAMPLE" in res["errors"][0]
    assert separation_problems(conn) == []


def test_overview_totals_exclude_sample(tmp_path):
    from app import create_app
    app = create_app(str(tmp_path / "o.db"))
    c = connect(app.config["DB_FILE"])
    sample.seed(c, days=10)
    repo.create_entity(c, "brand", "Real Brand"); c.commit(); c.close()
    html = app.test_client().get("/").get_data(as_text=True)
    # 1 real brand counted; the 3 sample brands are reported as excluded
    assert '<span>Brands monitored</span><strong>1</strong>' in html
    assert "3 sample brands" in html


def test_sample_can_be_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "t")
    from app import create_app
    app = create_app(str(tmp_path / "h.db"))
    app.config["TESTING_NO_CSRF"] = True
    c = connect(app.config["DB_FILE"]); sample.seed(c, days=10); c.close()
    cl = app.test_client()
    assert "SAMPLE Zuma Foods" in cl.get("/brands").get_data(as_text=True)
    cl.post("/settings/sample", data={"next": "/brands"})
    assert "SAMPLE Zuma Foods" not in cl.get("/brands").get_data(as_text=True)
    assert "SAMPLE" not in cl.get("/listening").get_data(as_text=True).split("<tbody>")[1].split("</tbody>")[0]


def test_sample_reports_are_watermarked_and_labelled(conn):
    sample.seed(conn, days=10)
    rid = create_report(conn, 1, 30, "builtin")
    r = dict(conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())
    facts = json.loads(r["facts"])
    assert "(SAMPLE DATA)" in r["title"] and facts["is_sample"]
    assert "SAMPLE DATA" in r["narrative"]
    assert "SAMPLE DATA - fictional" in report_csv(facts)
    pdf = build_pdf(r, facts)
    assert pdf[:4] == b"%PDF" and len(pdf) > 3000


def test_real_report_has_no_sample_and_shows_origin(conn):
    eid = repo.create_entity(conn, "influencer", "Real Creator")
    repo.record_manual_metric(conn, eid, "tiktok", "followers", 1000,
                              (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d"))
    repo.record_manual_metric(conn, eid, "tiktok", "followers", 1200,
                              datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    rid = create_report(conn, eid, 30, "builtin")
    r = dict(conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())
    facts = json.loads(r["facts"])
    assert not facts["is_sample"] and "SAMPLE" not in r["title"]
    assert facts["provenance"] == {"MANUAL": 2}
    assert facts["platforms"][0]["metrics"][0]["growth"]["pct"] == 20.0
    csv_text = report_csv(facts)
    assert ",MANUAL," in csv_text and "SAMPLE" not in csv_text
    assert "typed in by hand" in r["narrative"]


def test_youtube_retention_deletes_after_30_days_but_keeps_manual(conn):
    eid = repo.create_entity(conn, "influencer", "Y")
    old = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for src, ts in (("youtube", old), ("youtube", new), ("manual", old)):
        conn.execute("INSERT INTO metric_snapshots(entity_id,source_id,platform,metric,value,observed_at,collected_at) "
                     "VALUES (?,?,?,?,?,?,?)", (eid, src, "youtube", "followers", 100, ts, ts))
    removed = retention.enforce(conn)
    assert removed["youtube"] == 1
    left = sorted(r[0] for r in conn.execute("SELECT source_id FROM metric_snapshots"))
    assert left == ["manual", "youtube"]


@pytest.mark.skipif(bool(__import__("os").environ.get("MI_TEST_TURSO")), reason="online DB is backed up by Turso")
def test_backup_is_a_complete_copy(conn, tmp_path):
    repo.create_entity(conn, "brand", "B"); conn.commit()
    path = backup(conn, dest_dir=tmp_path / "bk")
    copy = connect(path)
    assert counts(copy) == counts(conn)


def test_entity_raw_export_has_full_history(conn):
    eid = repo.create_entity(conn, "influencer", "H")
    for d, v in (("2026-11-01", 10), ("2026-11-05", 12), ("2026-11-09", 15)):
        repo.record_manual_metric(conn, eid, "tiktok", "followers", v, d)
    text = entity_raw_csv(conn, eid)
    assert text.count("\nmetric,") == 3 and "MANUAL" in text
