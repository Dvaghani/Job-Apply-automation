"""The endpoints the Chrome extension talks to.

The extension exists because Playwright always launches a throwaway profile:
it can never have the passwords, cookies and SSO sessions of the browser you
actually use, and most employer portals want you signed in. Running in your
own Chrome solves that — at the cost of no longer being a browser this
program controls.

So the split is: the extension reads the page and writes to it, and this
module decides what should go where. All the policy — which field is which,
what counts as a credential, what is yours to answer — stays in Python, in
the same `plan_field` that `jobpipe apply` uses. A JavaScript reimplementation
of that would agree with this one right up until somebody edited one of them.

Kept apart from `web.py` because the trust model is different. The dashboard
is same-origin and its own pages call it; an extension has no stable origin,
so these routes authenticate with a bearer token instead.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from flask import jsonify, request, send_file

from . import applicant as applicant_mod
from . import autofill, db
from .config import Config
from .models import STATUS_APPROVED

log = logging.getLogger(__name__)

TOKEN_FILE = ".jobpipe-token"

# The only filenames ever served out of an application folder. An allowlist
# rather than sanitising whatever arrived: the request picks from this, it
# does not describe a path.
# Markdown is served as plain text on purpose: browsers download text/markdown
# instead of showing it, and the point of these links is to read the thing
# without leaving the page.
_TYPES = {
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "md": "text/plain; charset=utf-8",
}

# The same five documents per output language: resume.pdf and resume.de.pdf
# both need serving, and both need to stay on the allowlist rather than the
# language becoming part of a path the request controls.
SERVABLE = {
    f"{stem}{suffix}.{ext}": _TYPES[ext]
    for suffix in ("", ".de")
    for stem, ext in [
        ("resume", "pdf"), ("resume", "html"), ("resume", "md"),
        ("cover-letter", "md"), ("NOTES", "md"),
    ]
}


def load_or_create_token(path: str | Path = TOKEN_FILE) -> str:
    """The shared secret between this server and the extension.

    Persisted so it survives a restart — a token that changed every time
    would mean re-pasting it into the extension every morning.
    """
    file = Path(path)
    if file.exists():
        token = file.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    file.write_text(token + "\n", encoding="utf-8")
    log.info("wrote a new extension token to %s", file)
    return token


def job_folder(row) -> Path | None:
    """Where a job's tailored documents live, if it has been tailored."""
    if not row or not row["output_dir"]:
        return None
    folder = Path(row["output_dir"])
    return folder if folder.is_dir() else None


def servable_path(row, name: str) -> Path | None:
    """Resolve one allowlisted filename inside a job's folder, or None.

    The folder comes from the database and the name from a fixed set, so
    there is nothing here for a traversal to work on — the containment check
    is belt and braces.
    """
    if name not in SERVABLE:
        return None
    folder = job_folder(row)
    if folder is None:
        return None
    path = (folder / name).resolve()
    if not path.is_file() or folder.resolve() not in path.parents:
        return None
    return path


def documents_for(row) -> list[dict]:
    """Which of a job's tailored documents actually exist."""
    folder = job_folder(row)
    if folder is None:
        return []
    return [
        {"name": name, "size": (folder / name).stat().st_size}
        for name in SERVABLE
        if (folder / name).is_file()
    ]


def job_summary(row) -> dict:
    return {
        "fingerprint": row["fingerprint"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "url": row["url"],
        "score": row["score"],
        "reason": row["score_reason"],
        "tailored": bool(row["tailored_at"]),
        "output_dir": row["output_dir"],
        # Absolute, because the point of showing it is pasting it into a
        # file-upload dialog when the autofill misses.
        "folder": str(job_folder(row).resolve()) if job_folder(row) else None,
        "documents": [d["name"] for d in documents_for(row)],
    }


def attachments_for(row, language: str = "en") -> tuple[dict, str | None]:
    """The files to attach for a job, rendering the PDF if it isn't there yet.

    Returns the mapping and a note when something could not be prepared —
    a missing resume is worth saying out loud rather than silently filling
    every field except the one that matters.
    """
    folder = job_folder(row)
    if folder is None:
        return {}, "this job has not been tailored yet — no resume to attach"
    try:
        return autofill.gather_attachments(folder, language), None
    except Exception as exc:  # rendering the PDF needs a browser
        log.warning("could not prepare attachments: %s", exc)
        return {}, f"could not prepare the resume: {str(exc)[:80]}"


def register(app, config: Config, token: str, conn_factory) -> None:
    """Add the /ext routes to an existing app."""

    def authorized() -> bool:
        header = request.headers.get("Authorization", "")
        presented = header[7:].strip() if header.startswith("Bearer ") else ""
        # Constant-time: this is a secret, compare it like one.
        return bool(presented) and secrets.compare_digest(presented, token)

    @app.before_request
    def _require_token():
        if not request.path.startswith("/ext/"):
            return None
        if not authorized():
            return jsonify(error="bad or missing extension token"), 401
        return None

    @app.get("/ext/ping")
    def ext_ping():
        rows = db.by_status(conn_factory(), STATUS_APPROVED)
        return jsonify(ok=True, approved=len(rows))

    @app.get("/ext/jobs")
    def ext_jobs():
        rows = db.by_status(conn_factory(), STATUS_APPROVED)
        return jsonify(jobs=[job_summary(r) for r in rows])

    @app.post("/ext/plan")
    def ext_plan():
        body = request.get_json(silent=True) or {}
        fields = body.get("fields")
        if not isinstance(fields, list):
            return jsonify(error="expected a list of fields"), 400

        language = str(body.get("language") or config.language or "en").lower()
        row = db.get(conn_factory(), body["job"]) if body.get("job") else None
        attachments, note = attachments_for(row, language) if row else ({}, None)

        try:
            me = applicant_mod.load(config.applicant_path)
        except applicant_mod.ApplicantError as exc:
            return jsonify(error=str(exc)), 400

        plans = [autofill.plan_field(f, me, attachments) for f in fields]
        actions = []
        for plan in plans:
            action = {
                "id": plan.id, "label": plan.label, "key": plan.key,
                "action": plan.action, "how": plan.how, "value": plan.value,
                "detail": plan.detail,
            }
            if plan.how == "file" and row is not None:
                action["filename"] = Path(str(attachments[plan.value])).name
                action["url"] = f"/ext/file/{row['fingerprint']}/{action['filename']}"
                action["value"] = ""  # the bytes come from the URL, not from here
            actions.append(action)

        return jsonify(
            actions=actions,
            filled=sum(1 for a in actions if a["action"] == "filled"),
            note=note,
        )

    @app.get("/ext/file/<fingerprint>/<name>")
    def ext_file(fingerprint, name):
        row = db.get(conn_factory(), fingerprint)
        path = servable_path(row, name)
        if path is None:
            return jsonify(error="no such document"), 404
        return send_file(path, mimetype=SERVABLE[name].split(";")[0])
