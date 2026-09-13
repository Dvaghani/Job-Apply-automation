import json
import sqlite3

import pytest

from jobpipe import db, tailor
from jobpipe.resume import load
from jobpipe.verify import Finding

MASTER = {
    "basics": {"name": "Ada Lovelace", "email": "ada@example.com"},
    "work": [{
        "name": "Acme", "position": "Senior Engineer", "startDate": "2022-03",
        "highlights": ["Built a ledger in Python.", "Mentored 4 engineers."],
    }],
    "skills": [{"name": "Languages", "keywords": ["Python"]}],
}


@pytest.fixture
def master(tmp_path):
    path = tmp_path / "resume.json"
    path.write_text(json.dumps(MASTER))
    return load(path)


def row(**kw):
    base = {
        "fingerprint": "abc123", "company": "Globex Corp.", "title": "Staff Engineer",
        "location": "Remote", "description": "We use Python.", "url": "https://x/1",
    }
    base.update(kw)
    return base


class B:
    def __init__(self, i, text):
        self.source_index, self.text = i, text


class R:
    def __init__(self, i, bullets):
        self.role_index, self.bullets = i, bullets


class T:
    def __init__(self, cover="", keywords=None, gaps=None):
        self.summary = "Backend engineer."
        self.roles = [R(0, [B(0, "Built a Python ledger.")])]
        self.selected_skills = ["Python"]
        self.cover_letter = cover
        self.keywords_matched = keywords or []
        self.gaps = gaps or []


def test_slugify():
    assert tailor.slugify("Globex Corp.") == "globex-corp"
    assert tailor.slugify("Staff Engineer, Platform") == "staff-engineer-platform"
    assert tailor.slugify("!!!") == "untitled"


def test_output_dir_is_readable(tmp_path):
    d = tailor.output_dir(tmp_path, row())
    assert d.name == "globex-corp-staff-engineer"


def test_prompt_exposes_indices_for_citation(master):
    prompt = tailor.build_prompt(master, row(), cover_letter=True)
    assert "[role 0]" in prompt
    assert "[0] Built a ledger in Python." in prompt
    assert "[1] Mentored 4 engineers." in prompt
    assert "Also write the cover letter." in prompt


def test_prompt_can_suppress_cover_letter(master):
    prompt = tailor.build_prompt(master, row(), cover_letter=False)
    assert "Leave cover_letter empty." in prompt


def test_long_description_is_truncated_not_dropped(master):
    prompt = tailor.build_prompt(master, row(description="x" * 20000), cover_letter=False)
    assert "[truncated]" in prompt
    assert len(prompt) < 20000


def test_write_outputs_writes_expected_files(tmp_path, master):
    written = tailor.write_outputs(tmp_path / "app", master, T(cover="Hello."), row(), [])
    names = {p.name for p in written}
    assert names == {"resume.md", "resume.html", "cover-letter.md", "NOTES.md"}


def test_no_cover_letter_file_when_empty(tmp_path, master):
    written = tailor.write_outputs(tmp_path / "app", master, T(cover=""), row(), [])
    assert "cover-letter.md" not in {p.name for p in written}


def test_notes_records_a_clean_verification(tmp_path, master):
    tailor.write_outputs(tmp_path / "app", master, T(), row(), [])
    notes = (tmp_path / "app" / "NOTES.md").read_text()
    assert "No fabrication found" in notes


def test_notes_surfaces_findings_prominently(tmp_path, master):
    findings = [Finding("number", "Acme bullet 0", "asserts 20000000")]
    tailor.write_outputs(tmp_path / "app" / "x", master, T(), row(), findings)
    notes = (tmp_path / "app" / "x" / "NOTES.md").read_text()
    assert "Check each one before sending" in notes
    assert "20000000" in notes


def test_notes_lists_gaps(tmp_path, master):
    tailor.write_outputs(
        tmp_path / "app", master, T(gaps=["5 years Kubernetes"]), row(), []
    )
    assert "5 years Kubernetes" in (tmp_path / "app" / "NOTES.md").read_text()


# --- schema migration -----------------------------------------------------

def test_migration_adds_columns_to_an_existing_database(tmp_path):
    """A database created before phase 2 must survive the upgrade."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.executescript("""
        CREATE TABLE jobs (
            fingerprint TEXT PRIMARY KEY, source TEXT NOT NULL,
            source_id TEXT NOT NULL, company TEXT NOT NULL, title TEXT NOT NULL,
            url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        );
    """)
    old.execute(
        "INSERT INTO jobs VALUES ('fp1','greenhouse','1','Acme','Eng','u','approved','t','t')"
    )
    old.commit()
    old.close()

    conn = db.connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert {"tailored_at", "output_dir"} <= columns
    # The pre-existing row is intact.
    assert db.get(conn, "fp1")["company"] == "Acme"


def test_mark_tailored_records_output_dir(tmp_path):
    from jobpipe.models import Job
    conn = db.connect(tmp_path / "t.db")
    job = Job(source="s", source_id="1", company="Acme", title="Eng", url="u")
    db.upsert_job(conn, job)
    db.mark_tailored(conn, job.fingerprint(), "applications/acme-eng")
    stored = db.get(conn, job.fingerprint())
    assert stored["output_dir"] == "applications/acme-eng"
    assert stored["tailored_at"]


def test_untailored_approved_excludes_already_tailored(tmp_path):
    from jobpipe.models import STATUS_APPROVED, Job
    conn = db.connect(tmp_path / "t.db")
    a = Job(source="s", source_id="1", company="Acme", title="Eng A", url="u")
    b = Job(source="s", source_id="2", company="Acme", title="Eng B", url="u")
    for job in (a, b):
        db.upsert_job(conn, job)
        db.set_status(conn, job.fingerprint(), STATUS_APPROVED)
    db.mark_tailored(conn, a.fingerprint(), "somewhere")
    assert [r["title"] for r in db.untailored_approved(conn)] == ["Eng B"]
