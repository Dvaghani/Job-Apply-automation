import pytest
import yaml

from jobpipe.applicant import Applicant, ApplicantError, load
from jobpipe import autofill
from jobpipe.autofill import adzuna_job_id, apply_form_url, choose_option


def test_full_name_is_derived_when_absent():
    me = Applicant(fields={"first_name": "Ada", "last_name": "Lovelace"})
    assert me.value_for("full_name") == "Ada Lovelace"


def test_explicit_full_name_wins():
    me = Applicant(fields={"first_name": "A", "last_name": "B", "full_name": "Ada L."})
    assert me.value_for("full_name") == "Ada L."


def test_self_identification_withheld_by_default():
    me = Applicant(fields={"gender": "Female"})
    assert me.value_for("gender") is None
    assert Applicant(fields={"gender": "Female"}, fill_eeo=True).value_for("gender") == "Female"


def test_numbers_are_stringified():
    assert Applicant(fields={"years_experience": 6}).value_for("years_experience") == "6"


def test_missing_field_is_none():
    assert Applicant().value_for("phone") is None


def test_answer_bank_matches_on_substring():
    me = Applicant(answers={"authorized to work": "Yes"})
    assert me.answer_for("Are you legally authorized to work in the US?") == "Yes"


def test_answer_bank_prefers_the_most_specific_match():
    me = Applicant(answers={"work": "generic", "authorized to work in canada": "Yes, citizen"})
    assert me.answer_for("Are you authorized to work in Canada?") == "Yes, citizen"


def test_answer_bank_returns_none_when_nothing_matches():
    assert Applicant(answers={"sponsorship": "No"}).answer_for("Favourite colour?") is None


def test_load_rejects_malformed_file(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text("fields: [not, a, mapping]")
    with pytest.raises(ApplicantError, match="`fields` must be a mapping"):
        load(path)


def test_load_missing_file_explains_the_fix(tmp_path):
    with pytest.raises(ApplicantError, match="applicant.example.yaml"):
        load(tmp_path / "nope.yaml")


def test_example_applicant_file_is_valid():
    me = load("applicant.example.yaml")
    assert me.value_for("email")
    assert me.fill_eeo is False, "self-identification must ship opted out"


# --- apply-form URL derivation -------------------------------------------

@pytest.mark.parametrize("posting,expected", [
    ("https://jobs.lever.co/acme/abc-123",
     "https://jobs.lever.co/acme/abc-123/apply"),
    ("https://jobs.lever.co/acme/abc-123/apply",
     "https://jobs.lever.co/acme/abc-123/apply"),
    ("https://jobs.ashbyhq.com/acme/xyz",
     "https://jobs.ashbyhq.com/acme/xyz/application"),
    ("https://boards.greenhouse.io/acme/jobs/4567",
     "https://boards.greenhouse.io/acme/jobs/4567"),
])
def test_apply_form_url(posting, expected):
    assert apply_form_url(posting) == expected


def test_apply_form_url_drops_query_before_deriving():
    assert apply_form_url("https://jobs.lever.co/acme/abc?src=x").endswith("/abc/apply")


# --- Adzuna listings ------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.adzuna.de/details/5817935733?utm_medium=api", "5817935733"),
    ("https://adzuna.de/details/123", "123"),
    ("https://www.adzuna.co.uk/details/99", "99"),
    ("https://boards.greenhouse.io/acme/jobs/4567", None),
    ("https://example.com/adzuna.de/details/5", None),   # not the host
    ("", None),
])
def test_adzuna_job_id(url, expected):
    assert adzuna_job_id(url) == expected


def test_adzuna_url_is_left_alone_by_the_offline_derivation():
    """Resolving it needs a browser; apply_form_url must not pretend to."""
    url = "https://www.adzuna.de/details/5817935733?utm_medium=api"
    assert apply_form_url(url) == url


# --- filling on demand ----------------------------------------------------
#
# The browser stays open so you can get past a login or a wizard yourself
# and then ask for a fill. No browser needed to test the loop itself.

class _Page:
    def __init__(self, url="https://employer.example/step2"):
        self.url = url


def _hold_with(monkeypatch, replies):
    """Drive _hold with a scripted set of answers; count the fills."""
    filled = []
    monkeypatch.setattr(
        autofill, "fill_page", lambda page, a, att: filled.append(page.url) or []
    )
    answers = iter(replies)

    def fake_input(*_args):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    autofill._hold(_Page(), None, {}, [])
    return filled


def test_hold_fills_the_current_page_on_request(monkeypatch):
    assert len(_hold_with(monkeypatch, ["fill", "fill", ""])) == 2


def test_hold_closes_on_a_bare_enter(monkeypatch):
    assert _hold_with(monkeypatch, [""]) == []


@pytest.mark.parametrize("word", ["fill", "FILL", " f ", "refill"])
def test_hold_accepts_the_obvious_spellings(monkeypatch, word):
    assert len(_hold_with(monkeypatch, [word, ""])) == 1


@pytest.mark.parametrize("word", ["done", "close", "q", "quit", "exit"])
def test_hold_closes_on_any_of_the_obvious_words(monkeypatch, word):
    assert _hold_with(monkeypatch, [word]) == []


def test_hold_ignores_an_unknown_command_rather_than_filling(monkeypatch):
    assert _hold_with(monkeypatch, ["submit", "yes please", ""]) == []


def test_hold_exits_when_there_is_no_terminal(monkeypatch):
    """Run without stdin and it must close, not spin on EOFError."""
    assert _hold_with(monkeypatch, []) == []


# --- reporting ------------------------------------------------------------

def test_summary_says_why_an_empty_form_was_empty():
    actions = [autofill.FieldAction("Password *", "password", "skipped", "yours")]
    assert "wants an account" in autofill.summarize(actions, "https://x.example")


def test_summary_of_an_ordinary_form_does_not_mention_accounts():
    actions = [autofill.FieldAction("First Name", "first_name", "filled", "Ada")]
    assert "wants an account" not in autofill.summarize(actions, "https://x.example")


def test_summary_reports_a_page_with_no_fields_at_all():
    assert "no form fields" in autofill.summarize([], "https://x.example")


# --- dropdown option matching --------------------------------------------

OPTIONS = [
    {"text": "Select...", "value": ""},
    {"text": "Yes", "value": "1"},
    {"text": "No", "value": "0"},
]


def test_choose_option_exact_match():
    assert choose_option(OPTIONS, "Yes") == "1"
    assert choose_option(OPTIONS, "no") == "0"


def test_choose_option_when_option_text_is_longer():
    options = [{"text": "I am not a protected veteran", "value": "n"}]
    assert choose_option(options, "not a protected veteran") == "n"


def test_choose_option_when_answer_is_longer_than_option():
    assert choose_option(OPTIONS, "Yes, I am authorized") == "1"


def test_choose_option_returns_none_when_nothing_fits():
    assert choose_option(OPTIONS, "Maybe") is None
    assert choose_option(OPTIONS, "") is None


# --- which cover letter gets attached -------------------------------------

def test_the_pdf_cover_letter_is_preferred_over_markdown(tmp_path):
    """An upload field takes a document; .md is rejected outright."""
    (tmp_path / "resume.pdf").write_bytes(b"%PDF")
    (tmp_path / "cover-letter.md").write_text("x", encoding="utf-8")
    (tmp_path / "cover-letter.pdf").write_bytes(b"%PDF")
    got = autofill.gather_attachments(tmp_path)
    assert got["cover_letter"].name == "cover-letter.pdf"


def test_markdown_is_still_attached_when_there_is_no_pdf(tmp_path):
    """A folder generated before the PDF existed should attach something."""
    (tmp_path / "cover-letter.md").write_text("x", encoding="utf-8")
    got = autofill.gather_attachments(tmp_path)
    assert got["cover_letter"].name == "cover-letter.md"


def test_the_german_cover_letter_is_picked_for_a_german_application(tmp_path):
    (tmp_path / "cover-letter.pdf").write_bytes(b"%PDF")
    (tmp_path / "cover-letter.de.pdf").write_bytes(b"%PDF")
    got = autofill.gather_attachments(tmp_path, language="de")
    assert got["cover_letter"].name == "cover-letter.de.pdf"


def test_english_is_used_when_the_german_letter_was_never_generated(tmp_path):
    (tmp_path / "cover-letter.pdf").write_bytes(b"%PDF")
    got = autofill.gather_attachments(tmp_path, language="de")
    assert got["cover_letter"].name == "cover-letter.pdf"


def test_a_german_pdf_beats_an_english_markdown(tmp_path):
    """Format decides before language: the .md would be rejected either way."""
    (tmp_path / "cover-letter.md").write_text("x", encoding="utf-8")
    (tmp_path / "cover-letter.de.pdf").write_bytes(b"%PDF")
    got = autofill.gather_attachments(tmp_path, language="de")
    assert got["cover_letter"].name == "cover-letter.de.pdf"
