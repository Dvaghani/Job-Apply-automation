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

    client = create_app(config, token="t").test_client()
    before = client.get("/review?status=approved").get_data(as_text=True)
    assert "Not tailored yet" in before

    tailor.run(config, conn, [fp])
    after = client.get("/review?status=approved").get_data(as_text=True)
    assert "Tailored" in after
    assert "globex-staff-backend-engineer" in after


def test_apply_without_a_fingerprint_lists_approved_jobs(setup, monkeypatch, capsys):
    """`jobpipe apply` with no argument must be a picker, not an error —
    the fingerprint is otherwise invisible to the user."""
    from jobpipe.cli import main
    config, conn, fp = setup
    cfg = tmp_config(config)

    assert main(["-c", cfg, "apply"]) == 0
    out = capsys.readouterr().out
    assert fp in out
    assert "jobpipe apply" in out
    assert "NOT tailored" in out


def test_apply_lists_tailored_state_after_tailoring(setup, monkeypatch, capsys):
    from jobpipe.cli import main
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda c: _FakeBackend())
    tailor.run(config, conn, [fp])

    assert main(["-c", tmp_config(config), "apply"]) == 0
    out = capsys.readouterr().out
    assert "NOT tailored" not in out
    assert "tailored" in out


def test_apply_with_no_approved_jobs_explains_the_next_step(tmp_path, capsys):
    from jobpipe.cli import main
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"db_path: {tmp_path / 'empty.db'}\nprofile_path: {tmp_path / 'p.md'}\n")
    assert main(["-c", str(cfg), "apply"]) == 0
    assert "jobpipe review" in capsys.readouterr().out


def test_apply_with_an_unknown_fingerprint_points_at_the_list(setup, capsys):
    from jobpipe.cli import main
    config, conn, fp = setup
    assert main(["-c", tmp_config(config), "apply", "deadbeef"]) == 2
    assert "no arguments to list" in capsys.readouterr().err


def tmp_config(config) -> str:
    """Write a config file mirroring the fixture's in-memory Config."""
    from pathlib import Path
    path = Path(config.db_path).parent / "config.yaml"
    path.write_text(
        f"db_path: {config.db_path}\n"
        f"resume_path: {config.resume_path}\n"
        f"output_dir: {config.output_dir}\n"
    )
    return str(path)


# --- rebuilding without paying for the call again ------------------------
#
# Every template fix used to reach existing applications only by re-running
# the model on each one. The decisions are saved now, so rendering changes
# are free to apply.

def test_tailoring_state_is_saved_beside_the_documents(setup, monkeypatch):
    from pathlib import Path
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda c: _FakeBackend())
    tailor.run(config, conn, [fp])

    state = Path(db.get(conn, fp)["output_dir"]) / tailor.state_name("en")
    assert state.exists()
    loaded = tailor.load_states(state.parent)
    assert len(loaded) == 1
    tailoring, language = loaded[0]
    assert tailoring.summary == HONEST.summary
    assert language == "en"


def test_rerender_rebuilds_without_calling_the_model(setup, monkeypatch):
    from pathlib import Path
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda c: _FakeBackend())
    tailor.run(config, conn, [fp])

    resume_md = Path(db.get(conn, fp)["output_dir"]) / "resume.md"
    resume_md.write_text("clobbered", encoding="utf-8")

    def explode(*a, **k):
        raise AssertionError("rerender must not call the model")

    monkeypatch.setattr(tailor, "tailor_one", explode)
    monkeypatch.setattr(tailor.llm, "build", explode)

    assert tailor.rerender(config, conn, [fp])["rerendered"] == 1
    assert "clobbered" not in resume_md.read_text(encoding="utf-8")
    assert HONEST.summary in resume_md.read_text(encoding="utf-8")


def test_rerender_skips_an_application_with_no_saved_state(setup, monkeypatch):
    from pathlib import Path
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda c: _FakeBackend())
    tailor.run(config, conn, [fp])

    # An application tailored before the state file existed.
    (Path(db.get(conn, fp)["output_dir"]) / tailor.state_name("en")).unlink()

    result = tailor.rerender(config, conn, [fp])
    assert result == {"rerendered": 0, "skipped": 1, "failed": 0}


def test_rerender_reports_a_job_that_was_never_tailored(setup):
    config, conn, fp = setup
    assert tailor.rerender(config, conn, [fp])["failed"] == 1


def test_load_states_survives_a_corrupt_file(tmp_path):
    (tmp_path / tailor.state_name("en")).write_text("{not json", encoding="utf-8")
    assert tailor.load_states(tmp_path) == []
    assert tailor.load_states(tmp_path / "nope") == []


def test_each_language_keeps_its_own_saved_tailoring(setup, tmp_path):
    """Both languages share a folder; a German run must not overwrite the
    English decisions."""
    from jobpipe.resume import load as load_resume
    config, conn, fp = setup
    resume = load_resume(config.resume_path)
    row = db.get(conn, fp)
    directory = tmp_path / "app"

    tailor.write_outputs(directory, resume, HONEST, row, [], language="en")
    tailor.write_outputs(directory, resume, FABRICATED, row, [], language="de")

    assert (directory / tailor.state_name("en")).exists()
    assert (directory / tailor.state_name("de")).exists()
    by_language = {lang: t for t, lang in tailor.load_states(directory)}
    assert set(by_language) == {"en", "de"}
    assert by_language["en"].summary == HONEST.summary
    assert by_language["de"].summary == FABRICATED.summary


# --- removing tailored output --------------------------------------------

def test_untailor_deletes_the_folder_and_requeues_the_job(setup, monkeypatch):
    from pathlib import Path
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda c: _FakeBackend())
    tailor.run(config, conn, [fp])

    directory = Path(db.get(conn, fp)["output_dir"])
    assert directory.is_dir()

    result = tailor.untailor(config, conn, [fp])
    assert result == {"removed": 1, "cleared": 1, "failed": 0}
    assert not directory.exists()

    row = db.get(conn, fp)
    assert row["tailored_at"] is None and row["output_dir"] is None
    # The job itself survives: same status, back in the tailoring queue.
    assert row["status"] == STATUS_APPROVED
    assert [r["fingerprint"] for r in db.untailored_approved(conn)] == [fp]


def test_untailor_leaves_the_score_and_status_alone(setup, monkeypatch):
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda c: _FakeBackend())
    tailor.run(config, conn, [fp])

    before = db.get(conn, fp)
    tailor.untailor(config, conn, [fp])
    after = db.get(conn, fp)
    assert (after["status"], after["score"]) == (before["status"], before["score"])


def test_untailor_refuses_a_path_outside_the_output_folder(setup, tmp_path, monkeypatch):
    """A hand-edited or corrupt row must not be able to delete anything it
    likes."""
    config, conn, fp = setup
    outside = tmp_path / "not-mine"
    outside.mkdir()
    (outside / "keep.txt").write_text("important", encoding="utf-8")
    db.mark_tailored(conn, fp, str(outside))
    conn.commit()

    result = tailor.untailor(config, conn, [fp])
    assert result["failed"] == 1
    assert result["removed"] == 0
    assert (outside / "keep.txt").exists()
    # Refused, so the row is left as it was rather than half-cleared.
    assert db.get(conn, fp)["output_dir"] is not None


def test_untailor_reports_an_unknown_fingerprint(setup):
    config, conn, _ = setup
    assert tailor.untailor(config, conn, ["deadbeef"])["failed"] == 1


def test_untailor_clears_a_row_whose_folder_is_already_gone(setup, monkeypatch):
    import shutil
    from pathlib import Path
    config, conn, fp = setup
    monkeypatch.setattr(tailor, "tailor_one", lambda *a, **k: HONEST)
    monkeypatch.setattr(tailor.llm, "build", lambda c: _FakeBackend())
    tailor.run(config, conn, [fp])

    shutil.rmtree(Path(db.get(conn, fp)["output_dir"]))
    result = tailor.untailor(config, conn, [fp])
    assert result["cleared"] == 1 and result["failed"] == 0
    assert db.get(conn, fp)["tailored_at"] is None
