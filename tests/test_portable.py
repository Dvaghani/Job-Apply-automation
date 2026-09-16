"""Moving a working setup to another machine.

Two things go wrong when a setup is copied by hand, and both are silent.
SQLite keeps recent writes in a `-wal` file beside the database — routinely
larger than the database itself — so copying `jobs.db` alone loses them. And
a Windows path stored in that database is not a path on Linux, which turns
every tailored job back into an untailored one.
"""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from jobpipe import db, extapi, portable
from jobpipe.config import Config
from jobpipe.models import Job


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A working setup: config, profile, masters, answers, database, output."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("min_score: 60\n", encoding="utf-8")
    (tmp_path / "profile.md").write_text("Background.", encoding="utf-8")
    (tmp_path / "resume.json").write_text('{"basics": {}}', encoding="utf-8")
    (tmp_path / "resume.de.json").write_text('{"basics": {}}', encoding="utf-8")
    (tmp_path / "applicant.yaml").write_text("fields: {}\n", encoding="utf-8")
    (tmp_path / ".jobpipe-token").write_text("secret\n", encoding="utf-8")

    folder = tmp_path / "applications" / "acme-engineer"
    folder.mkdir(parents=True)
    (folder / "resume.pdf").write_bytes(b"%PDF")
    (folder / "NOTES.md").write_text("notes", encoding="utf-8")

    conn = db.connect(tmp_path / "jobs.db")
    job = Job(source="greenhouse", source_id="1", company="Acme",
              title="Engineer", url="https://e.example")
    db.upsert_job(conn, job)
    conn.commit()
    conn.close()

    return Config(
        db_path="jobs.db",
        path="config.yaml",
        profile_path="profile.md",
        resume_path="resume.json",
        applicant_path="applicant.yaml",
        output_dir="applications",
    )


def names(archive: Path) -> set[str]:
    with zipfile.ZipFile(archive) as bundle:
        return set(bundle.namelist())


# -- what goes in the bundle -----------------------------------------------


def test_everything_personal_is_collected(setup, tmp_path):
    archive, _ = portable.export(setup, tmp_path / "out.zip")
    got = names(archive)
    for expected in [
        "config.yaml", "profile.md", "resume.json", "applicant.yaml", "jobs.db",
        "applications/acme-engineer/resume.pdf",
        "applications/acme-engineer/NOTES.md",
    ]:
        assert expected in got, expected


def test_per_language_masters_come_too(setup, tmp_path):
    """A German setup is not portable without its German master."""
    archive, _ = portable.export(setup, tmp_path / "out.zip")
    assert "resume.de.json" in names(archive)


def test_the_extension_token_is_left_behind(setup, tmp_path):
    """A secret is better regenerated than carried around."""
    archive, _ = portable.export(setup, tmp_path / "out.zip")
    assert ".jobpipe-token" not in names(archive)


def test_the_bundle_explains_itself(setup, tmp_path):
    archive, _ = portable.export(setup, tmp_path / "out.zip")
    with zipfile.ZipFile(archive) as bundle:
        readme = bundle.read("WHERE-THIS-CAME-FROM.txt").decode()
    assert "git clone" in readme
    assert "playwright install chromium" in readme


def test_a_missing_extension_is_added(setup, tmp_path):
    archive, _ = portable.export(setup, tmp_path / "out")
    assert archive.suffix == ".zip"


def test_exporting_from_the_wrong_folder_says_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    empty = Config(db_path="nope.db", path="nope.yaml", profile_path="nope.md",
                   resume_path="nope.json", applicant_path="nope.yaml",
                   output_dir="nope")
    with pytest.raises(FileNotFoundError, match="nothing to export"):
        portable.export(empty, tmp_path / "out.zip")


# -- the write-ahead log ---------------------------------------------------


def test_the_database_in_the_bundle_holds_the_latest_writes(setup, tmp_path):
    """The trap this command exists for: uncheckpointed writes live in the
    -wal file, and copying jobs.db alone loses them."""
    conn = db.connect("jobs.db")
    for i in range(50):
        db.upsert_job(conn, Job(source="s", source_id=str(i), company=f"Co{i}",
                                title=f"Engineer {i}", url="u"))
    conn.commit()
    # Deliberately not closed: this is the state a running dashboard leaves.

    archive, _ = portable.export(setup, tmp_path / "out.zip")

    extracted = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extract("jobs.db", extracted)

    moved = sqlite3.connect(extracted / "jobs.db")
    assert moved.execute("select count(*) from jobs").fetchone()[0] == 51
    conn.close()


def test_checkpoint_empties_the_log(setup):
    conn = db.connect("jobs.db")
    for i in range(50):
        db.upsert_job(conn, Job(source="s", source_id=str(i), company=f"Co{i}",
                                title=f"Engineer {i}", url="u"))
    conn.commit()
    conn.close()

    wal = Path("jobs.db-wal")
    portable.checkpoint(Path("jobs.db"))
    assert not wal.exists() or wal.stat().st_size == 0


# -- paths that survive the move -------------------------------------------


def test_a_tailored_path_is_stored_with_forward_slashes(tmp_path):
    """Stored as a Windows path, it is not a path at all on Linux."""
    conn = db.connect(tmp_path / "jobs.db")
    job = Job(source="s", source_id="1", company="Acme", title="Engineer", url="u")
    db.upsert_job(conn, job)
    db.mark_tailored(conn, job.fingerprint(), str(Path("applications") / "acme-eng"))
    conn.commit()
    stored = db.get(conn, job.fingerprint())["output_dir"]
    assert "\\" not in stored
    assert stored == "applications/acme-eng"


def test_a_windows_path_already_in_the_database_still_resolves(tmp_path, monkeypatch):
    """Existing rows were written before this; they must not break."""
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "applications" / "acme-eng"
    folder.mkdir(parents=True)

    row = {"output_dir": "applications\\acme-eng"}
    found = extapi.job_folder(row)
    assert found is not None
    assert found.resolve() == folder.resolve()


def test_an_untailored_job_has_no_folder():
    assert extapi.job_folder({"output_dir": None}) is None
    assert extapi.job_folder(None) is None


def test_the_headshot_travels_with_the_setup(tmp_path, monkeypatch):
    """Without it the new machine's resumes lose the photo, silently."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "photo.jpg").write_bytes(b"stub")
    cfg = Config(path="config.yaml", photo_path="photo.jpg", db_path="jobs.db")
    assert Path("photo.jpg") in portable.collect(cfg)
