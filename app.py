"""Market Intel — local web app.

Run:  python app.py        then open http://127.0.0.1:5000
Only listens on this computer (127.0.0.1) unless you change HOST.
"""
from __future__ import annotations

import json
import os
import re
import sys
from html import escape as html_escape
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from intel.config import ROOT, env, env_int, load_env, on_vercel

load_env()

_BOOT_ERROR: Exception | None = None
try:  # if a part fails to load online, show why instead of crashing
    from intel import charts, repo  # noqa: E402
    from intel.ai.engines import available_engines, claude_handoff_prompt  # noqa: E402
    from intel.analysis import authenticity  # noqa: E402
    from intel.analysis.trends import (daily_counts, iso, latest_metrics, metric_series,  # noqa: E402
                                       sentiment_summary, share_of_voice)
    from intel.db import connect, db_path, get_setting, migrate, origin_of, platform_name, utcnow  # noqa: E402
    from intel.db import online_db as _online_db  # noqa: E402
    from intel.pipeline import Pipeline, make_quota_guard  # noqa: E402
    from intel.providers import build_providers  # noqa: E402
    from intel.exports import entity_raw_csv, report_csv  # noqa: E402
    from intel.pdf import build_pdf  # noqa: E402
    from intel.render import create_report, md_to_html  # noqa: E402
    from intel.reports import METRIC_LABELS  # noqa: E402
except Exception as _e:  # noqa: BLE001
    if not on_vercel():
        raise
    _BOOT_ERROR = _e

JOBS: dict[int, str] = {}          # entity_id -> "running" | "done" | "error: ..."
JOBS_LOCK = threading.Lock()


def _secret_key() -> str:
    if env("SECRET_KEY"):
        return env("SECRET_KEY")
    if on_vercel():
        # Read-only disk: derive a stable key from the private settings every instance shares.
        import hashlib
        return hashlib.sha256(("mi-session|" + env("APP_PASSWORD") + "|" + env("TURSO_AUTH_TOKEN")).encode()).hexdigest()
    p = ROOT / "data" / ".secret_key"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip()


def _conn(db_file):
    """Explicit file (tests) -> that file; otherwise the online DB if configured, else data/intel.db."""
    return connect(db_file) if db_file else connect()


def create_app(db_file: str | None = None, http_get=None) -> Flask:
    app = Flask(__name__)
    from datetime import timedelta as _td
    app.config.update(SECRET_KEY=_secret_key(), MAX_CONTENT_LENGTH=2 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=on_vercel(), PERMANENT_SESSION_LIFETIME=_td(days=30),
                      DB_FILE=db_file, HTTP_GET=http_get,
                      SYNC_JOBS=on_vercel())      # online, background threads stop after each page: work inline
    app.jinja_env.globals["origin_of"] = origin_of
    app.jinja_env.filters["plat"] = platform_name
    with _conn(app.config["DB_FILE"]) as c:
        migrate(c)

    def db():
        if "db" not in g:
            g.db = _conn(app.config["DB_FILE"])
        return g.db

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("db", None)
        if conn:
            conn.close()

    # ---------- login (required whenever APP_PASSWORD is set, and always online) ----------
    OPEN = {"login", "static", "health", "cron_collect"}

    @app.before_request
    def require_login():
        if request.endpoint in OPEN:
            return None
        pw = env("APP_PASSWORD")
        if not pw:
            if on_vercel():
                return Response("Set APP_PASSWORD in Vercel → Settings → Environment Variables, then redeploy.",
                                status=503, mimetype="text/plain")
            return None   # local laptop use without a password is allowed
        if session.get("auth") != _pw_tag(pw):
            return redirect(url_for("login", next=request.path))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        err = None
        if request.method == "POST":
            sent = request.form.get("password", "")
            if env("APP_PASSWORD") and secrets.compare_digest(sent, env("APP_PASSWORD")):
                session.clear()
                session.permanent = True
                session["auth"] = _pw_tag(env("APP_PASSWORD"))
                nxt = request.args.get("next", "/")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            import time as _t
            _t.sleep(1.5)   # slow down guessing
            err = "That password isn't right."
        return render_template("login.html", err=err)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/cron/collect")
    def cron_collect():
        """Called once a day by Vercel Cron (Authorization: Bearer CRON_SECRET)."""
        secret = env("CRON_SECRET")
        if not secret or request.headers.get("Authorization", "") != f"Bearer {secret}":
            abort(401)
        import time as _t
        from intel import schedules as _sched
        from intel import topics as _topics
        from intel.pipeline import due_entities
        start, c = _t.monotonic(), db()
        pipe = Pipeline(c, build_providers(app.config["HTTP_GET"]))
        done, skipped = [], 0
        for eid in due_entities(c):
            if _t.monotonic() - start > 38:      # stay inside the 60-second limit
                skipped += 1
                continue
            pipe.run_entity(eid)
            done.append(eid)
        topics_res = _topics.collect(c, pipe.providers["rss_news"]) if _t.monotonic() - start < 48 else {"new": 0}
        reports = _sched.run_due(c) if _t.monotonic() - start < 52 else []
        return jsonify({"collected": done, "left_for_next_run": skipped, "topic_stories": topics_res.get("new", 0),
                        "reports": reports, "seconds": round(_t.monotonic() - start, 1)})

    # ---------- security: CSRF token on every form ----------
    @app.before_request
    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        if request.method == "POST" and request.endpoint != "login" and not app.config.get("TESTING_NO_CSRF"):
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF")
            if not sent or not secrets.compare_digest(sent, session["csrf"]):
                abort(400, "Form expired. Go back, refresh the page and try again.")

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' "
                                                   "'unsafe-inline'; script-src 'self'")
        return resp

    @app.before_request
    def load_prefs():
        from intel.db import get_setting
        g.show_sample = bool(get_setting(db(), "show_sample", True))

    @app.post("/settings/sample")
    def toggle_sample():
        from intel.db import set_setting
        c = db()
        set_setting(c, "show_sample", not g.show_sample)
        c.commit()
        return redirect(request.form.get("next") or url_for("overview"))

    @app.post("/settings/report")
    def report_settings():
        from intel.db import set_setting
        c = db()
        set_setting(c, "report_prepared_by", request.form.get("prepared_by", "").strip()[:120])
        set_setting(c, "report_footer", request.form.get("footer", "").strip()[:200])
        c.commit()
        flash("Report settings saved.", "ok")
        return redirect(url_for("sources") + "#report-settings")

    @app.context_processor
    def inject():
        c = db()
        unread = c.execute("SELECT COUNT(*) FROM alerts WHERE is_read=0").fetchone()[0]
        has_sample = c.execute("SELECT 1 FROM entities WHERE is_sample=1 LIMIT 1").fetchone() is not None
        return {"csrf_token": session.get("csrf", ""), "unread_alerts": unread, "has_sample": has_sample,
                "show_sample": g.get("show_sample", True), "nav": request.endpoint, "fmt": fmt, "ago": ago,
                "json": json, "origin_of": origin_of, "online": on_vercel() or bool(_online_db()),
                "signed_in": bool(session.get("auth"))}

    # ---------- helpers ----------
    def period(days_default=30):
        try:
            days = max(1, min(int(request.args.get("days", days_default)), 365))
        except ValueError:
            days = days_default
        now = datetime.now(timezone.utc)
        return days, iso(now - timedelta(days=days)), iso(now)

    def entity_or_404(eid):
        e = repo.get_entity(db(), eid)
        if not e:
            abort(404)
        return e

    def form_error(e: Exception, target):
        flash(str(e), "error")
        return redirect(target)

    # ---------- overview ----------
    @app.route("/")
    def overview():
        """Headline numbers count REAL data only. Sample data is shown separately, never mixed in."""
        c = db()
        days, start, end = period(30)
        show = g.show_sample
        brands = repo.list_entities(c, "brand", include_sample=False)
        infl = repo.list_entities(c, "influencer", include_sample=False)
        sample_counts = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind, COUNT(*) n FROM entities WHERE is_sample=1 GROUP BY kind")}
        competitor_ids = {r[0] for r in c.execute(
            "SELECT DISTINCT l.competitor_id FROM competitor_links l JOIN entities e ON e.id=l.competitor_id "
            "WHERE e.is_sample=0")}
        real = "JOIN entities e ON e.id=c.entity_id AND e.is_sample=0"
        mentions = c.execute(f"""SELECT COUNT(*) FROM content_items c {real} WHERE c.kind IN ('mention','article')
                                 AND COALESCE(c.published_at,c.collected_at) BETWEEN ? AND ?""", (start, end)).fetchone()[0]
        sent = c.execute(f"""SELECT c.sentiment_label l, COUNT(*) n FROM content_items c {real}
                             WHERE c.sentiment_label IS NOT NULL AND COALESCE(c.published_at,c.collected_at) BETWEEN ? AND ?
                             GROUP BY l""", (start, end)).fetchall()
        s = {r["l"]: r["n"] for r in sent}
        total_s = sum(s.values())
        net = round((s.get("positive", 0) - s.get("negative", 0)) / total_s * 100) if total_s else None
        pool_b = repo.list_entities(c, "brand", include_sample=show)
        pool_i = repo.list_entities(c, "influencer", include_sample=show)
        trend = daily_counts(c, [e["id"] for e in pool_b + pool_i], start, end)
        now = datetime.now(timezone.utc)
        rows = []
        for e in pool_b + pool_i:
            a = c.execute("""SELECT COUNT(*) FROM content_items WHERE entity_id=? AND kind IN ('mention','article')
                             AND COALESCE(published_at,collected_at) >= ?""", (e["id"], iso(now - timedelta(days=7)))).fetchone()[0]
            b = c.execute("""SELECT COUNT(*) FROM content_items WHERE entity_id=? AND kind IN ('mention','article')
                             AND COALESCE(published_at,collected_at) BETWEEN ? AND ?""",
                          (e["id"], iso(now - timedelta(days=14)), iso(now - timedelta(days=7)))).fetchone()[0]
            if a or b:
                rows.append({**e, "this_week": a, "last_week": b, "delta": a - b})
        trending = sorted(rows, key=lambda r: r["delta"], reverse=True)[:6]
        top_infl = []
        for e in pool_i:
            lm = latest_metrics(c, e["id"])
            f = max((v["value"] for (p, m), v in lm.items() if m == "followers"), default=None)
            er = max((v["value"] for (p, m), v in lm.items() if m == "engagement_rate"), default=None)
            top_infl.append({**e, "followers": f, "er": er})
        top_infl = sorted(top_infl, key=lambda r: (r["er"] or 0), reverse=True)[:6]
        scope = "" if show else "AND COALESCE(e.is_sample,0)=0"
        alerts = c.execute(f"""SELECT a.*, e.name ename, e.is_sample FROM alerts a LEFT JOIN entities e ON e.id=a.entity_id
                               WHERE 1=1 {scope} ORDER BY a.created_at DESC LIMIT 6""").fetchall()
        latest = c.execute(f"""SELECT c.*, e.name ename, e.is_sample FROM content_items c JOIN entities e ON e.id=c.entity_id
                               WHERE c.kind IN ('mention','article') {scope}
                               ORDER BY COALESCE(c.published_at,c.collected_at) DESC LIMIT 8""").fetchall()
        freshness = c.execute("""SELECT d.id, d.name, d.cost_tier, d.enabled, MAX(r.finished_at) last_ok
                                 FROM data_sources d LEFT JOIN collection_runs r ON r.source_id=d.id
                                 AND r.status IN ('ok','partial') WHERE d.id NOT IN ('sample','manual','csv_import')
                                 GROUP BY d.id""").fetchall()
        return render_template("overview.html", days=days, brands=brands, infl=infl, sample_counts=sample_counts,
                               competitors=len(competitor_ids), mentions=mentions, net=net, s=s,
                               trend_chart=charts.bar_chart(trend), trending=trending, top_infl=top_infl,
                               alerts=alerts, latest=latest, freshness=freshness)

    # ---------- lists ----------
    def entity_rows(kind):
        c = db()
        items = repo.list_entities(c, kind, request.args.get("country", ""), request.args.get("category", ""),
                                   request.args.get("platform", ""), request.args.get("q", "").strip(),
                                   include_sample=g.show_sample)
        _, start, end = period(30)
        for e in items:
            lm = latest_metrics(c, e["id"])
            e["followers"] = sum(v["value"] for (p, m), v in lm.items() if m == "followers") or None
            e["er"] = max((v["value"] for (p, m), v in lm.items() if m == "engagement_rate"), default=None)
            e["platforms"] = sorted({a["platform"] for a in repo.accounts_for(c, e["id"])})
            e["sent"] = sentiment_summary(c, e["id"], start, end)
            last = c.execute("SELECT MAX(finished_at) FROM collection_runs WHERE entity_id=? AND status IN ('ok','partial')",
                             (e["id"],)).fetchone()[0]
            e["last_ok"] = last
        cats = [r[0] for r in c.execute("SELECT DISTINCT category FROM entities WHERE kind=? AND category IS NOT NULL "
                                        "ORDER BY 1", (kind,))]
        countries = [r[0] for r in c.execute("SELECT DISTINCT country FROM entities WHERE kind=? AND country IS NOT NULL "
                                             "ORDER BY 1", (kind,))]
        return items, cats, countries

    @app.route("/brands")
    def brands():
        items, cats, countries = entity_rows("brand")
        return render_template("entities.html", kind="brand", items=items, cats=cats, countries=countries,
                               platforms=repo.PLATFORMS, args=request.args)

    @app.route("/influencers")
    def influencers():
        items, cats, countries = entity_rows("influencer")
        return render_template("entities.html", kind="influencer", items=items, cats=cats, countries=countries,
                               platforms=repo.PLATFORMS, args=request.args)

    @app.post("/entities")
    def entity_create():
        f = request.form
        kind = f.get("kind", "brand")
        back = url_for("brands" if kind == "brand" else "influencers")
        c = db()
        try:
            eid = repo.create_entity(c, kind, f.get("name", ""), f.get("country", ""), f.get("category", ""),
                                     f.get("website", ""), f.get("keywords", ""), f.get("notes", ""),
                                     f.get("refresh_hours", 24) or 24)
            for plat in repo.PLATFORMS:
                h = f.get(f"handle_{plat}", "").strip()
                if h:
                    repo.add_account(c, eid, plat, h)
            c.commit()
        except repo.ValidationError as e:
            c.rollback()
            return form_error(e, back)
        # Start collecting straight away, so the page shows data as soon as it opens.
        with JOBS_LOCK:
            JOBS[eid] = "running"
        if app.config.get("SYNC_JOBS"):
            _collect_bg(eid)
            flash(f"Now tracking {f.get('name')}. First collection done — see below what each source found.", "ok")
        else:
            threading.Thread(target=_collect_bg, args=(eid,), daemon=True).start()
            flash(f"Now tracking {f.get('name')}. Collecting the first data now…", "ok")
        return redirect(url_for("entity_detail", eid=eid))

    # ---------- entity detail ----------
    @app.route("/entities/<int:eid>")
    def entity_detail(eid):
        c = db()
        e = entity_or_404(eid)
        days, start, end = period(90)
        accounts = repo.accounts_for(c, eid)
        lm = latest_metrics(c, eid)
        plats = sorted({p for (p, m) in lm if p})
        follower_series = {p.title(): metric_series(c, eid, "followers", p, since=start) for p in plats}
        follower_series = {k: v for k, v in follower_series.items() if v}
        cards = []
        for p in plats:
            for m in ("followers", "engagement_rate", "avg_likes", "avg_comments", "avg_views", "posts"):
                if (p, m) in lm:
                    s = metric_series(c, eid, m, p, since=start)
                    first = s[0]["value"] if s else None
                    cur = lm[(p, m)]
                    pct = ((cur["value"] - first) / first * 100) if first and len(s) > 1 else None
                    cards.append({"platform": p, "metric": m, "label": METRIC_LABELS.get(m, m), "value": cur["value"],
                                  "as_of": cur["observed_at"], "source": cur["source_id"], "pct": pct,
                                  "sample": cur["is_sample"]})
        mentions = c.execute("""SELECT * FROM content_items WHERE entity_id=? AND kind IN ('mention','article')
                                ORDER BY COALESCE(published_at,collected_at) DESC LIMIT 12""", (eid,)).fetchall()
        posts = c.execute("""SELECT * FROM content_items WHERE entity_id=? AND kind IN ('post','video')
                             ORDER BY COALESCE(published_at,collected_at) DESC LIMIT 12""", (eid,)).fetchall()
        sent = sentiment_summary(c, eid, start, end)
        comps = repo.competitors_of(c, eid) if e["kind"] == "brand" else []
        sov = share_of_voice(c, [eid] + [x["id"] for x in comps], start, end) if comps else []
        others = [x for x in repo.list_entities(c, "brand") if x["id"] != eid and x["id"] not in {y["id"] for y in comps}]
        pages = c.execute("SELECT * FROM page_snapshots WHERE entity_id=? ORDER BY id DESC LIMIT 6", (eid,)).fetchall()
        runs = c.execute("""SELECT r.*, d.name sname FROM collection_runs r JOIN data_sources d ON d.id=r.source_id
                            WHERE r.entity_id=? ORDER BY r.id DESC LIMIT 12""", (eid,)).fetchall()
        signals = authenticity.signals(c, eid) if e["kind"] == "influencer" else []
        reports = c.execute("SELECT id,title,generated_at,engine FROM reports WHERE entity_id=? ORDER BY id DESC LIMIT 5",
                            (eid,)).fetchall()
        from intel import partnerships as _pt
        from intel import watchlists as _wl
        partners = _pt.for_brand(c, eid) if e["kind"] == "brand" else _pt.for_influencer(c, eid)
        in_lists = _wl.lists_for_entity(c, eid)
        all_lists = [x for x in _wl.all_lists(c) if x["id"] not in {y["id"] for y in in_lists}]
        brand_campaigns = c.execute("SELECT * FROM campaigns WHERE brand_id=? ORDER BY start_date DESC",
                                    (eid,)).fetchall() if e["kind"] == "brand" else []
        with JOBS_LOCK:
            job = JOBS.get(eid)
        return render_template("entity.html", e=e, partners=partners, in_lists=in_lists, all_lists=all_lists,
                               brand_campaigns=brand_campaigns, days=days, accounts=accounts, cards=cards,
                               follower_chart=charts.line_chart(follower_series, empty="Follower history appears after "
                                                                "the first collection or manual entry"),
                               mention_chart=charts.bar_chart(daily_counts(c, [eid], start, end)),
                               mentions=mentions, posts=posts, sent=sent,
                               sent_bar=charts.split_bar(sent["positive"], sent["neutral"], sent["negative"]),
                               comps=comps, sov=sov, others=others, pages=pages, runs=runs, signals=signals,
                               reports=reports, job=job, platforms=repo.PLATFORMS, metrics=repo.MANUAL_METRICS,
                               keywords=json.loads(e["keywords"] or "[]"), engines=available_engines(),
                               today=datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    @app.post("/entities/<int:eid>/edit")
    def entity_edit(eid):
        entity_or_404(eid)
        f = request.form
        c = db()
        try:
            repo.update_entity(c, eid, country=f.get("country", ""), category=f.get("category", ""),
                               website=f.get("website", ""), keywords=f.get("keywords", ""), notes=f.get("notes", ""),
                               refresh_hours=max(1, min(int(f.get("refresh_hours") or 24), 720)))
            c.commit()
            flash("Saved.", "ok")
        except (repo.ValidationError, ValueError) as e:
            return form_error(e, url_for("entity_detail", eid=eid))
        return redirect(url_for("entity_detail", eid=eid))

    @app.post("/entities/<int:eid>/delete")
    def entity_delete(eid):
        e = entity_or_404(eid)
        c = db()
        repo.delete_entity(c, eid)
        c.commit()
        flash(f"Stopped tracking {e['name']} and removed its data.", "ok")
        return redirect(url_for("brands" if e["kind"] == "brand" else "influencers"))

    @app.post("/entities/<int:eid>/accounts")
    def account_add(eid):
        entity_or_404(eid)
        c = db()
        try:
            repo.add_account(c, eid, request.form.get("platform", ""), request.form.get("handle", ""))
            c.commit()
        except repo.ValidationError as e:
            return form_error(e, url_for("entity_detail", eid=eid))
        return redirect(url_for("entity_detail", eid=eid))

    @app.post("/accounts/<int:aid>/delete")
    def account_delete(aid):
        c = db()
        r = c.execute("SELECT entity_id FROM social_accounts WHERE id=?", (aid,)).fetchone()
        if not r:
            abort(404)
        repo.remove_account(c, aid)
        c.commit()
        return redirect(url_for("entity_detail", eid=r[0]))

    @app.post("/entities/<int:eid>/competitors")
    def competitor_add(eid):
        entity_or_404(eid)
        c = db()
        try:
            cid = request.form.get("competitor_id")
            new_name = request.form.get("new_name", "").strip()
            if new_name:
                cid = repo.create_entity(c, "brand", new_name, request.form.get("country", ""),
                                         request.form.get("category", ""), request.form.get("website", ""))
            if not cid:
                raise repo.ValidationError("Choose a brand or type a new competitor name")
            repo.link_competitor(c, eid, int(cid))
            c.commit()
        except repo.ValidationError as e:
            c.rollback()
            return form_error(e, url_for("entity_detail", eid=eid))
        return redirect(url_for("entity_detail", eid=eid) + "#competitors")

    @app.post("/entities/<int:eid>/competitors/<int:cid>/delete")
    def competitor_remove(eid, cid):
        c = db()
        repo.unlink_competitor(c, eid, cid)
        c.commit()
        return redirect(url_for("entity_detail", eid=eid) + "#competitors")

    @app.post("/entities/<int:eid>/manual")
    def manual_add(eid):
        entity_or_404(eid)
        f = request.form
        c = db()
        try:
            repo.record_manual_metric(c, eid, f.get("platform", ""), f.get("metric", ""), f.get("value", ""),
                                      f.get("observed_on", ""))
            c.commit()
            flash("Recorded. Source will show as “Manual entry”.", "ok")
        except repo.ValidationError as e:
            return form_error(e, url_for("entity_detail", eid=eid))
        return redirect(url_for("entity_detail", eid=eid))

    def _collect_bg(eid: int):
        try:
            conn = _conn(app.config["DB_FILE"])
            Pipeline(conn, build_providers(app.config["HTTP_GET"])).run_entity(eid)
            conn.close()
            state = "done"
        except Exception as e:  # shown on the page
            state = f"error: {e}"
        with JOBS_LOCK:
            JOBS[eid] = state

    @app.post("/entities/<int:eid>/collect")
    def collect_now(eid):
        e = entity_or_404(eid)
        if e["is_sample"]:
            flash("Sample entries are fictional and are not collected.", "error")
            return redirect(url_for("entity_detail", eid=eid))
        with JOBS_LOCK:
            if JOBS.get(eid) == "running":
                return redirect(url_for("entity_detail", eid=eid))
            JOBS[eid] = "running"
        if app.config.get("SYNC_JOBS"):
            _collect_bg(eid)
        else:
            threading.Thread(target=_collect_bg, args=(eid,), daemon=True).start()
        return redirect(url_for("entity_detail", eid=eid))

    @app.route("/entities/<int:eid>/job")
    def job_status(eid):
        with JOBS_LOCK:
            return jsonify({"status": JOBS.get(eid)})

    # ---------- competitors ----------
    @app.route("/competitors")
    def competitors():
        c = db()
        days, start, end = period(30)
        brands_with = c.execute(f"""SELECT DISTINCT e.* FROM entities e JOIN competitor_links l ON l.brand_id=e.id
                                    {'' if g.show_sample else 'WHERE e.is_sample=0'} ORDER BY e.is_sample, e.name""").fetchall()
        sel = request.args.get("brand", type=int) or (brands_with[0]["id"] if brands_with else None)
        table, chart = [], None
        if sel:
            group = [repo.get_entity(c, sel)] + repo.competitors_of(c, sel)
            sov = {r["id"]: r for r in share_of_voice(c, [x["id"] for x in group], start, end)}
            series = {}
            for x in group:
                lm = latest_metrics(c, x["id"])
                fol = {p: v["value"] for (p, m), v in lm.items() if m == "followers"}
                ers = [v["value"] for (p, m), v in lm.items() if m == "engagement_rate"]
                s = sentiment_summary(c, x["id"], start, end)
                pages = c.execute("SELECT COUNT(*) FROM page_snapshots WHERE entity_id=? AND changed=1 AND collected_at>=?",
                                  (x["id"], start)).fetchone()[0]
                last = c.execute("""SELECT title, publisher, published_at FROM content_items WHERE entity_id=?
                                    AND kind IN ('mention','article') ORDER BY published_at DESC LIMIT 1""",
                                 (x["id"],)).fetchone()
                table.append({"e": x, "is_main": x["id"] == sel, "followers": fol, "er": max(ers) if ers else None,
                              "mentions": sov[x["id"]]["mentions"], "share": sov[x["id"]]["share"], "sent": s,
                              "page_changes": pages, "last": last})
                main_plat = max(fol, key=fol.get) if fol else None
                if main_plat:
                    series[x["name"]] = metric_series(c, x["id"], "followers", main_plat, since=start)
            chart = charts.line_chart({k: v for k, v in series.items() if v}, height=300, width=1060,
                                      empty="Follower history appears once data is collected")
        return render_template("competitors.html", brands_with=brands_with, sel=sel, table=table, chart=chart,
                               days=days)

    # ---------- listening ----------
    @app.route("/listening")
    def listening():
        c = db()
        days, start, end = period(30)
        a = request.args
        sql = """SELECT c.*, e.name ename, e.kind ekind FROM content_items c JOIN entities e ON e.id=c.entity_id
                 WHERE COALESCE(c.published_at,c.collected_at) BETWEEN ? AND ?"""
        args = [start, end]
        if not g.show_sample:
            sql += " AND e.is_sample=0"
        if a.get("origin") == "live":
            sql += " AND c.source_id NOT IN ('sample','manual','csv_import')"
        elif a.get("origin") == "sample":
            sql += " AND c.source_id='sample'"
        if a.get("entity", type=int):
            sql += " AND c.entity_id=?"; args.append(a.get("entity", type=int))
        if a.get("sentiment") in ("positive", "neutral", "negative"):
            sql += " AND c.sentiment_label=?"; args.append(a["sentiment"])
        if a.get("source"):
            sql += " AND c.source_id=?"; args.append(a["source"])
        if a.get("kind") in ("mention", "post", "video", "article"):
            sql += " AND c.kind=?"; args.append(a["kind"])
        if a.get("country"):
            sql += " AND e.country=?"; args.append(a["country"].upper()[:2])
        if a.get("q", "").strip():
            sql += " AND (c.title LIKE ? OR c.body LIKE ?)"; args += [f"%{a['q'].strip()}%"] * 2
        total = c.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
        counts = {r["l"]: r["n"] for r in c.execute(
            f"SELECT sentiment_label l, COUNT(*) n FROM ({sql}) GROUP BY l", args)}
        rows = c.execute(sql + " ORDER BY COALESCE(c.published_at,c.collected_at) DESC LIMIT 200", args).fetchall()
        ents = repo.list_entities(c, include_sample=g.show_sample)
        sources = c.execute("SELECT DISTINCT source_id FROM content_items ORDER BY 1").fetchall()
        return render_template("listening.html", rows=rows, total=total, counts=counts, ents=ents, sources=sources,
                               args=a, days=days,
                               bar=charts.split_bar(counts.get("positive", 0), counts.get("neutral", 0),
                                                    counts.get("negative", 0)))

    # ---------- discovery ----------
    @app.route("/discover")
    def discover():
        c = db()
        q = request.args.get("q", "").strip()
        region = (request.args.get("region") or "NG").upper()[:2]
        yt = build_providers(app.config["HTTP_GET"])["youtube"]
        ok, why = yt.is_configured()
        results, error = [], None
        if q and ok:
            try:
                results = yt.search_channels(q[:100], region, make_quota_guard(c))
                tracked = {r[0] for r in c.execute("SELECT external_id FROM social_accounts WHERE platform='youtube'")}
                for r in results:
                    r["tracked"] = r["channel_id"] in tracked
            except Exception as e:
                error = str(e)
        used = c.execute("SELECT units FROM api_usage WHERE source_id='youtube_search' AND day=?",
                         (datetime.now(timezone.utc).strftime("%Y-%m-%d"),)).fetchone()
        return render_template("discover.html", q=q, region=region, results=results, yt_ok=ok, yt_why=why,
                               error=error, units=used[0] if used else 0,
                               budget=env_int("YOUTUBE_DAILY_SEARCH_BUDGET", 90))

    @app.post("/discover/track")
    def discover_track():
        c = db()
        f = request.form
        try:
            eid = repo.create_entity(c, "influencer", f.get("title", ""), f.get("country", "") or "NG",
                                     f.get("niche", ""))
            aid = repo.add_account(c, eid, "youtube", f.get("channel_id", ""))
            c.execute("UPDATE social_accounts SET external_id=? WHERE id=?", (f.get("channel_id"), aid))
            c.commit()
        except repo.ValidationError as e:
            return form_error(e, url_for("discover", q=f.get("q", "")))
        flash("Added. Press “Collect now” to take the first snapshot.", "ok")
        return redirect(url_for("entity_detail", eid=eid))

    # ---------- reports ----------
    @app.route("/reports")
    def reports():
        c = db()
        rows = c.execute("""SELECT r.*, e.name ename FROM reports r LEFT JOIN entities e ON e.id=r.entity_id
                            ORDER BY r.id DESC LIMIT 100""").fetchall()
        from intel import schedules as _s
        from intel import watchlists as _w
        return render_template("reports.html", rows=rows, ents=repo.list_entities(c, include_sample=g.show_sample),
                               engines=available_engines(), scheds=_s.all_schedules(c),
                               real_ents=repo.list_entities(c, include_sample=False), lists=_w.all_lists(c))

    @app.post("/reports")
    def report_create():
        c = db()
        try:
            eid = int(request.form.get("entity_id", 0))
            days = max(1, min(int(request.form.get("days", 30)), 365))
        except ValueError:
            abort(400)
        entity_or_404(eid)
        engine = request.form.get("engine", "builtin")
        if engine not in {e["id"] for e in available_engines() if e["available"]}:
            return form_error(ValueError("That AI engine isn't available on this computer."), url_for("reports"))
        try:
            rid = create_report(c, eid, days, engine)
        except Exception as e:
            return form_error(ValueError(f"Report failed: {e}"), url_for("reports"))
        if engine == "claude_handoff":
            return redirect(url_for("report_handoff", rid=rid))
        return redirect(url_for("report_view", rid=rid))

    def report_or_404(rid):
        r = db().execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if not r:
            abort(404)
        return dict(r)

    @app.route("/reports/<int:rid>")
    def report_view(rid):
        r = report_or_404(rid)
        return render_template("report.html", r=r, body=md_to_html(r["narrative"]), facts=json.loads(r["facts"]))

    @app.route("/reports/<int:rid>.pdf")
    def report_pdf_dl(rid):
        r = report_or_404(rid)
        c = db()
        pdf = build_pdf(r, json.loads(r["facts"]), get_setting(c, "report_prepared_by", ""),
                        get_setting(c, "report_footer", ""))
        return Response(pdf, mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{_fname(r)}.pdf"'})

    @app.route("/reports/<int:rid>.csv")
    def report_csv_dl(rid):
        r = report_or_404(rid)
        return Response(report_csv(json.loads(r["facts"])), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{_fname(r)}.csv"'})

    @app.route("/entities/<int:eid>/export.csv")
    def entity_export(eid):
        e = entity_or_404(eid)
        safe = re.sub(r"[^A-Za-z0-9]+", "-", e["name"]).strip("-").lower() or "export"
        return Response(entity_raw_csv(db(), eid), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{safe}-all-data.csv"'})

    @app.route("/reports/<int:rid>/handoff")
    def report_handoff(rid):
        r = report_or_404(rid)
        return render_template("handoff.html", r=r, prompt=claude_handoff_prompt(json.loads(r["facts"])))

    @app.post("/reports/<int:rid>/narrative")
    def report_paste(rid):
        """Save the report text you got back from Claude Pro, so it's stored with its facts."""
        report_or_404(rid)
        text = request.form.get("narrative", "").strip()
        if not text:
            return form_error(ValueError("Paste the report text first."), url_for("report_handoff", rid=rid))
        c = db()
        c.execute("UPDATE reports SET narrative=?, engine='claude_handoff' WHERE id=?", (text[:100_000], rid))
        c.commit()
        return redirect(url_for("report_view", rid=rid))

    # ---------- alerts ----------
    @app.route("/alerts")
    def alerts():
        rows = db().execute("""SELECT a.*, e.name ename, e.is_sample FROM alerts a LEFT JOIN entities e
                               ON e.id=a.entity_id ORDER BY a.created_at DESC LIMIT 200""").fetchall()
        return render_template("alerts.html", rows=rows)

    @app.post("/alerts/read")
    def alerts_read():
        c = db()
        c.execute("UPDATE alerts SET is_read=1")
        c.commit()
        return redirect(url_for("alerts"))

    # ---------- sources & settings ----------
    @app.route("/sources")
    def sources():
        c = db()
        provs = build_providers()
        rows = []
        for d in c.execute("SELECT * FROM data_sources ORDER BY CASE cost_tier WHEN 'free' THEN 0 WHEN 'free_setup' "
                           "THEN 1 WHEN 'paid' THEN 2 ELSE 3 END, name").fetchall():
            p = provs.get(d["id"])
            ok, why = p.is_configured() if p else (True, d["notes"])
            stats = c.execute("""SELECT COUNT(*) runs, SUM(status IN ('ok','partial')) ok, SUM(status='failed') failed,
                                 MAX(CASE WHEN status IN ('ok','partial') THEN finished_at END) last_ok
                                 FROM collection_runs WHERE source_id=?""", (d["id"],)).fetchone()
            last_err = c.execute("""SELECT message, finished_at FROM collection_runs WHERE source_id=? AND status='failed'
                                    ORDER BY id DESC LIMIT 1""", (d["id"],)).fetchone()
            rows.append({**dict(d), "ready": ok, "why": why, "stats": stats, "last_err": last_err})
        feeds = c.execute("SELECT * FROM news_feeds ORDER BY id").fetchall()
        usage = c.execute("SELECT * FROM api_usage ORDER BY day DESC LIMIT 14").fetchall()
        return render_template("sources.html", rows=rows, feeds=feeds, usage=usage, engines=available_engines(),
                               prepared_by=get_setting(c, "report_prepared_by", ""),
                               footer=get_setting(c, "report_footer", ""),
                               db_file=app.config["DB_FILE"] or ("online database (Turso)" if _online_db() else str(db_path())),
                               csv_headers=repo.CSV_HEADERS)

    @app.post("/sources/<sid>/toggle")
    def source_toggle(sid):
        c = db()
        if sid in ("tiktok", "x", "sample"):
            return form_error(ValueError("This source is postponed and can't be switched on yet."), url_for("sources"))
        c.execute("UPDATE data_sources SET enabled=1-enabled WHERE id=?", (sid,))
        c.commit()
        return redirect(url_for("sources"))

    @app.post("/feeds")
    def feed_add():
        c = db()
        name, url = request.form.get("name", "").strip()[:80], request.form.get("url", "").strip()[:500]
        if not name or not url.startswith(("http://", "https://")):
            return form_error(ValueError("Give the feed a name and a full http(s) address."), url_for("sources"))
        c.execute("INSERT OR IGNORE INTO news_feeds(name,url,country) VALUES (?,?,?)",
                  (name, url, request.form.get("country", "").upper()[:2] or None))
        c.commit()
        flash("Feed added. It will be tested on the next collection.", "ok")
        return redirect(url_for("sources"))

    @app.post("/feeds/<int:fid>/toggle")
    def feed_toggle(fid):
        c = db()
        c.execute("UPDATE news_feeds SET enabled=1-enabled WHERE id=?", (fid,))
        c.commit()
        return redirect(url_for("sources"))

    @app.post("/import")
    def csv_import():
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".csv"):
            return form_error(ValueError("Choose a .csv file."), url_for("sources"))
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return form_error(ValueError("The file must be UTF-8 text (save as CSV UTF-8 in Excel)."), url_for("sources"))
        c = db()
        try:
            res = repo.import_metrics_csv(c, text)
            c.commit()
        except repo.ValidationError as e:
            return form_error(e, url_for("sources"))
        flash(f"Imported {res['imported']} rows." + (f" {len(res['errors'])} problem(s): " +
                                                       "; ".join(res["errors"][:5]) if res["errors"] else ""),
              "ok" if not res["errors"] else "error")
        return redirect(url_for("sources"))

    @app.route("/import/template.csv")
    def csv_template():
        body = ",".join(repo.CSV_HEADERS) + "\nExample Creator,influencer,tiktok,examplecreator,followers,15400,2026-11-01\n"
        return Response(body, mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="metrics-template.csv"'})


    # ================= Phase 3 =================
    from intel import ask as ask_mod
    from intel import campaigns as camp
    from intel import opportunities, partnerships
    from intel import schedules as sched
    from intel import topics as topic_mod
    from intel import watchlists as wl

    def _bg(key, fn):
        """Run fn(conn) in the background (or inline in tests); progress kept in JOBS[key]."""
        def work():
            try:
                conn = _conn(app.config["DB_FILE"])
                fn(conn)
                conn.close()
                state = "done"
            except Exception as e:
                state = f"error: {e}"
            with JOBS_LOCK:
                JOBS[key] = state
        with JOBS_LOCK:
            if JOBS.get(key) == "running":
                return
            JOBS[key] = "running"
        if app.config.get("SYNC_JOBS"):
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    # ---------- watchlists ----------
    @app.route("/watchlists")
    def watchlists():
        return render_template("watchlists.html", rows=wl.all_lists(db()))

    @app.post("/watchlists")
    def watchlist_create():
        c = db()
        try:
            wid = wl.create(c, request.form.get("name", ""), request.form.get("notes", ""))
            c.commit()
        except repo.ValidationError as e:
            return form_error(e, url_for("watchlists"))
        return redirect(url_for("watchlist_detail", wid=wid))

    @app.route("/watchlists/<int:wid>")
    def watchlist_detail(wid):
        c = db()
        w = wl.get(c, wid) or abort(404)
        days, start, end = period(30)
        rows = wl.summary(c, wid, start, end)
        inside = {r["id"] for r in rows}
        others = [e for e in repo.list_entities(c, include_sample=g.show_sample) if e["id"] not in inside]
        scheds = [s for s in sched.all_schedules(c) if s["watchlist_id"] == wid]
        return render_template("watchlist.html", w=w, rows=rows, others=others, days=days, scheds=scheds)

    @app.post("/watchlists/<int:wid>/add")
    def watchlist_add(wid):
        c = db()
        wl.get(c, wid) or abort(404)
        try:
            for eid in request.form.getlist("entity_id"):
                wl.add(c, wid, int(eid))
            c.commit()
        except (repo.ValidationError, ValueError) as e:
            return form_error(e, url_for("watchlist_detail", wid=wid))
        back = request.form.get("next")
        return redirect(back if back and back.startswith("/") else url_for("watchlist_detail", wid=wid))

    @app.post("/watchlists/<int:wid>/remove/<int:eid>")
    def watchlist_remove(wid, eid):
        c = db(); wl.remove(c, wid, eid); c.commit()
        return redirect(url_for("watchlist_detail", wid=wid))

    @app.post("/watchlists/<int:wid>/delete")
    def watchlist_delete(wid):
        c = db(); wl.delete(c, wid); c.commit()
        flash("Watchlist deleted. The brands and creators in it are still tracked.", "ok")
        return redirect(url_for("watchlists"))

    # ---------- influencer comparison ----------
    @app.route("/influencers/compare")
    def influencer_compare():
        c = db()
        ids = []
        for x in request.args.getlist("ids") + request.args.get("id_list", "").split(","):
            if x.strip().isdigit() and int(x) not in ids:
                ids.append(int(x))
        ids = ids[:4]
        _, start, end = period(30)
        cols = []
        for eid in ids:
            e = repo.get_entity(c, eid)
            if not e or e["kind"] != "influencer":
                continue
            lm = latest_metrics(c, eid)
            plats = {}
            for (p, m), v in lm.items():
                if p:
                    plats.setdefault(p, {})[m] = v
            growth = {}
            for p in plats:
                s = metric_series(c, eid, "followers", p, since=start)
                if len(s) >= 2 and s[0]["value"]:
                    growth[p] = (s[-1]["value"] - s[0]["value"]) / s[0]["value"] * 100
            sig = authenticity.signals(c, eid)
            cols.append({"e": e, "plats": plats, "growth": growth, "sent": sentiment_summary(c, eid, start, end),
                         "watch": sum(1 for x in sig if x["level"] == "watch"),
                         "unknown": sum(1 for x in sig if x["level"] == "unknown"),
                         "partners": partnerships.for_influencer(c, eid)})
        all_infl = repo.list_entities(c, "influencer", include_sample=g.show_sample)
        platforms = sorted({p for col in cols for p in col["plats"]})
        return render_template("compare.html", cols=cols, all_infl=all_infl, platforms=platforms,
                               ids=ids, metric_labels=METRIC_LABELS)

    # ---------- topics ----------
    @app.route("/topics")
    def topics_page():
        c = db()
        now = datetime.now(timezone.utc)
        rows = topic_mod.all_topics(c)
        for t in rows:
            t["this_week"], t["last_week"] = topic_mod.week_change(
                c, t["id"], iso(now), iso(now - timedelta(days=7)), iso(now - timedelta(days=14)))
        with JOBS_LOCK:
            job = JOBS.get("topics")
        return render_template("topics.html", rows=rows, job=job)

    @app.post("/topics")
    def topic_create():
        c = db()
        try:
            tid = topic_mod.create(c, request.form.get("name", ""), request.form.get("keywords", ""),
                                   request.form.get("country", ""))
            c.commit()
        except repo.ValidationError as e:
            return form_error(e, url_for("topics_page"))
        flash("Topic saved. Press “Scan news now” to search the feeds.", "ok")
        return redirect(url_for("topic_detail", tid=tid))

    @app.post("/topics/scan")
    def topics_scan():
        prov = build_providers(app.config["HTTP_GET"])["rss_news"]
        _bg("topics", lambda conn: topic_mod.collect(conn, prov))
        return redirect(request.form.get("next") or url_for("topics_page"))

    @app.route("/topics/<int:tid>")
    def topic_detail(tid):
        c = db()
        t = topic_mod.get(c, tid) or abort(404)
        days, start, end = period(30)
        s = topic_mod.summary(c, tid, start, end)
        with JOBS_LOCK:
            job = JOBS.get("topics")
        return render_template("topic.html", t=t, s=s, days=days, job=job, kws=json.loads(t["keywords"]),
                               chart=charts.bar_chart(s["days"]),
                               bar=charts.split_bar(s["positive"], s["neutral"], s["negative"]))

    @app.post("/topics/<int:tid>/delete")
    def topic_delete(tid):
        c = db(); topic_mod.delete(c, tid); c.commit()
        return redirect(url_for("topics_page"))

    @app.route("/jobs/<key>")
    def job_key(key):
        with JOBS_LOCK:
            return jsonify({"status": JOBS.get(key)})

    # ---------- campaigns ----------
    @app.route("/campaigns")
    def campaigns_page():
        c = db()
        return render_template("campaigns.html", rows=camp.all_campaigns(c),
                               brands=repo.list_entities(c, "brand", include_sample=g.show_sample),
                               infl=repo.list_entities(c, "influencer", include_sample=g.show_sample),
                               today=datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    @app.post("/campaigns")
    def campaign_create():
        c = db()
        f = request.form
        try:
            cid = camp.create(c, int(f.get("brand_id") or 0), f.get("name", ""), f.get("start_date", ""),
                              f.get("end_date", ""), f.get("keywords", ""), f.get("notes", ""),
                              [int(x) for x in f.getlist("influencer_id") if x.isdigit()])
            c.commit()
        except (repo.ValidationError, ValueError) as e:
            c.rollback()
            return form_error(e, url_for("campaigns_page"))
        return redirect(url_for("campaign_detail", cid=cid))

    @app.route("/campaigns/<int:cid>")
    def campaign_detail(cid):
        c = db()
        if not camp.get(c, cid):
            abort(404)
        f = camp.facts(c, cid)
        team = {x["id"] for x in camp.influencers(c, cid)}
        others = [e for e in repo.list_entities(c, "influencer", include_sample=g.show_sample) if e["id"] not in team]
        return render_template("campaign.html", f=f, summary=camp.summary_lines(f), others=others)

    @app.post("/campaigns/<int:cid>/creators")
    def campaign_add_creator(cid):
        c = db()
        try:
            camp.add_influencer(c, cid, int(request.form.get("influencer_id") or 0)); c.commit()
        except (repo.ValidationError, ValueError) as e:
            return form_error(e, url_for("campaign_detail", cid=cid))
        return redirect(url_for("campaign_detail", cid=cid))

    @app.post("/campaigns/<int:cid>/creators/<int:iid>/remove")
    def campaign_remove_creator(cid, iid):
        c = db(); camp.remove_influencer(c, cid, iid); c.commit()
        return redirect(url_for("campaign_detail", cid=cid))

    @app.post("/campaigns/<int:cid>/delete")
    def campaign_delete(cid):
        c = db(); camp.delete(c, cid); c.commit()
        return redirect(url_for("campaigns_page"))

    @app.route("/campaigns/<int:cid>.pdf")
    def campaign_pdf(cid):
        c = db()
        if not camp.get(c, cid):
            abort(404)
        f = camp.facts(c, cid)
        from intel.pdf import build_campaign_pdf
        pdf = build_campaign_pdf(f, camp.summary_lines(f), get_setting(c, "report_prepared_by", ""),
                                 get_setting(c, "report_footer", ""))
        name = re.sub(r"[^A-Za-z0-9]+", "-", f["campaign"]["name"]).strip("-").lower()
        return Response(pdf, mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}-campaign.pdf"'})

    @app.route("/campaigns/<int:cid>.csv")
    def campaign_csv_dl(cid):
        c = db()
        if not camp.get(c, cid):
            abort(404)
        from intel.exports import campaign_csv
        f = camp.facts(c, cid)
        name = re.sub(r"[^A-Za-z0-9]+", "-", f["campaign"]["name"]).strip("-").lower()
        return Response(campaign_csv(f), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}-campaign.csv"'})

    # ---------- opportunities ----------
    @app.route("/insights")
    def insights_page():
        return render_template("insights.html", items=opportunities.find(db(), include_sample=g.show_sample))

    # ---------- scheduled reports ----------
    @app.post("/schedules")
    def schedule_create():
        c = db()
        f = request.form
        target = f.get("target", "")
        try:
            kind, _, tid = target.partition(":")
            if not tid.isdigit():
                raise repo.ValidationError("Choose what to report on")
            sched.create(c, f.get("frequency", "weekly"), int(tid) if kind == "e" else None,
                         int(tid) if kind == "w" else None)
            c.commit()
            flash("Scheduled. Reports are saved in data/reports/ each time collection runs and one is due.", "ok")
        except repo.ValidationError as e:
            return form_error(e, url_for("reports") + "#schedules")
        return redirect(url_for("reports") + "#schedules")

    @app.post("/schedules/<int:sid>/delete")
    def schedule_delete(sid):
        c = db(); sched.delete(c, sid); c.commit()
        return redirect(url_for("reports") + "#schedules")

    @app.post("/schedules/run")
    def schedule_run():
        c = db()
        files = sched.run_due(c)
        flash(f"{len(files)} scheduled report(s) written to data/reports/." if files else
              "No scheduled reports are due right now.", "ok")
        return redirect(url_for("reports") + "#schedules")

    # ---------- ask your data ----------
    @app.route("/ask", methods=["GET", "POST"])
    def ask_page():
        c = db()
        ents = repo.list_entities(c, include_sample=g.show_sample)
        lists = wl.all_lists(c)
        result = {}
        if request.method == "POST":
            q = request.form.get("question", "").strip()[:1000]
            scope = request.form.get("scope", "all")
            if not q:
                return form_error(ValueError("Type a question first."), url_for("ask_page"))
            if scope.startswith("e:") and scope[2:].isdigit():
                ids = [int(scope[2:])]
            elif scope.startswith("w:") and scope[2:].isdigit():
                ids = [m["id"] for m in wl.members(c, int(scope[2:]))]
            else:
                ids = [e["id"] for e in repo.list_entities(c, include_sample=False)]
            if not ids:
                return form_error(ValueError("Nothing to ask about yet: add brands or creators first."),
                                  url_for("ask_page"))
            facts = ask_mod.gather(c, ids, 30)
            result = {"question": q, "scope": scope, "pack": ask_mod.pack(q, facts), "n": len(facts)}
            if request.form.get("engine") == "ollama":
                try:
                    result["answer"] = ask_mod.answer_with_ollama(q, facts)
                except Exception as e:
                    result["error"] = str(e)
        return render_template("ask.html", ents=ents, lists=lists, r=result, engines=available_engines())

    # ---------- JSON API (for a future Next.js front end) ----------
    @app.route("/api/entities")
    def api_entities():
        kind = request.args.get("kind")
        return jsonify([{k: e[k] for k in ("id", "kind", "name", "country", "category", "website", "is_sample")}
                        for e in repo.list_entities(db(), kind if kind in ("brand", "influencer") else None)])

    @app.route("/api/entities/<int:eid>/metrics")
    def api_metrics(eid):
        entity_or_404(eid)
        metric = request.args.get("metric", "followers")
        return jsonify(metric_series(db(), eid, metric, request.args.get("platform") or None))

    @app.route("/api/entities/<int:eid>/mentions")
    def api_mentions(eid):
        entity_or_404(eid)
        rows = db().execute("""SELECT title,url,publisher,published_at,sentiment_label,sentiment_score,source_id,
                               is_sample FROM content_items WHERE entity_id=? ORDER BY published_at DESC LIMIT 200""",
                            (eid,)).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route("/health")
    def health():
        db().execute("SELECT 1")
        return jsonify({"ok": True, "time": utcnow()})

    @app.errorhandler(404)
    def nf(_e):
        return render_template("error.html", code=404, msg="That page or record doesn't exist."), 404

    @app.errorhandler(400)
    def bad(e):
        return render_template("error.html", code=400, msg=getattr(e, "description", "Bad request")), 400

    return app


def _fname(r: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", r["title"].split(" — ")[0]).strip("-").lower() or "report"
    return f"{base}-report-{r['period_end'][:10]}" + ("-SAMPLE" if "(SAMPLE DATA)" in r["title"] else "")


def _pw_tag(pw: str) -> str:
    import hashlib
    return hashlib.sha256(("mi-login|" + pw).encode()).hexdigest()[:32]


# ---------- template helpers ----------
def fmt(v, digits=0):
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if abs(v) >= 10_000:
        return f"{v/1_000:.1f}k"
    return f"{v:,.{digits}f}"


def ago(ts):
    if not ts:
        return "never"
    try:
        d = datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    s = d.total_seconds()
    if s < 0: return "just now"
    if s < 3600: return f"{int(s//60)} min ago"
    if s < 86400: return f"{int(s//3600)} h ago"
    return f"{int(s//86400)} days ago"


def _setup_problem_app(err: Exception) -> Flask:
    """If the app can't start online, show what to fix instead of a blank 'function failed' page."""
    fallback = Flask(__name__)
    missing = [k for k in ("APP_PASSWORD", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN") if not env(k)]
    url = env("TURSO_DATABASE_URL")
    hints = []
    if missing:
        hints.append("These settings are missing in Vercel → Settings → Environment Variables: " + ", ".join(missing) + ".")
    if url and not url.startswith(("libsql://", "https://")):
        hints.append("TURSO_DATABASE_URL should start with libsql:// (copy it again from Turso, with no spaces).")
    if "token" in str(err).lower() or "401" in str(err):
        hints.append("Turso refused the token: create a new token in Turso and paste it into TURSO_AUTH_TOKEN.")
    if "unreachable" in str(err).lower():
        hints.append("The database address couldn't be reached: copy TURSO_DATABASE_URL again from Turso exactly.")
    if not hints:
        hints.append("Unexpected start-up error. Send Claude the message below.")
    detail = f"{err.__class__.__name__}: {err}"
    for secret in (env("TURSO_AUTH_TOKEN"), env("APP_PASSWORD"), env("SECRET_KEY"), env("CRON_SECRET")):
        if secret and len(secret) >= 6:
            detail = detail.replace(secret, "•••")

    @fallback.route("/", defaults={"path": ""})
    @fallback.route("/<path:path>")
    def problem(path):
        items = "".join(f"<li>{html_escape(h)}</li>" for h in hints)
        return (f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>Market Intel — setup needed</title><body style='font:16px/1.5 system-ui;max-width:640px;margin:40px auto;padding:0 16px'>"
                f"<h1>Almost there — one setting needs fixing</h1><ul>{items}</ul>"
                f"<p>After changing a setting: Vercel → Deployments → ⋯ on the latest → <b>Redeploy</b>.</p>"
                f"<p style='color:#666;font-size:13px'>Technical detail: {html_escape(detail[:400])}</p></body>", 503)
    return fallback


def _make_online_app() -> Flask:
    if _BOOT_ERROR is not None:
        import traceback
        tb = "".join(traceback.format_exception(_BOOT_ERROR))[-600:]
        return _setup_problem_app(RuntimeError(f"a part of the app failed to load: {tb}"))
    try:
        if not env("TURSO_DATABASE_URL"):
            raise RuntimeError("TURSO_DATABASE_URL is not set, so there is nowhere to save data online")
        return create_app()
    except Exception as e:  # never crash on start-up online
        return _setup_problem_app(e)


# Vercel imports `app` from this file; locally it is created in __main__ below.
app = _make_online_app() if on_vercel() else None


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    app = create_app()
    try:
        from intel.maintenance import backup_if_none_today
        with _conn(app.config["DB_FILE"]) as _c:
            b = backup_if_none_today(_c)
        if b:
            print(f"  Daily backup saved: {b}")
    except Exception as _e:  # a failed backup must never stop the app
        print(f"  (backup skipped: {_e})")
    host = env("HOST", "127.0.0.1")
    port = env_int("PORT", 5000)
    print(f"\n  Market Intel running at http://{host}:{port}\n  Database: {app.config['DB_FILE']}\n")
    app.run(host=host, port=port, debug=False, threaded=True)
