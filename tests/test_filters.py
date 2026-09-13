from datetime import datetime, timedelta, timezone

from jobpipe.config import Filters
from jobpipe.filters import check
from jobpipe.models import Job

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def make(**kw):
    base = dict(source="s", source_id="1", company="Acme", title="Senior Engineer", url="u")
    base.update(kw)
    return Job(**base)


def test_no_filters_passes_everything():
    assert check(make(), Filters()) is None


def test_title_exclude_rejects():
    reason = check(make(title="Engineering Intern"), Filters(title_exclude=["intern"]))
    assert reason and "intern" in reason


def test_title_include_requires_a_match():
    f = Filters(title_include=["engineer"])
    assert check(make(title="Senior Engineer"), f) is None
    assert check(make(title="Product Manager"), f) is not None


def test_remote_only():
    f = Filters(remote_only=True)
    assert check(make(location="Remote"), f) is None
    assert check(make(location="Boston"), f) == "not remote"


def test_location_allowlist_lets_remote_through():
    f = Filters(locations=["toronto"])
    assert check(make(location="Toronto, ON"), f) is None
    assert check(make(location="Remote - Canada"), f) is None
    assert check(make(location="Austin, TX"), f) is not None


def test_salary_floor_uses_top_of_band():
    f = Filters(min_salary=150000)
    assert check(make(salary_min=120000, salary_max=160000), f) is None
    assert check(make(salary_min=90000, salary_max=110000), f) is not None


def test_salary_floor_ignores_unstated_salary():
    # Most postings omit salary; rejecting on absence would empty the pipeline.
    assert check(make(), Filters(min_salary=150000)) is None


def test_max_age_days():
    f = Filters(max_age_days=30)
    fresh = (NOW - timedelta(days=5)).isoformat()
    stale = (NOW - timedelta(days=90)).isoformat()
    assert check(make(posted_at=fresh), f, now=NOW) is None
    assert check(make(posted_at=stale), f, now=NOW) is not None


def test_unparseable_date_is_not_rejected():
    assert check(make(posted_at="whenever"), Filters(max_age_days=30), now=NOW) is None
