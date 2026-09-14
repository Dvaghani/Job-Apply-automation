"""Arbeitnow job board API.

    https://www.arbeitnow.com/api/job-board-api

Open, unauthenticated, and German-focused, with full descriptions in the
listing itself — no second request per job. Each page returns around 250
postings and links to the next.

It is a *general* board rather than a technical one: a page will carry a
welder and a graphic designer alongside the software roles. `keywords` and
`location_contains` are here because of that — without narrowing, this
source contributes exactly the noise it was added to replace.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import Job, strip_html
from .base import SourceError, fetch_json

log = logging.getLogger(__name__)

API = "https://www.arbeitnow.com/api/job-board-api"


def _posted_at(value) -> str | None:
    """`created_at` is a Unix timestamp; the rest of the pipeline wants ISO."""
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError):
        return None


def _matches(item: dict, keywords: list[str], location_contains: list[str]) -> bool:
    if location_contains:
        location = (item.get("location") or "").lower()
        if not any(term.lower() in location for term in location_contains):
            return False
    if keywords:
        haystack = " ".join(
            [
                item.get("title") or "",
                " ".join(item.get("tags") or []),
                " ".join(item.get("job_types") or []),
            ]
        ).lower()
        if not any(term.lower() in haystack for term in keywords):
            return False
    return True


def _parse(item: dict) -> Job:
    location = item.get("location") or ""
    return Job(
        source="arbeitnow",
        source_id=str(item.get("slug") or item.get("url") or ""),
        company=(item.get("company_name") or "unknown").strip(),
        title=(item.get("title") or "").strip(),
        url=item.get("url", ""),
        location=location,
        description=strip_html(item.get("description", "")),
        remote=bool(item.get("remote")),
        posted_at=_posted_at(item.get("created_at")),
        raw=item,
    )


def fetch(
    keywords: list[str] | None = None,
    location_contains: list[str] | None = None,
    max_pages: int = 2,
) -> list[Job]:
    keywords = keywords or []
    location_contains = location_contains or []
    jobs: list[Job] = []
    url: str | None = API

    for _ in range(max(1, max_pages)):
        if not url:
            break
        data = fetch_json(url)
        if not isinstance(data, dict):
            raise SourceError("unexpected Arbeitnow payload")

        for item in data.get("data") or []:
            if not _matches(item, keywords, location_contains):
                continue
            try:
                jobs.append(_parse(item))
            except (KeyError, TypeError) as exc:
                log.warning("arbeitnow: skipping malformed job: %s", exc)

        url = (data.get("links") or {}).get("next")

    return jobs
