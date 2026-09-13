"""Greenhouse public job board API.

    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Public, free, no auth, no rate limit published. The board token is the
slug in a company's boards.greenhouse.io URL.
"""

from __future__ import annotations

import logging

from ..models import Job, strip_html
from .base import SourceError, fetch_json

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch(board_token: str) -> list[Job]:
    data = fetch_json(API.format(token=board_token), params={"content": "true"})
    if not isinstance(data, dict) or "jobs" not in data:
        raise SourceError(f"unexpected Greenhouse payload for {board_token}")

    jobs = []
    for item in data.get("jobs") or []:
        try:
            jobs.append(_parse(item, board_token))
        except (KeyError, TypeError) as exc:
            log.warning("greenhouse/%s: skipping malformed job: %s", board_token, exc)
    return jobs


def _parse(item: dict, board_token: str) -> Job:
    location = (item.get("location") or {}).get("name", "") or ""
    # Greenhouse omits `company_name` on single-company boards; fall back
    # to the token, which is usually the company slug.
    company = item.get("company_name") or board_token
    return Job(
        source="greenhouse",
        source_id=str(item["id"]),
        company=company,
        title=item.get("title", "").strip(),
        url=item.get("absolute_url", ""),
        location=location,
        description=strip_html(item.get("content", "")),
        remote="remote" in location.lower(),
        posted_at=item.get("updated_at"),
        raw=item,
    )
