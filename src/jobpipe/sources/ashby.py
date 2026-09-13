"""Ashby public job board API.

    https://api.ashbyhq.com/posting-api/job-board/{org}

Public, free, no auth. The org slug is the path segment in a company's
jobs.ashbyhq.com URL.
"""

from __future__ import annotations

import logging

from ..models import Job, strip_html
from .base import SourceError, fetch_json

log = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board/{org}"


def fetch(org: str) -> list[Job]:
    data = fetch_json(API.format(org=org), params={"includeCompensation": "true"})
    if not isinstance(data, dict) or "jobs" not in data:
        raise SourceError(f"unexpected Ashby payload for {org}")

    jobs = []
    for item in data.get("jobs") or []:
        # Ashby returns unlisted drafts too; only take live postings.
        if item.get("isListed") is False:
            continue
        try:
            jobs.append(_parse(item, org))
        except (KeyError, TypeError) as exc:
            log.warning("ashby/%s: skipping malformed job: %s", org, exc)
    return jobs


def _parse(item: dict, org: str) -> Job:
    location = item.get("location", "") or ""
    description = item.get("descriptionPlain") or strip_html(
        item.get("descriptionHtml", "")
    )
    return Job(
        source="ashby",
        source_id=str(item["id"]),
        company=item.get("companyName") or org,
        title=(item.get("title") or "").strip(),
        url=item.get("jobUrl") or item.get("applyUrl", ""),
        location=location,
        description=description,
        remote=bool(item.get("isRemote")) or "remote" in location.lower(),
        posted_at=item.get("publishedAt"),
        raw=item,
    )
