"""Full tailor run with the model call stubbed out.

Covers everything except the API round-trip: prompt build, verification,
rendering, file writing, and the DB status update.
"""

import json

import pytest

from jobpipe import db, tailor
from jobpipe.config import Config
from jobpipe.models import STATUS_APPROVED, Job
from jobpipe.tailor import TailoredBullet, TailoredRole, Tailoring

MASTER = {
    "basics": {"name": "Ada Lovelace", "email": "ada@example.com"},
    "work": [{
        "name": "Acme Payments",
        "position": "Senior Backend Engineer",
        "startDate": "2022-03",
        "highlights": [
            "Built a ledger handling 2,000,000 transactions per day in Python and Postgres.",
            "Mentored 4 engineers.",
        ],
    }],
    "skills": [{"name": "Languages", "keywords": ["Python", "Go"]}],
}

HONEST = Tailoring(
    summary="Backend engineer with payments depth in Python.",
    roles=[TailoredRole(role_index=0, bullets=[
        TailoredBullet(
            source_index=0,
            text="Owned a Postgres ledger processing 2M transactions daily.",
        ),
    ])],
    selected_skills=["Python", "Go"],
    cover_letter="My Python ledger work maps directly onto Globex's payments roadmap.",
    keywords_matched=["Python", "payments"],
    gaps=["No Kubernetes administration"],
)

class _FakeBackend:
    """Stands in for a model backend; tailor_one is stubbed separately."""

    name = "fake"


FABRICATED = Tailoring(
    summary="Backend engineer.",
    roles=[TailoredRole(role_index=0, bullets=[
        TailoredBullet(
            source_index=0,
            # Two lies: the figure is inflated 10x and Kubernetes is invented.
            text="Ran a Kubernetes ledger processing 20M transactions daily.",
        ),
    ])],
    selected_skills=["Python"],
    cover_letter="",
)


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "resume.json").write_text(json.dumps(MASTER))
    config = Config(
        db_path=str(tmp_path / "jobs.db"),
        resume_path=str(tmp_path / "resume.json"),
        output_dir=str(tmp_path / "applications"),
    )
    conn = db.connect(config.db_path)
    job = Job(
        source="greenhouse", source_id="1", company="Globex",
        title="Staff Backend Engineer", url="https://x/1",
        location="Remote", description="We run Python and Postgres at scale.",
    )
    db.upsert_job(conn, job)
    db.set_status(conn, job.fingerprint(), STATUS_APPROVED)
    conn.commit()
    return config, conn, job.fingerprint()


def test_honest_tailoring_writes_clean_output(setup, monkeypatch):
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda cfg: _FakeBackend())

    result = tailor.run(config, conn, [fp])
    assert result == {"tailored": 1, "failed": 0, "flagged": 0}

    out = db.get(conn, fp)["output_dir"]
    from pathlib import Path
    notes = (Path(out) / "NOTES.md").read_text()
    assert "No fabrication found" in notes
    assert "No Kubernetes administration" in notes      # gap surfaced

    resume_md = (Path(out) / "resume.md").read_text()
    assert "2M transactions daily" in resume_md
    assert "Mentored" not in resume_md                  # bullet not selected
    assert (Path(out) / "cover-letter.md").exists()


def test_fabricated_tailoring_is_flagged_not_silently_written(setup, monkeypatch):
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: FABRICATED)
    monkeypatch.setattr(tailor.llm, "build", lambda cfg: _FakeBackend())

    result = tailor.run(config, conn, [fp])
    assert result["flagged"] == 1

    from pathlib import Path
    notes = (Path(db.get(conn, fp)["output_dir"]) / "NOTES.md").read_text()
    assert "Check each one before sending" in notes
    assert "20000000" in notes        # the inflated figure
    assert "Kubernetes" in notes      # the invented technology


def test_tailored_job_leaves_the_work_queue(setup, monkeypatch):
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda cfg: _FakeBackend())
    assert len(db.untailored_approved(conn)) == 1
    tailor.run(config, conn, [fp])
    assert db.untailored_approved(conn) == []


def test_api_failure_leaves_the_job_retryable(setup, monkeypatch):
    from jobpipe.llm import LLMError
    config, conn, fp = setup

    def boom(*a, **k):
        raise LLMError("backend unavailable")

    monkeypatch.setattr(tailor, "tailor_one", boom)
    monkeypatch.setattr(tailor.llm, "build", lambda cfg: _FakeBackend())

    result = tailor.run(config, conn, [fp])
    assert result == {"tailored": 0, "failed": 1, "flagged": 0}
    # Still queued, so the next run picks it up.
    assert len(db.untailored_approved(conn)) == 1


def test_cover_letter_naming_the_employer_is_not_flagged(setup, monkeypatch):
    """The company name is context, not a fabrication — even when the job
    description never repeats it."""
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda cfg: _FakeBackend())
    # The seeded description mentions Python and Postgres but not "Globex".
    assert "Globex" not in db.get(conn, fp)["description"]
    assert tailor.run(config, conn, [fp])["flagged"] == 0


def test_review_queue_shows_tailored_state(setup, monkeypatch):
    from jobpipe.web import create_app
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda cfg: _FakeBackend())

    client = create_app(config).test_client()
    before = client.get("/?status=approved").get_data(as_text=True)
    assert "Not tailored yet" in before

    tailor.run(config, conn, [fp])
    after = client.get("/?status=approved").get_data(as_text=True)
    assert "Tailored" in after
    assert "globex-staff-backend-engineer" in after
