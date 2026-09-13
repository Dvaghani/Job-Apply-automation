"""Local review queue. The human-in-the-loop step the whole design turns on."""

from __future__ import annotations

import json

from flask import Flask, redirect, render_template, request, url_for

from . import db
from .config import Config
from .models import (
    STATUS_APPLIED,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SCORED,
)


def create_app(config: Config) -> Flask:
    app = Flask(__name__)

    def rows(status: str):
        if status == STATUS_SCORED:
            return db.review_queue(conn(), min_score=config.min_score)
        return db.by_status(conn(), status)

    def conn():
        # SQLite connections are not shareable across threads; the Flask dev
        # server is single-threaded here (threaded=False in serve()).
        if not hasattr(app, "_conn"):
            app._conn = db.connect(config.db_path)
        return app._conn

    @app.template_filter("flags")
    def _flags(value):
        if not value:
            return []
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return []

    @app.route("/")
    def index():
        status = request.args.get("status", STATUS_SCORED)
        return render_template(
            "review.html",
            jobs=rows(status),
            status=status,
            stats=db.stats(conn()),
            min_score=config.min_score,
        )

    @app.post("/decide/<fingerprint>")
    def decide(fingerprint):
        action = request.form.get("action")
        mapping = {
            "approve": STATUS_APPROVED,
            "reject": STATUS_REJECTED,
            "applied": STATUS_APPLIED,
        }
        if action not in mapping:
            return "unknown action", 400
        db.set_status(conn(), fingerprint, mapping[action])
        conn().commit()
        return redirect(url_for("index", status=request.form.get("from", STATUS_SCORED)))

    return app


def serve(config: Config, host: str = "127.0.0.1", port: int = 5000) -> None:
    # Bind to localhost only. This page lists everything you're applying to;
    # it is not something to expose on a network.
    create_app(config).run(host=host, port=port, threaded=False)
