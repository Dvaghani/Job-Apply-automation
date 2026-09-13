from jobpipe.models import Job, normalize_company, normalize_title, strip_html


def make(**kw):
    base = dict(source="greenhouse", source_id="1", company="Acme", title="Engineer", url="u")
    base.update(kw)
    return Job(**base)


def test_strip_html_removes_tags_and_entities():
    assert strip_html("<p>Hello&nbsp;&amp; welcome</p>") == "Hello & welcome"
    assert strip_html("") == ""


def test_normalize_title_drops_location_noise():
    assert normalize_title("Senior Engineer (Remote)") == "senior engineer"
    assert normalize_title("Backend Engineer - US") == "backend engineer"
    assert normalize_title("  Staff  Engineer ") == "staff engineer"


def test_normalize_company_drops_legal_suffixes():
    assert normalize_company("Acme, Inc.") == "acme"
    assert normalize_company("Acme Corp") == "acme"


def test_fingerprint_collapses_equivalent_postings():
    a = make(company="Acme, Inc.", title="Senior Engineer (Remote)", location="SF")
    b = make(company="Acme Corp", title="Senior Engineer", location="NYC", source="lever")
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_separates_different_roles():
    assert make(title="Backend Engineer").fingerprint() != make(title="Frontend Engineer").fingerprint()


def test_is_remote_reads_location_text():
    assert make(location="Remote - US").is_remote()
    assert not make(location="New York").is_remote()
    assert make(location="New York", remote=True).is_remote()


def test_normalize_title_strips_city_parentheticals():
    # Companies post one role per office with the city in the title; for
    # dedup purposes that is one decision, not five.
    assert normalize_title("Account Executive (Bengaluru, India)") == "account executive"
    assert normalize_title("Account Executive (Berlin, Germany)") == "account executive"


def test_normalize_title_strips_stacked_suffixes():
    assert normalize_title("Engineer (Backend) (Remote)") == "engineer"
    assert normalize_title("Engineer [L5]") == "engineer"


def test_normalize_title_keeps_inner_parentheticals():
    # Only trailing brackets are noise — a mid-title qualifier is signal.
    assert normalize_title("Engineer (Backend) - Payments") == "engineer (backend) - payments"
