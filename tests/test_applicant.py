import pytest
import yaml

from jobpipe.applicant import Applicant, ApplicantError, load
from jobpipe.autofill import apply_form_url, choose_option


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
