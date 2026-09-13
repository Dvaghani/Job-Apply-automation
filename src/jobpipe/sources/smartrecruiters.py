"""SmartRecruiters public postings API.

    https://api.smartrecruiters.com/v1/companies/{company}/postings

Public, free, no auth. Worth having alongside the US-centric ATSes:
SmartRecruiters has far better coverage among European employers, where
Greenhouse and Lever barely appear.

The company identifier is the slug in a careers.smartrecruiters.com URL.
"""

from __future__ import annotations

import logging

from ..models import Job, strip_html
from .base import SourceError, fetch_json

log = logging.getLogger(__name__)

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
DETAIL = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting}"
PAGE_SIZE = 100


def fetch(company: str, max_pages: int = 5, with_descriptions: bool = False) -> list[Job]:
    """Fetch postings. Descriptions need one extra call each, so they're opt-in."""
    jobs: list[Job] = []
    offset = 0

    for _ in range(max_pages):
        data = fetch_json(
            API.format(company=company), params={"limit": PAGE_SIZE, "offset": offset}
        )
        if not isinstance(data, dict) or "content" not in data:
            raise SourceError(f"unexpected SmartRecruiters payload for {company}")

        page = data.get("content") or []
        if not page:
            break
        for item in page:
            try:
                jobs.append(_parse(item, company))
            except (KeyError, TypeError) as exc:
                log.warning("smartrecruiters/%s: skipping malformed job: %s", company, exc)

        offset += len(page)
        if offset >= int(data.get("totalFound", 0)):
            break

    if with_descriptions:
        for job in jobs:
            try:
                job.description = _fetch_description(company, job.source_id)
            except SourceError as exc:
                log.warning("smartrecruiters/%s: no description for %s: %s",
                            company, job.source_id, exc)
    return jobs


def _fetch_description(company: str, posting_id: str) -> str:
    data = fetch_json(DETAIL.format(company=company, posting=posting_id))
    sections = ((data.get("jobAd") or {}).get("sections") or {})
    parts = [
        (sections.get(name) or {}).get("text", "")
        for name in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    ]
    return strip_html(" ".join(p for p in parts if p))


def _parse(item: dict, company: str) -> Job:
    location = item.get("location") or {}
    full = location.get("fullLocation") or ", ".join(
        x for x in [location.get("city"), location.get("country", "").upper()] if x
    )
    company_name = (item.get("company") or {}).get("name") or company
    slug = (item.get("company") or {}).get("identifier") or company

    return Job(
        source="smartrecruiters",
        source_id=str(item["id"]),
        company=company_name,
        title=(item.get("name") or "").strip(),
        url=f"https://jobs.smartrecruiters.com/{slug}/{item['id']}",
        location=full,
        description="",  # filled by _fetch_description when requested
        remote=bool(location.get("remote")),
        posted_at=item.get("releasedDate"),
        raw=item,
    )
