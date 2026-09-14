"""The German sources, against recorded payload shapes — no network.

Shapes captured from the live endpoints, including the parts the written
documentation gets wrong: the search response is `ergebnisliste` with
`stellenangebotsTitel` and `firma`, and details arrive under
`stellenangebotsBeschreibung` with `externeURL` (capitalised URL).
"""

from __future__ import annotations

import base64

import pytest

from jobpipe.sources import arbeitnow, arbeitsagentur, germantechjobs
from jobpipe.sources.base import NotFound, SourceError

# --- Bundesagentur für Arbeit ---------------------------------------------

BA_SEARCH = {
    "maxErgebnisse": 253,
    "page": 1,
    "size": 25,
    "ergebnisliste": [
        {
            "referenznummer": "10001-1003683246-S",
            "stellenangebotsTitel": "Java Softwareentwickler (m/w/d)",
            "firma": "eg factory GmbH",
            "datumErsteVeroeffentlichung": "2026-09-10",
            "homeofficemoeglich": True,
            "stellenlokationen": [
                {
                    "adresse": {
                        "strasse": "Straße der Nationen",
                        "plz": "09111",
                        "ort": "Chemnitz, Sachsen",
                        "region": "SACHSEN",
                    }
                }
            ],
        }
    ],
}

BA_DETAIL = {
    "referenznummer": "10001-1003683246-S",
    "firma": "eg factory GmbH",
    "stellenangebotsBeschreibung": "<p>Unser Unternehmen: Wir bauen &amp; testen.</p>",
    "externeURL": "https://egfactory.example/karriere/java",
}


def test_search_parses_the_v6_shape():
    job = arbeitsagentur._parse(BA_SEARCH["ergebnisliste"][0])
    assert job.source == "arbeitsagentur"
    assert job.title == "Java Softwareentwickler (m/w/d)"
    assert job.company == "eg factory GmbH"
    assert job.location == "Chemnitz, Sachsen"
    assert job.posted_at == "2026-09-10"
    assert job.is_remote() is True


def test_a_search_result_alone_has_no_description():
    """Which is exactly why fetching one costs a second request."""
    assert arbeitsagentur._parse(BA_SEARCH["ergebnisliste"][0]).description == ""


def test_details_supply_the_description_and_the_employer_link():
    job = arbeitsagentur._parse(BA_SEARCH["ergebnisliste"][0], BA_DETAIL)
    assert "Wir bauen & testen" in job.description
    assert job.url == "https://egfactory.example/karriere/java"


def test_without_an_external_url_it_links_to_the_register():
    detail = {k: v for k, v in BA_DETAIL.items() if k != "externeURL"}
    job = arbeitsagentur._parse(BA_SEARCH["ergebnisliste"][0], detail)
    assert job.url.endswith("/jobdetail/10001-1003683246-S")


def test_a_missing_employer_does_not_lose_the_job():
    item = {k: v for k, v in BA_SEARCH["ergebnisliste"][0].items() if k != "firma"}
    assert arbeitsagentur._parse(item).company == "unbekannt"


def test_the_reference_number_is_base64_for_the_detail_endpoint():
    encoded = arbeitsagentur.encode_reference("10001-1003683246-S")
    assert base64.b64decode(encoded).decode() == "10001-1003683246-S"


def test_staffing_agencies_are_excluded_by_default(monkeypatch):
    seen = {}

    def fake(url, params=None, headers=None):
        seen.update(params)
        return BA_SEARCH

    monkeypatch.setattr(arbeitsagentur, "fetch_json", fake)
    arbeitsagentur.search("Softwareentwickler", where="Chemnitz", radius_km=100)
    assert seen["zeitarbeit"] == "false"
    assert seen["pav"] == "false"
    assert seen["angebotsart"] == arbeitsagentur.EMPLOYMENT
    assert seen["umkreis"] == 100


def test_staffing_agencies_can_be_let_back_in(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        arbeitsagentur, "fetch_json",
        lambda url, params=None, headers=None: (seen.update(params), BA_SEARCH)[1],
    )
    arbeitsagentur.search("x", exclude_staffing=False)
    assert "zeitarbeit" not in seen


def test_the_api_key_is_sent(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        arbeitsagentur, "fetch_json",
        lambda url, params=None, headers=None: (seen.update(headers or {}), BA_SEARCH)[1],
    )
    arbeitsagentur.search("x")
    assert seen["X-API-Key"] == arbeitsagentur.API_KEY


# --- the expensive part: skipping details the filters would reject --------

def _stub_ba(monkeypatch, detail_calls):
    def fake_json(url, params=None, headers=None):
        if url == arbeitsagentur.SEARCH:
            return BA_SEARCH
        detail_calls.append(url)
        return BA_DETAIL

    monkeypatch.setattr(arbeitsagentur, "fetch_json", fake_json)
    monkeypatch.setattr(arbeitsagentur.time, "sleep", lambda *_: None)


def test_a_rejected_title_never_costs_a_detail_request(monkeypatch):
    """The pipeline's own rule, one stage earlier: a job the filters throw
    out should not be paid for first."""
    calls: list[str] = []
    _stub_ba(monkeypatch, calls)
    jobs = arbeitsagentur.fetch(["Softwareentwickler"], keep=lambda job: False)
    assert calls == []
    assert len(jobs) == 1              # still reported, just without a description
    assert jobs[0].description == ""


def test_a_kept_title_does_fetch_its_description(monkeypatch):
    calls: list[str] = []
    _stub_ba(monkeypatch, calls)
    jobs = arbeitsagentur.fetch(["Softwareentwickler"], keep=lambda job: True)
    assert len(calls) == 1
    assert "Wir bauen & testen" in jobs[0].description


def test_details_are_capped_per_run(monkeypatch):
    calls: list[str] = []
    _stub_ba(monkeypatch, calls)
    arbeitsagentur.fetch(["Softwareentwickler"], max_details=0)
    assert calls == []


def test_a_filled_posting_is_skipped_not_fatal(monkeypatch):
    def fake_json(url, params=None, headers=None):
        if url == arbeitsagentur.SEARCH:
            return BA_SEARCH
        raise NotFound("gone")

    monkeypatch.setattr(arbeitsagentur, "fetch_json", fake_json)
    monkeypatch.setattr(arbeitsagentur.time, "sleep", lambda *_: None)
    assert arbeitsagentur.fetch(["x"]) == []


def test_a_detail_failure_keeps_the_job_without_its_description(monkeypatch):
    def fake_json(url, params=None, headers=None):
        if url == arbeitsagentur.SEARCH:
            return BA_SEARCH
        raise SourceError("503")

    monkeypatch.setattr(arbeitsagentur, "fetch_json", fake_json)
    monkeypatch.setattr(arbeitsagentur.time, "sleep", lambda *_: None)
    jobs = arbeitsagentur.fetch(["x"])
    assert len(jobs) == 1
    assert jobs[0].description == ""


def test_the_same_job_across_two_queries_is_returned_once(monkeypatch):
    calls: list[str] = []
    _stub_ba(monkeypatch, calls)
    jobs = arbeitsagentur.fetch(["Softwareentwickler", "Python"])
    assert len(jobs) == 1


# --- Arbeitnow ------------------------------------------------------------

ARBEITNOW = {
    "data": [
        {
            "slug": "backend-engineer-acme-123",
            "title": "Backend Engineer (m/w/d)",
            "company_name": "Acme GmbH",
            "location": "Chemnitz, Germany",
            "url": "https://www.arbeitnow.com/jobs/companies/acme/backend",
            "description": "<p>Python &amp; Postgres.</p>",
            "remote": True,
            "created_at": 1757000000,
            "tags": ["python"],
            "job_types": ["full-time"],
        },
        {
            "slug": "schweissfachmann-456",
            "title": "Schweißfachmann (m/w/d)",
            "company_name": "Kandziora",
            "location": "Niederlangen",
            "url": "https://www.arbeitnow.com/jobs/companies/k/schweiss",
            "description": "Schweißen.",
            "remote": False,
            "created_at": 1757000000,
            "tags": [],
            "job_types": ["full-time"],
        },
    ],
    "links": {"next": None},
}


def test_arbeitnow_parses_a_posting():
    job = arbeitnow._parse(ARBEITNOW["data"][0])
    assert job.company == "Acme GmbH"
    assert job.location == "Chemnitz, Germany"
    assert "Python & Postgres" in job.description
    assert job.posted_at == "2025-09-04T15:33:20+00:00"   # Unix -> ISO, UTC


def test_arbeitnow_keywords_drop_the_unrelated_trades(monkeypatch):
    """It is a general board — a welder shares the page with the engineers."""
    monkeypatch.setattr(
        arbeitnow, "fetch_json", lambda url, params=None, headers=None: ARBEITNOW
    )
    jobs = arbeitnow.fetch(keywords=["python", "engineer"])
    assert [j.title for j in jobs] == ["Backend Engineer (m/w/d)"]


def test_arbeitnow_location_filter(monkeypatch):
    monkeypatch.setattr(
        arbeitnow, "fetch_json", lambda url, params=None, headers=None: ARBEITNOW
    )
    jobs = arbeitnow.fetch(location_contains=["germany"])
    assert len(jobs) == 1


def test_arbeitnow_without_filters_takes_everything(monkeypatch):
    monkeypatch.setattr(
        arbeitnow, "fetch_json", lambda url, params=None, headers=None: ARBEITNOW
    )
    assert len(arbeitnow.fetch()) == 2


def test_arbeitnow_survives_a_bad_timestamp():
    item = dict(ARBEITNOW["data"][0], created_at="not-a-time")
    assert arbeitnow._parse(item).posted_at is None


# --- GermanTechJobs -------------------------------------------------------

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>GermanTechJobs</title>
  <item>
    <title><![CDATA[Softwareentwickler:in (m/w/d) @ eco COMPLIANCE GmbH [48.000 - 78.000 EUR]]]></title>
    <link>https://germantechjobs.de/jobs/eco-softwareentwickler</link>
    <guid isPermaLink="false">https://germantechjobs.de/jobs/eco-softwareentwickler</guid>
    <pubDate>Mon, 14 Sep 2026 08:30:45 GMT</pubDate>
    <description><![CDATA[<p><b>Salary</b></p><ul><li>PHP 8.x und PostgreSQL.</li></ul>]]></description>
  </item>
  <item>
    <title><![CDATA[Data Engineer @ Beispiel AG]]></title>
    <link>https://germantechjobs.de/jobs/beispiel-data</link>
    <guid isPermaLink="false">https://germantechjobs.de/jobs/beispiel-data</guid>
    <pubDate>Sun, 13 Sep 2026 09:00:00 GMT</pubDate>
    <description><![CDATA[Spark und Airflow.]]></description>
  </item>
</channel></rss>
"""


@pytest.fixture
def rss(monkeypatch):
    monkeypatch.setattr(
        germantechjobs, "fetch_text", lambda url, params=None, headers=None: RSS
    )


def test_the_title_yields_role_company_and_salary():
    title, company, band = germantechjobs.split_title(
        "Softwareentwickler:in (m/w/d) @ eco COMPLIANCE GmbH [48.000 - 78.000 EUR]"
    )
    assert title == "Softwareentwickler:in (m/w/d)"
    assert company == "eco COMPLIANCE GmbH"
    assert band == (48000, 78000)


def test_a_title_without_a_salary_still_yields_the_company():
    title, company, band = germantechjobs.split_title("Data Engineer @ Beispiel AG")
    assert (title, company, band) == ("Data Engineer", "Beispiel AG", (None, None))


def test_an_unexpected_title_is_kept_whole_rather_than_guessed_at():
    title, company, _ = germantechjobs.split_title("Just Some Title")
    assert title == "Just Some Title"
    assert company == ""


def test_small_numbers_are_not_mistaken_for_a_salary():
    """A stray "4" would otherwise be acted on by the salary floor filter."""
    assert germantechjobs.parse_salary("PHP 8.x, 4 Tage") == (None, None)


def test_the_feed_parses(rss):
    jobs = germantechjobs.fetch()
    assert [j.company for j in jobs] == ["eco COMPLIANCE GmbH", "Beispiel AG"]
    assert jobs[0].salary_min == 48000
    assert jobs[0].salary_max == 78000
    assert "PHP 8.x" in jobs[0].description
    assert jobs[0].posted_at.startswith("2026-09-14")


def test_the_feed_publishes_no_location(rss):
    """Documented, because a `locations:` filter would then reject all of it."""
    assert all(job.location == "" for job in germantechjobs.fetch())


def test_feed_keywords_narrow_it(rss):
    assert len(germantechjobs.fetch(keywords=["spark"])) == 1


def test_a_broken_feed_is_a_source_error(monkeypatch):
    monkeypatch.setattr(
        germantechjobs, "fetch_text", lambda url, params=None, headers=None: "<not xml"
    )
    with pytest.raises(SourceError, match="did not parse"):
        germantechjobs.fetch()
