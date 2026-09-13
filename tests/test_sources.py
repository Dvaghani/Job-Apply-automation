"""Parser tests against recorded payload shapes — no network."""

from jobpipe.sources import ashby, greenhouse, lever

GREENHOUSE = {
    "jobs": [
        {
            "id": 4567,
            "title": "Senior Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/4567",
            "location": {"name": "Remote - US"},
            "updated_at": "2026-09-01T10:00:00-04:00",
            "content": "<p>Build &amp; own services.</p>",
        }
    ]
}

LEVER = [
    {
        "id": "abc-123",
        "text": "Staff Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
        "categories": {"location": "Toronto", "team": "Platform"},
        "workplaceType": "hybrid",
        "createdAt": 1757000000000,
        "descriptionPlain": "Own the platform.",
    }
]

ASHBY = {
    "jobs": [
        {
            "id": "xyz-9",
            "title": "Product Engineer",
            "location": "San Francisco",
            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz-9",
            "descriptionPlain": "Ship product.",
            "isRemote": False,
            "isListed": True,
            "publishedAt": "2026-08-20T00:00:00Z",
        },
        {"id": "draft-1", "title": "Hidden", "isListed": False},
    ]
}


def test_greenhouse_parse(monkeypatch):
    monkeypatch.setattr(greenhouse, "fetch_json", lambda *a, **k: GREENHOUSE)
    (job,) = greenhouse.fetch("acme")
    assert job.title == "Senior Backend Engineer"
    assert job.company == "acme"
    assert job.source_id == "4567"
    assert job.is_remote()
    assert job.description == "Build & own services."


def test_lever_parse(monkeypatch):
    monkeypatch.setattr(lever, "fetch_json", lambda *a, **k: LEVER)
    (job,) = lever.fetch("acme")
    assert job.title == "Staff Engineer"
    assert job.url.endswith("abc-123")
    assert job.location == "Toronto"
    assert not job.is_remote()
    assert job.posted_at and job.posted_at.startswith("2025-")


def test_ashby_skips_unlisted(monkeypatch):
    monkeypatch.setattr(ashby, "fetch_json", lambda *a, **k: ASHBY)
    jobs = ashby.fetch("acme")
    assert [j.title for j in jobs] == ["Product Engineer"]


def test_parsers_survive_a_malformed_row(monkeypatch):
    # A row missing its id is skipped, not fatal — one bad record must not
    # cost us the rest of the board.
    payload = {"jobs": [{"title": "No ID here"}, GREENHOUSE["jobs"][0]]}
    monkeypatch.setattr(greenhouse, "fetch_json", lambda *a, **k: payload)
    assert len(greenhouse.fetch("acme")) == 1
