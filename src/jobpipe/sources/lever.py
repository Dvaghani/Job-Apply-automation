"""Lever public postings API.

    https://api.lever.co/v0/postings/{site}?mode=json

Public, free, no auth. The site slug is the path segment in a company's
jobs.lever.co URL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import Job, strip_html
from .base import SourceError, fetch_json

log = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings/{site}"


def fetch(site: str) -> list[Job]:
    data = fetch_json(API.format(site=site), params={"mode": "json"})
    if not isinstance(data, list):
        raise SourceError(f"unexpected Lever payload for {site}")

    jobs = []
    for item in data:
        try:
            jobs.append(_parse(item, site))
        except (KeyError, TypeError) as exc:
            log.warning("lever/%s: skipping malformed job: %s", site, exc)
    return jobs


def _parse(item: dict, site: str) -> Job:
    categories = item.get("categories") or {}
    location = categories.get("location", "") or ""
    workplace = (item.get("workplaceType") or "").lower()

    # Lever gives createdAt as epoch milliseconds.
    posted_at = None
    created = item.get("createdAt")
    if isinstance(created, (int, float)):
        posted_at = datetime.fromtimestamp(
            created / 1000, tz=timezone.utc
        ).isoformat(timespec="seconds")

    description = item.get("descriptionPlain") or strip_html(item.get("description", ""))

    return Job(
        source="lever",
        source_id=str(item["id"]),
        company=site,
        title=(item.get("text") or "").strip(),
        url=item.get("hostedUrl") or item.get("applyUrl", ""),
        location=location,
        description=description,
        remote=workplace == "remote" or "remote" in location.lower(),
        posted_at=posted_at,
        raw=item,
    )
