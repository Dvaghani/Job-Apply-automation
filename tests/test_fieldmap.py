import pytest

from jobpipe.fieldmap import classify, is_eeo, normalize


def test_normalize_reduces_punctuation():
    assert normalize("First Name *") == "first name"
    assert normalize("e-mail_address") == "e mail address"


@pytest.mark.parametrize("label,expected", [
    ("First Name", "first_name"),
    ("Given name", "first_name"),
    ("Last Name *", "last_name"),
    ("Surname", "last_name"),
    ("Email", "email"),
    ("E-mail Address", "email"),
    ("Phone", "phone"),
    ("Mobile number", "phone"),
    ("LinkedIn Profile", "linkedin"),
    ("GitHub URL", "github"),
    ("Portfolio", "portfolio"),
    ("Personal website", "portfolio"),
    ("Cover Letter", "cover_letter"),
    ("Resume/CV", "resume"),
    ("Current Company", "current_company"),
    ("Current title", "current_title"),
    ("Pronouns", "pronouns"),
])
def test_common_labels(label, expected):
    assert classify(label=label) == expected


def test_specific_beats_general():
    # "Current Company" must not fall through to the bare name pattern,
    # and "First Name" must not be read as a plain name field.
    assert classify(label="Company Name") == "current_company"
    assert classify(label="First Name") == "first_name"
    assert classify(label="Full Name") == "full_name"
    assert classify(label="Name") == "full_name"


def test_sponsorship_is_not_confused_with_authorization():
    sponsorship = "Will you now or in the future require sponsorship for an employment visa?"
    authorization = "Are you legally authorized to work in the United States?"
    assert classify(label=sponsorship) == "sponsorship"
    assert classify(label=authorization) == "work_authorization"


def test_years_of_experience_phrasings():
    assert classify(label="How many years of experience do you have?") == "years_experience"
    assert classify(label="Years of relevant experience") == "years_experience"


def test_label_outranks_a_cryptic_name_attribute():
    # Real ATS forms carry names like "cand_q_4712"; the visible label wins.
    assert classify(label="Current Company", name="cand_q_4712") == "current_company"


def test_falls_back_to_name_then_placeholder():
    assert classify(label="", name="candidate_email") == "email"
    assert classify(label="", name="", placeholder="you@example.com") is None
    assert classify(label="", name="", placeholder="Enter your phone") == "phone"


def test_aria_label_is_used_when_there_is_no_label():
    assert classify(label="", aria_label="LinkedIn profile URL") == "linkedin"


def test_file_input_defaults_to_resume():
    assert classify(label="Attach", field_type="file") == "resume"
    assert classify(label="Upload your CV", field_type="file") == "resume"
    assert classify(label="Cover letter", field_type="file") == "cover_letter"


def test_unrecognized_field_returns_none():
    assert classify(label="What is your favourite colour?") is None
    assert classify(label="") is None


@pytest.mark.parametrize("label", [
    "Gender", "Race", "Hispanic or Latino?", "Protected Veteran Status",
    "Disability Status",
])
def test_self_identification_is_detected_so_it_can_be_skipped(label):
    key = classify(label=label)
    assert is_eeo(key), f"{label!r} classified as {key!r}"


def test_ordinary_fields_are_not_eeo():
    assert not is_eeo(classify(label="Email"))
    assert not is_eeo(None)
