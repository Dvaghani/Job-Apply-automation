from jobpipe import verify
from jobpipe.verify import (
    check_numbers,
    check_vocabulary,
    check_tailoring,
    claim_tokens,
    numbers_in,
)


# --- number normalization -------------------------------------------------

def test_numbers_normalize_magnitude_and_separators():
    assert numbers_in("2,000,000 transactions") == {2_000_000.0}
    assert numbers_in("2M transactions") == {2_000_000.0}
    assert numbers_in("1.5k requests") == {1500.0}
    assert numbers_in("reduced by 38%") == {38.0}


def test_reformatting_a_number_is_not_fabrication():
    # "2,000,000" -> "2M" is the same claim, and must not be flagged.
    source = "Handled 2,000,000 transactions per day."
    assert check_numbers(source, "Scaled to 2M transactions/day.", "w") == []


def test_inflating_a_number_is_caught():
    source = "Handled 2,000,000 transactions per day."
    findings = check_numbers(source, "Handled 20M transactions per day.", "w")
    assert len(findings) == 1
    assert findings[0].kind == "number"


def test_dropping_a_number_is_fine():
    # Omitting a metric is a legitimate editorial choice; adding one is not.
    source = "Cut reconciliation from 4 hours to 6 minutes across 3 regions."
    assert check_numbers(source, "Cut reconciliation from 4 hours to 6 minutes.", "w") == []


# --- vocabulary -----------------------------------------------------------

def test_claim_tokens_finds_technologies():
    found = claim_tokens("Built services on AWS with PostgreSQL and S3")
    assert {"AWS", "PostgreSQL", "S3"} <= found


def test_claim_tokens_ignores_ordinary_sentence_starts():
    # "Built" and "Designed" are capitalized but assert no technology.
    assert claim_tokens("Built a service. Designed the schema.") == set()


def test_inventing_a_technology_is_caught():
    master = "Python, Postgres, Kafka"
    findings = check_vocabulary(master, "Deployed on Kubernetes with Istio", "w")
    assert len(findings) == 1
    assert "Kubernetes" in findings[0].detail and "Istio" in findings[0].detail


def test_technology_present_in_master_passes():
    master = "Python, PostgreSQL, Kafka, AWS"
    assert check_vocabulary(master, "Built on Kafka and AWS", "w") == []


# --- whole-tailoring check ------------------------------------------------

class FakeRole:
    def __init__(self, index, name, highlights):
        self.index, self.name, self.highlights = index, name, highlights


class FakeResume:
    def __init__(self):
        self.roles = [
            FakeRole(0, "Acme", [
                "Built a ledger handling 2,000,000 transactions/day in Python.",
                "Mentored 4 engineers.",
            ])
        ]

    def full_text(self):
        return "Acme Python Postgres 2,000,000 transactions mentored 4 engineers"


class B:
    def __init__(self, source_index, text):
        self.source_index, self.text = source_index, text


class R:
    def __init__(self, role_index, bullets):
        self.role_index, self.bullets = role_index, bullets


class T:
    def __init__(self, roles, summary="", skills=None, cover=""):
        self.roles = roles
        self.summary = summary
        self.selected_skills = skills or []
        self.cover_letter = cover


def test_clean_tailoring_produces_no_findings():
    t = T([R(0, [B(0, "Built a Python ledger processing 2M transactions daily.")])],
          summary="Backend engineer focused on Python.",
          skills=["Python"])
    assert check_tailoring(FakeResume(), t) == []


def test_bad_source_index_is_caught():
    t = T([R(0, [B(9, "Something.")])])
    findings = check_tailoring(FakeResume(), t)
    assert any(f.kind == "index" for f in findings)


def test_unknown_role_index_is_caught():
    t = T([R(7, [B(0, "Something.")])])
    findings = check_tailoring(FakeResume(), t)
    assert any(f.kind == "index" for f in findings)


def test_skill_not_in_master_is_caught():
    t = T([], skills=["Rust"])
    findings = check_tailoring(FakeResume(), t)
    assert any("Rust" in f.detail for f in findings)


def test_cover_letter_may_name_the_employer():
    # The posting is supporting context, so naming the target company is fine.
    t = T([], cover="I would bring my Python work to Globex.")
    findings = check_tailoring(FakeResume(), t, job_text="Globex is hiring")
    assert findings == []


def test_cover_letter_inventing_a_number_is_caught():
    t = T([], cover="I have 15 years of experience.")
    findings = check_tailoring(FakeResume(), t, job_text="")
    assert any(f.kind == "number" for f in findings)


def test_single_capital_technology_is_caught_midsentence():
    # The case the first implementation missed: ordinary proper nouns.
    master = "Python, Postgres, Kafka"
    findings = check_vocabulary(master, "Deployed on Kubernetes and Terraform", "w")
    assert findings and "Kubernetes" in findings[0].detail


def test_sentence_start_technology_still_caught_when_shape_is_distinctive():
    master = "Python, Postgres"
    assert check_vocabulary(master, "AWS Lambda ran the job.", "w")


def test_months_are_not_treated_as_technology():
    assert check_vocabulary("Python", "Shipped in March and April.", "w") == []


# --- German output --------------------------------------------------------
#
# German capitalises every noun, so the positional proper-noun rule marks
# every one of them as an asserted technology. Left alone, a German resume
# buries its real findings under dozens of false ones.

GERMAN_BULLET = (
    "Entwicklung eines interaktiven Python/Plotly-Dashboards zur "
    "Visualisierung von HiL-Testergebnissen für Testläufe und Signalanomalien."
)


def test_german_nouns_are_not_treated_as_technology_claims():
    tokens = verify.claim_tokens(GERMAN_BULLET, language="de")
    for ordinary in ["Visualisierung", "Entwicklung", "Testläufe"]:
        assert ordinary not in tokens


def test_german_still_catches_real_technology_names():
    """A compound hides the technology inside an ordinary noun."""
    tokens = verify.claim_tokens(GERMAN_BULLET, language="de")
    assert "HiL" in tokens                   # from "HiL-Testergebnissen"
    assert "Testergebnissen" not in tokens   # the ordinary half, correctly ignored


def test_a_distinctively_shaped_invention_is_caught_in_a_german_compound():
    findings = verify.check_vocabulary(
        "Python HiL",
        "Aufbau einer PostgreSQL-Datenbank für die Testausführung.",
        "bullet",
        language="de",
    )
    assert findings, "a compound must not hide an invented technology"
    assert "PostgreSQL" in findings[0].detail


def test_german_cannot_catch_an_invention_shaped_like_an_ordinary_noun():
    """The known cost of switching off the positional rule.

    "Kubernetes" is one capital and no digits — indistinguishable in shape
    from any German noun, so with capitalisation carrying no signal there is
    nothing left to catch it. Documented here rather than left as a surprise:
    the number check still holds, and NOTES.md says so on German output.
    """
    findings = verify.check_vocabulary(
        "Python HiL",
        "Aufbau eines Kubernetes-Clusters für die Testausführung.",
        "bullet",
        language="de",
    )
    assert findings == []


def test_english_keeps_the_positional_rule():
    tokens = verify.claim_tokens("Built a ledger with Kubernetes and Postgres.")
    assert "Kubernetes" in tokens
    assert "Postgres" in tokens
    assert "Built" not in tokens            # sentence-initial, ordinary word


def test_a_german_rewrite_does_not_drown_in_false_findings():
    findings = verify.check_vocabulary(
        "Python Plotly HiL ECU-TEST", GERMAN_BULLET, "bullet", language="de"
    )
    assert findings == []


def test_the_same_german_rewrite_would_be_flagged_as_english():
    """Shows the rule being switched off, not merely absent."""
    findings = verify.check_vocabulary(
        "Python Plotly HiL ECU-TEST", GERMAN_BULLET, "bullet", language="en"
    )
    assert findings, "the English rule should flag German nouns — that is the bug"


def test_numbers_are_still_checked_in_german():
    """The weakened vocabulary rule must not weaken the metric check."""
    findings = verify.check_numbers(
        "Verarbeitung von 2.000 Nachrichten.",
        "Verarbeitung von 20.000 Nachrichten.",
        "bullet",
    )
    assert findings
