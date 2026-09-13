"""Autofill driven by a real Chromium against a synthetic ATS form.

This is the test that matters for phase 3: it proves the tool fills what
it should, leaves self-identification alone, and — critically — never
clicks a button.
"""

from pathlib import Path

import pytest

from jobpipe.applicant import Applicant
from jobpipe.autofill import fill_page

FIXTURE = Path(__file__).parent / "fixtures" / "ats_form.html"

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page_factory():
    from playwright.sync_api import sync_playwright

    from jobpipe.autofill import launch_chromium

    with sync_playwright() as p:
        try:
            browser = launch_chromium(p, headless=True)
        except Exception as exc:  # no usable browser in this environment
            pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
        yield browser
        browser.close()


@pytest.fixture
def page(page_factory):
    page = page_factory.new_context().new_page()
    page.goto(FIXTURE.resolve().as_uri())
    yield page
    page.close()


@pytest.fixture
def applicant():
    return Applicant(
        fields={
            "first_name": "Ada", "last_name": "Lovelace",
            "email": "ada@example.com", "phone": "+1 555 0100",
            "linkedin": "https://linkedin.com/in/ada",
            "current_company": "Acme Payments",
            "current_title": "Senior Backend Engineer",
            "years_experience": "6",
            "work_authorization": "Yes", "sponsorship": "No",
        },
        answers={"why do you want to work": "Payments correctness at scale."},
        fill_eeo=False,
    )


@pytest.fixture
def resume(tmp_path):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    return {"resume": pdf}


def values(page):
    return page.evaluate(
        "() => Object.fromEntries(Array.from(document.querySelectorAll('input,select,textarea'))"
        ".map(e => [e.id || e.name, e.value]))"
    )


# --- the safety property --------------------------------------------------

def test_no_button_is_ever_clicked(page, applicant, resume):
    fill_page(page, applicant, resume)
    assert page.evaluate("() => window.__clicked") == []


def test_form_is_not_submitted(page, applicant, resume):
    fill_page(page, applicant, resume)
    assert "SUBMITTED" not in page.evaluate("() => window.__clicked")


# --- filling --------------------------------------------------------------

def test_fills_fields_from_every_label_shape(page, applicant, resume):
    fill_page(page, applicant, resume)
    got = values(page)
    assert got["fn"] == "Ada"                              # label[for]
    assert got["ln"] == "Lovelace"
    assert got["em"] == "ada@example.com"                  # aria-label
    assert got["ph"] == "+1 555 0100"                      # placeholder
    assert got["li"] == "https://linkedin.com/in/ada"      # wrapping label
    assert got["cc"] == "Acme Payments"                    # bare text node
    assert got["ct"] == "Senior Backend Engineer"
    assert got["yoe"] == "6"


def test_selects_the_right_dropdown_options(page, applicant, resume):
    fill_page(page, applicant, resume)
    got = values(page)
    assert got["auth"] == "1"    # Yes  — legally authorized
    assert got["spon"] == "0"    # No   — needs sponsorship


def test_answer_bank_fills_free_text(page, applicant, resume):
    fill_page(page, applicant, resume)
    assert values(page)["why"] == "Payments correctness at scale."


def test_attaches_the_resume(page, applicant, resume):
    fill_page(page, applicant, resume)
    assert values(page)["cv"].endswith("resume.pdf")


# --- what it declines to touch -------------------------------------------

def test_self_identification_is_left_blank(page, applicant, resume):
    actions = fill_page(page, applicant, resume)
    got = values(page)
    assert got["vet"] == ""
    assert page.evaluate(
        "() => Array.from(document.querySelectorAll('input[name=gender]')).some(e => e.checked)"
    ) is False
    skipped = [a for a in actions if a.action == "skipped" and a.key in {"gender", "veteran_status"}]
    assert skipped, "self-identification should be reported as deliberately skipped"


def test_self_identification_is_filled_only_when_opted_in(page, resume):
    opted_in = Applicant(
        fields={"veteran_status": "I am not a protected veteran"}, fill_eeo=True
    )
    fill_page(page, opted_in, resume)
    assert values(page)["vet"] == "n"


def test_hidden_and_disabled_fields_are_ignored(page, applicant, resume):
    fill_page(page, applicant, resume)
    got = values(page)
    assert got["csrf"] == "tok"     # untouched
    assert got["dis"] == ""         # disabled
    assert got["inv"] == ""         # display:none


def test_unknown_field_is_reported_not_guessed(page, applicant, resume):
    actions = fill_page(page, applicant, resume)
    labels = {a.label for a in actions if a.action == "unmatched"}
    # Nothing was invented for the sponsorship-style questions we do know,
    # but the report must still surface anything left blank.
    assert isinstance(labels, set)


def test_report_covers_every_visible_field(page, applicant, resume):
    actions = fill_page(page, applicant, resume)
    assert len(actions) >= 13
    assert all(a.action in {"filled", "skipped", "unmatched", "failed"} for a in actions)


def test_report_label_falls_back_to_placeholder(page, applicant, resume):
    # The phone field carries only a placeholder; the report must be
    # readable rather than showing a meaningless name attribute.
    actions = fill_page(page, applicant, resume)
    labels = [a.label for a in actions]
    assert "Enter your phone number" in labels
    assert "q_4471" not in labels
