"""Local dashboard: run the pipeline, and make the call on every job.

Two pages, one server. `/` runs commands and shows what came back; `/review`
is the queue where you approve or reject. Both bind to localhost only — this
lists everything you are applying to, and it is not something to put on a
network.

The dashboard starts commands but never decides anything. Approving a job is
still a click you make, and `apply` still hands you the browser without
touching Submit. Automating the running was always the point; automating the
judgement was never it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from flask import (
    Flask, jsonify, redirect, render_template, request, send_file, url_for
)

from . import db, extapi, settings as settings_mod, tasks
from .config import Config
from .models import (
    STATUS_APPLIED,
    STATUS_APPROVED,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_SCORED,
)


def open_folder(folder) -> None:
    """Show a folder in the desktop's file manager."""
    path = str(folder)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a directory, not a command
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    else:
        subprocess.run(["xdg-open", path], check=True)


# A view, not a status: "scored, but under the threshold". These jobs still
# have status `scored` in the database — nothing has been decided about them.
BELOW = "below"


def _job_json(row) -> dict:
    """One job as the dashboard sees it — the same shape the extension gets."""
    return extapi.job_summary(row)


def _config_summary(config: Config) -> dict:
    sources = config.sources or {}
    named = {
        "greenhouse": config.greenhouse_boards,
        "lever": config.lever_sites,
        "ashby": config.ashby_orgs,
        "smartrecruiters": config.smartrecruiters_companies,
    }
    active = [f"{name} ({len(v)})" for name, v in named.items() if v]
    adzuna = config.adzuna
    if adzuna.get("app_id"):
        queries = adzuna.get("queries") or []
        active.append(f"adzuna ({len(queries)} queries, {adzuna.get('country', '?')})")
    return {
        "path": config.path,
        "backend": config.backend,
        "model": config.active_model,
        "min_score": config.min_score,
        "sources": active or ["none configured"],
    }


def create_app(
    config: Config, runner: tasks.Runner | None = None, token: str | None = None
) -> Flask:
    app = Flask(__name__)
    app.runner = runner or tasks.Runner(config_path=config.path)
    app.ext_token = token or extapi.load_or_create_token()

    def conn():
        # SQLite connections are not shareable across threads; the Flask dev
        # server is single-threaded here (threaded=False in serve()). Commands
        # run in their own processes and open their own connections, so a long
        # ingest never blocks this one — WAL lets them overlap.
        if not hasattr(app, "_conn"):
            app._conn = db.connect(config.db_path)
        return app._conn

    def rows(status: str):
        if status == STATUS_SCORED:
            return db.review_queue(conn(), min_score=config.min_score)
        if status == BELOW:
            return db.below_threshold(conn(), min_score=config.min_score)
        return db.by_status(conn(), status)

    def counts() -> dict:
        """Status counts, plus what the review queue will actually show.

        `stats["scored"]` counts every scored job; the queue only shows those
        at or above `min_score`. Reporting the first as "to review" is how the
        dashboard ends up promising 240 and the page delivering none.
        """
        stats = db.stats(conn())
        ready = len(db.review_queue(conn(), min_score=config.min_score))
        stats["to_review"] = ready
        stats["below"] = stats["scored"] - ready
        return stats

    def state_payload() -> dict:
        current = app.runner.current()
        return {
            "stats": counts(),
            "min_score": config.min_score,
            "approved": [_job_json(r) for r in db.by_status(conn(), STATUS_APPROVED)],
            "current": current.state() if current else None,
        }

    @app.template_filter("flags")
    def _flags(value):
        if not value:
            return []
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return []

    # -- guard -----------------------------------------------------------

    @app.before_request
    def _same_origin_only():
        """Keep other pages in the browser from driving this one.

        The JSON endpoints start processes. A page on any other site can POST
        to localhost without reading the response, so requiring a JSON body
        (which forces a CORS preflight a plain form cannot send) and matching
        Origin is what stops a stray tab from running your pipeline.
        """
        if request.method == "GET" or not request.path.startswith("/api/"):
            return None
        origin = request.headers.get("Origin")
        if origin and origin not in (
            f"http://{request.host}", f"https://{request.host}"
        ):
            return jsonify(error="cross-origin request refused"), 403
        if not request.is_json:
            return jsonify(error="expected a JSON body"), 415
        return None

    # -- pages -----------------------------------------------------------

    @app.route("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            page="dashboard",
            commands=tasks.COMMANDS,
            config=_config_summary(config),
            ext_token=app.ext_token,
            extension_dir=str(extapi.Path(__file__).parent / "extension"),
            stats=counts(),
            approved=[_job_json(r) for r in db.by_status(conn(), STATUS_APPROVED)],
        )

    @app.route("/settings")
    def settings():
        return render_template(
            "settings.html",
            page="settings",
            groups=settings_mod.grouped(),
            values=settings_mod.read(config.path),
            config_path=str(extapi.Path(config.path).resolve()),
        )

    @app.route("/review")
    def review():
        status = request.args.get("status", STATUS_SCORED)
        if status not in (
            STATUS_NEW, STATUS_SCORED, STATUS_APPROVED, STATUS_APPLIED,
            STATUS_REJECTED, BELOW,
        ):
            status = STATUS_SCORED
        return render_template(
            "review.html",
            page="review",
            jobs=rows(status),
            status=status,
            stats=counts(),
            min_score=config.min_score,
        )

    DECISIONS = {
        "approve": STATUS_APPROVED,
        "reject": STATUS_REJECTED,
        "applied": STATUS_APPLIED,
    }

    @app.post("/decide/<fingerprint>")
    def decide(fingerprint):
        action = request.form.get("action")
        mapping = DECISIONS
        if action not in mapping:
            return "unknown action", 400
        db.set_status(conn(), fingerprint, mapping[action])
        conn().commit()
        target = request.form.get("from", STATUS_SCORED)
        if target == "dashboard":
            return redirect(url_for("dashboard"))
        return redirect(url_for("review", status=target))

    # -- api -------------------------------------------------------------

    @app.get("/doc/<fingerprint>/<name>")
    def document(fingerprint, name):
        """Serve a tailored document to the dashboard itself.

        No token, unlike the extension's copy of this: it is same-origin, and
        a page on another site can issue the request but cannot read the
        response without CORS headers, which are not sent.
        """
        path = extapi.servable_path(db.get(conn(), fingerprint), name)
        if path is None:
            return "no such document", 404
        return send_file(path, mimetype=extapi.SERVABLE[name].split(";")[0])

    @app.post("/api/reveal/<fingerprint>")
    def reveal(fingerprint):
        """Open a job's folder in the system file manager.

        The dashboard runs on the machine you are sitting at, so this is the
        shortest path from "the autofill missed the upload" to "here are the
        files". It opens a folder and nothing else — the path comes from the
        database, never from the request.
        """
        folder = extapi.job_folder(db.get(conn(), fingerprint))
        if folder is None:
            return jsonify(error="nothing tailored for this job yet"), 404
        try:
            open_folder(folder)
        except OSError as exc:
            return jsonify(error=f"could not open the folder: {exc}"), 500
        return jsonify(ok=True, folder=str(folder.resolve()))

    @app.post("/api/decide/<fingerprint>")
    def api_decide(fingerprint):
        """Record a decision without navigating away.

        The review page can afford a form post and a redirect. The dashboard
        cannot: reloading it throws away whatever was set up in the command
        panel, so marking a job applied would quietly reset the tailoring
        language back to English.
        """
        body = request.get_json(silent=True) or {}
        status = DECISIONS.get(body.get("action"))
        if status is None:
            return jsonify(error="unknown action"), 400
        db.set_status(conn(), fingerprint, status)
        conn().commit()
        return jsonify(ok=True, **state_payload())

    @app.post("/api/settings")
    def api_settings():
        """Apply an edit to config.yaml.

        The running server keeps its own Config, loaded at startup, so a
        saved change does not take effect until it restarts. Say so rather
        than let the next Ingest quietly use the old terms.
        """
        body = request.get_json(silent=True) or {}
        try:
            changed = settings_mod.write(config.path, body.get("values") or {})
        except settings_mod.SettingsError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(
            ok=True,
            changed=changed,
            restart_required=bool(changed),
            values=settings_mod.read(config.path),
        )

    @app.post("/api/probe")
    def api_probe():
        """Live count for one search term, so it can be judged before use.

        Bundesagentur only: it is free and unmetered. Adzuna costs quota
        from a small monthly allowance, which is not something a page should
        spend on every keystroke.
        """
        from .sources import arbeitsagentur
        from .sources.base import SourceError

        body = request.get_json(silent=True) or {}
        query = str(body.get("query") or "").strip()
        if not query:
            return jsonify(error="no search term"), 400

        try:
            _, found = arbeitsagentur.search(
                query,
                where=str(body.get("where") or ""),
                radius_km=body.get("umkreis"),
                max_days_old=body.get("max_days_old"),
                exclude_staffing=bool(body.get("exclude_staffing", True)),
                size=1,
            )
        except SourceError as exc:
            return jsonify(error=str(exc)[:140]), 502
        return jsonify(query=query, count=found)

    @app.get("/api/state")
    def api_state():
        return jsonify(state_payload())

    @app.post("/api/run")
    def api_run():
        body = request.get_json(silent=True) or {}
        try:
            task = app.runner.start(body.get("command"), body.get("options"))
        except tasks.TaskError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(task=task.state())

    @app.get("/api/task/<task_id>")
    def api_task(task_id):
        task = app.runner.get(task_id)
        if task is None:
            return jsonify(error="no such task"), 404
        return jsonify(task=task.state(cursor=request.args.get("cursor", 0, type=int)))

    # What the page may say to a waiting command — an allowlist, like the
    # commands themselves. Arbitrary text from a request never reaches a
    # process's stdin.
    INSTRUCTIONS = {"": "\n", "fill": "fill\n"}

    @app.post("/api/task/<task_id>/send")
    def api_send(task_id):
        body = request.get_json(silent=True) or {}
        instruction = str(body.get("text", "")).strip().lower()
        if instruction not in INSTRUCTIONS:
            return jsonify(error="not an instruction this page can send"), 400
        try:
            app.runner.send(task_id, INSTRUCTIONS[instruction])
        except tasks.TaskError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(ok=True)

    @app.post("/api/task/<task_id>/stop")
    def api_stop(task_id):
        try:
            app.runner.stop(task_id)
        except tasks.TaskError as exc:
            return jsonify(error=str(exc)), 404
        return jsonify(ok=True)

    extapi.register(app, config, app.ext_token, conn)
    return app


def serve(config: Config, host: str = "127.0.0.1", port: int = 5000) -> None:
    # Bind to localhost only. See the module docstring.
    create_app(config).run(host=host, port=port, threaded=False)
