"""The endpoints the Chrome extension talks to.

The extension runs in a browser this program does not control, so these
routes are the trust boundary: a bearer token instead of an origin, an
allowlist instead of a path, and — the one that matters — the same
`plan_field` the Playwright path uses, so a credential is refused here for
exactly the same reason it is refused there.
"""

from __future__ import annotations

import json

import pytest

from jobpipe import db, extapi, web
from jobpipe.config import Config
from jobpipe.models import STATUS_APPROVED, Job

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def config(tmp_path):
    applicant = tmp_path / "applicant.yaml"
    applicant.write_text(
        "fields:\n"
        "  first_name: Ada\n"
        "  email: ada@example.com\n"
        "answers:\n"
        '  "why do you want": "Payments."\n',
        encoding="utf-8",
    )
    return Config(
        db_path=str(tmp_path / "jobs.db"),
        path=str(tmp_path / "config.yaml"),
        applicant_path=str(applicant),
    )


@pytest.fixture
def client(config):
    app = web.create_app(config, runner=object(), token=TOKEN)
    app.config["TESTING"] = True
    return app.test_client()


def seed(config, tmp_path, tailored=True):
    conn = db.connect(config.db_path)
    job = Job(source="greenhouse", source_id="1", company="Acme",
              title="Radar Engineer", url="https://example.com/j/1")
    fp = job.fingerprint()
    db.upsert_job(conn, job)
    db.save_score(conn, fp, 82, "strong overlap", ["on-site"])
    db.set_status(conn, fp, STATUS_APPROVED)
    if tailored:
        folder = tmp_path / "applications" / "acme-radar"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").write_bytes(b"%PDF-1.4 fake")
        (folder / "resume.html").write_text("<p>resume</p>", encoding="utf-8")
        (folder / "NOTES.md").write_text("# notes", encoding="utf-8")
        db.mark_tailored(conn, fp, str(folder))
    conn.commit()
    conn.close()
    return fp


def field(**kw):
    base = {
        "id": "jp-0", "tag": "input", "type": "text", "name": "", "label": "",
        "placeholder": "", "ariaLabel": "", "required": False, "value": "",
        "options": [],
    }
    base.update(kw)
    return base


# -- the token -----------------------------------------------------------


def test_no_token_is_refused(client):
    assert client.get("/ext/jobs").status_code == 401


def test_a_wrong_token_is_refused(client):
    resp = client.get("/ext/jobs", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_a_malformed_header_is_refused(client):
    assert client.get("/ext/jobs", headers={"Authorization": TOKEN}).status_code == 401


def test_the_right_token_is_accepted(client):
    assert client.get("/ext/ping", headers=AUTH).status_code == 200


def test_the_token_survives_a_restart(tmp_path):
    path = tmp_path / ".jobpipe-token"
    first = extapi.load_or_create_token(path)
    assert extapi.load_or_create_token(path) == first, "a new token every start means re-pasting it"


def test_a_blank_token_file_is_replaced(tmp_path):
    path = tmp_path / ".jobpipe-token"
    path.write_text("\n", encoding="utf-8")
    assert len(extapi.load_or_create_token(path)) > 10


# -- jobs ----------------------------------------------------------------


def test_jobs_lists_approved_work_with_scores(client, config, tmp_path):
    seed(config, tmp_path)
    jobs = client.get("/ext/jobs", headers=AUTH).get_json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["score"] == 82
    assert jobs[0]["reason"] == "strong overlap"
    assert sorted(jobs[0]["documents"]) == ["NOTES.md", "resume.html", "resume.pdf"]


def test_an_untailored_job_lists_no_documents(client, config, tmp_path):
    seed(config, tmp_path, tailored=False)
    jobs = client.get("/ext/jobs", headers=AUTH).get_json()["jobs"]
    assert jobs[0]["documents"] == []


# -- planning ------------------------------------------------------------


def test_plan_fills_a_known_field(client):
    body = {"fields": [field(id="jp-1", label="First Name")]}
    plan = client.post("/ext/plan", json=body, headers=AUTH).get_json()
    action = plan["actions"][0]
    assert action["action"] == "filled"
    assert action["how"] == "text"
    assert action["value"] == "Ada"
    assert plan["filled"] == 1


def test_plan_never_returns_a_password_to_type(client):
    """The extension's refusal is a second line; this is the first."""
    body = {"fields": [
        field(id="jp-1", type="password", label="Password"),
        field(id="jp-2", label="Kennwort"),
    ]}
    plan = client.post("/ext/plan", json=body, headers=AUTH).get_json()
    for action in plan["actions"]:
        assert action["action"] == "skipped"
        assert action["how"] == "none"
        assert action["value"] == ""
        assert action["key"] == "password"


def test_plan_leaves_self_identification_alone(client):
    body = {"fields": [field(id="jp-1", label="Gender")]}
    action = client.post("/ext/plan", json=body, headers=AUTH).get_json()["actions"][0]
    assert action["action"] == "skipped"


def test_plan_reports_an_unknown_field_rather_than_guessing(client):
    body = {"fields": [field(id="jp-1", label="Favourite colour")]}
    action = client.post("/ext/plan", json=body, headers=AUTH).get_json()["actions"][0]
    assert action["action"] == "unmatched"


def test_plan_points_a_file_field_at_a_download(client, config, tmp_path):
    fp = seed(config, tmp_path)
    body = {"job": fp, "fields": [field(id="jp-1", type="file", label="Resume")]}
    action = client.post("/ext/plan", json=body, headers=AUTH).get_json()["actions"][0]
    assert action["how"] == "file"
    assert action["filename"] == "resume.pdf"
    assert action["url"] == f"/ext/file/{fp}/resume.pdf"
    assert action["value"] == "", "the bytes come from the URL, never inline"


def test_plan_says_when_a_job_has_no_resume_yet(client, config, tmp_path):
    fp = seed(config, tmp_path, tailored=False)
    body = {"job": fp, "fields": [field(id="jp-1", type="file", label="Resume")]}
    plan = client.post("/ext/plan", json=body, headers=AUTH).get_json()
    assert "not been tailored" in plan["note"]


def test_plan_rejects_a_body_that_is_not_fields(client):
    assert client.post("/ext/plan", json={"fields": "nope"}, headers=AUTH).status_code == 400


def test_plan_needs_the_token_too(client):
    assert client.post("/ext/plan", json={"fields": []}).status_code == 401


# -- documents -----------------------------------------------------------


def test_a_document_is_served(client, config, tmp_path):
    fp = seed(config, tmp_path)
    resp = client.get(f"/ext/file/{fp}/resume.pdf", headers=AUTH)
    assert resp.status_code == 200
    assert resp.data.startswith(b"%PDF")


def test_the_dashboard_serves_documents_without_a_token(client, config, tmp_path):
    fp = seed(config, tmp_path)
    assert client.get(f"/doc/{fp}/NOTES.md").status_code == 200


@pytest.mark.parametrize("name", [
    "../../config.yaml", "..%2f..%2fconfig.yaml", "secrets.txt", "resume.pdf.bak",
])
def test_only_allowlisted_names_are_served(client, config, tmp_path, name):
    fp = seed(config, tmp_path)
    assert client.get(f"/doc/{fp}/{name}").status_code in (404, 308)


def test_a_document_of_an_unknown_job_is_a_404(client):
    assert client.get("/doc/deadbeefdeadbeef/resume.pdf").status_code == 404


def test_servable_path_refuses_a_name_outside_the_allowlist(config, tmp_path):
    class Row(dict):
        def __getitem__(self, key):
            return dict.get(self, key)

    row = Row(output_dir=str(tmp_path))
    assert extapi.servable_path(row, "config.yaml") is None
    assert extapi.servable_path(row, "../config.yaml") is None
