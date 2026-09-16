import json
import sqlite3

import pytest

from jobpipe import db, tailor
from jobpipe.resume import load
from jobpipe.verify import Finding

# Captured before the autouse stub below replaces it, so the one test that
# exercises the real renderer still can.
REAL_WRITE_PDF = tailor.write_pdf
REAL_WRITE_LATEX_PDF = tailor.write_latex_pdf

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


@pytest.fixture(autouse=True)
def fake_pdf(monkeypatch):
    """Stand in for both PDF renders.

    write_outputs produces the PDF too, so the folder is upload-ready
    without waiting for a form to ask. The LaTeX route needs pdflatex and
    the HTML fallback needs a browser; neither is what this module is
    about, and neither should decide whether these tests pass.
    """
    def render(html_path, pdf_path):
        pdf_path.write_bytes(b"%PDF-1.4 stub")
        return pdf_path, 1

    def compile_tex(tex_path):
        pdf_path = tex_path.with_suffix(".pdf")
        pdf_path.write_bytes(b"%PDF-1.4 stub")
        return pdf_path, 1

    monkeypatch.setattr(tailor, "write_pdf", render)
    monkeypatch.setattr(tailor, "write_latex_pdf", compile_tex)


def test_write_outputs_writes_expected_files(tmp_path, master):
    written = tailor.write_outputs(tmp_path / "app", master, T(cover="Hello."), row(), [])
    names = {p.name for p in written}
    assert names == {
        "resume.md", "resume.html", "resume.tex", "resume.pdf",
        "cover-letter.md", "cover-letter.html", "cover-letter.pdf",
        "NOTES.md",
    }


def test_a_pdf_is_ready_without_waiting_for_a_form(tmp_path, master):
    """Forms want a PDF, and so does anyone uploading by hand."""
    tailor.write_outputs(tmp_path / "app", master, T(), row(), [])
    assert (tmp_path / "app" / "resume.pdf").is_file()


def test_a_failed_pdf_render_does_not_fail_the_tailoring(tmp_path, monkeypatch):
    """No browser installed is a warning, not a lost tailoring run."""
    from jobpipe import autofill

    def explode(html_path, pdf_path):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(autofill, "html_to_pdf", explode)
    assert REAL_WRITE_PDF(tmp_path / "resume.html", tmp_path / "resume.pdf") == (None, 0)


def test_write_outputs_keeps_going_when_there_is_no_pdf(tmp_path, master, monkeypatch):
    monkeypatch.setattr(tailor, "write_pdf", lambda html, pdf: (None, 0))
    monkeypatch.setattr(tailor, "write_latex_pdf", lambda tex: (None, 0))
    written = tailor.write_outputs(tmp_path / "app", master, T(), row(), [])
    names = {p.name for p in written}
    assert "resume.md" in names
    assert "resume.pdf" not in names


# --- German output --------------------------------------------------------

def test_german_output_gets_its_own_filenames(tmp_path, master):
    """Both languages coexist; the second run must not overwrite the first."""
    written = tailor.write_outputs(
        tmp_path / "app", master, T(cover="Hallo."), row(), [], language="de"
    )
    names = {p.name for p in written}
    assert names == {
        "resume.de.md", "resume.de.html", "resume.de.tex", "resume.de.pdf",
        "cover-letter.de.md", "cover-letter.de.html", "cover-letter.de.pdf",
        "NOTES.de.md",
    }


def test_both_languages_can_live_side_by_side(tmp_path, master):
    tailor.write_outputs(tmp_path / "app", master, T(cover="Hello."), row(), [])
    tailor.write_outputs(
        tmp_path / "app", master, T(cover="Hallo."), row(), [], language="de"
    )
    present = {p.name for p in (tmp_path / "app").iterdir()}
    assert {"resume.md", "resume.de.md"} <= present


def test_german_prompt_asks_for_german(master):
    prompt = tailor.build_prompt(master, row(), cover_letter=True, language="de")
    assert "in German" in prompt
    assert "Lebenslauf" in prompt


def test_english_prompt_says_english_explicitly(master):
    """Left unsaid, the model follows the posting and writes German for a
    German job. Naming the language is what stops that."""
    prompt = tailor.build_prompt(master, row(), cover_letter=True, language="en")
    assert "in English" in prompt
    assert "even when the posting" in prompt
    assert "in German" not in prompt


def test_an_unknown_language_falls_back_to_english(master):
    prompt = tailor.build_prompt(master, row(), cover_letter=True, language="xx")
    assert "in English" in prompt


def test_suffix_for_language():
    assert tailor.suffix_for("en") == ""
    assert tailor.suffix_for("") == ""
    assert tailor.suffix_for("de") == ".de"


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


def test_the_cover_letter_is_uploadable_not_just_readable(tmp_path, master):
    """An ATS file field rejects .md, so the letter needs a document too."""
    letter = "Dear team,\n\nHello."
    tailor.write_outputs(tmp_path / "app", master, T(cover=letter), row(), [])
    assert (tmp_path / "app" / "cover-letter.pdf").is_file()


def test_the_cover_letter_html_carries_a_letterhead(tmp_path, master):
    letter = "Dear team,\n\nHello."
    tailor.write_outputs(tmp_path / "app", master, T(cover=letter), row(), [])
    html = (tmp_path / "app" / "cover-letter.html").read_text(encoding="utf-8")
    assert "Ada Lovelace" in html
    assert "Globex Corp." in html                 # addressed to the employer
    assert "Application for Staff Engineer" in html
    assert "<p>Dear team,</p>" in html            # body kept verbatim


def test_the_prompt_asks_for_a_complete_letter(master):
    prompt = tailor.SYSTEM
    assert "salutation" in prompt
    assert "sign-off" in prompt


# --- the LaTeX resume -----------------------------------------------------

def test_the_tex_source_ships_next_to_the_pdf(tmp_path, master):
    """The .tex is the source of the PDF, and the thing you hand-edit."""
    tailor.write_outputs(tmp_path / "app", master, T(), row(), [])
    tex = (tmp_path / "app" / "resume.tex").read_text()
    assert r"\begin{document}" in tex
    assert "Ada Lovelace" in tex


def test_the_headshot_is_copied_in_beside_the_tex(tmp_path, master):
    """Copied, not linked: the folder has to compile after being moved."""
    photo = tmp_path / "me.png"
    photo.write_bytes(b"\x89PNG stub")
    tailor.write_outputs(tmp_path / "app", master, T(), row(), [], photo=photo)
    assert (tmp_path / "app" / "photo.png").read_bytes() == b"\x89PNG stub"
    assert r"\IfFileExists{photo.png}" in (tmp_path / "app" / "resume.tex").read_text()


def test_a_missing_headshot_is_a_warning_not_a_failure(tmp_path, master, caplog):
    written = tailor.write_outputs(
        tmp_path / "app", master, T(), row(), [], photo=tmp_path / "nope.jpg"
    )
    assert (tmp_path / "app" / "resume.tex").is_file()
    assert {p.name for p in written} & {"resume.pdf"}
    assert "placeholder" in caplog.text


def test_a_headshot_pdflatex_cannot_read_is_not_copied(tmp_path, master, caplog):
    photo = tmp_path / "me.heic"
    photo.write_bytes(b"stub")
    tailor.write_outputs(tmp_path / "app", master, T(), row(), [], photo=photo)
    assert not (tmp_path / "app" / "me.heic").exists()
    assert not (tmp_path / "app" / "photo.heic").exists()
    assert "pdflatex can include" in caplog.text


def test_no_headshot_configured_still_names_one(tmp_path, master):
    """The template draws a placeholder box for a file that isn't there."""
    assert tailor.copy_photo(None, tmp_path) == "photo.jpg"
    assert tailor.copy_photo("", tmp_path) == "photo.jpg"


def test_the_pdf_comes_from_latex_when_it_compiles(tmp_path, master, monkeypatch):
    """The HTML render is the fallback, not the default."""
    calls = []
    monkeypatch.setattr(
        tailor, "write_pdf",
        lambda html, pdf: calls.append("html") or (pdf, 1),
    )
    tailor.write_outputs(tmp_path / "app", master, T(), row(), [])
    assert calls == []


def test_a_machine_without_tex_falls_back_to_the_html_render(tmp_path, master, monkeypatch):
    monkeypatch.setattr(tailor, "write_latex_pdf", lambda tex: (None, 0))
    written = tailor.write_outputs(tmp_path / "app", master, T(), row(), [])
    assert (tmp_path / "app" / "resume.pdf").is_file()
    assert "resume.tex" in {p.name for p in written}


def test_missing_pdflatex_is_a_warning_not_an_exception(tmp_path, monkeypatch, caplog):
    def no_binary(*args, **kw):
        raise FileNotFoundError("pdflatex")

    monkeypatch.setattr(tailor.subprocess, "run", no_binary)
    tex = tmp_path / "resume.tex"
    tex.write_text(r"\documentclass{article}\begin{document}x\end{document}")
    assert REAL_WRITE_LATEX_PDF(tex) == (None, 0)
    assert "pdflatex not found" in caplog.text
