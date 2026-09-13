"""Adzuna aggregator API.

    https://api.adzuna.com/v1/api/jobs/{country}/search/{page}

Free tier is ~1,000 calls/month. Needs an app_id and app_key from
https://developer.adzuna.com/ — put them in config.yaml or set
ADZUNA_APP_ID / ADZUNA_APP_KEY in the environment.

Unlike the ATS sources this is a broad aggregator, so it's noisy. Use it
to catch companies that aren't on your watchlist yet.
"""

from __future__ import annotations

import logging
import os

from ..models import Job, strip_html
from .base import SourceError, fetch_json

log = logging.getLogger(__name__)

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def fetch(
    what: str,
    country: str = "us",
    where: str = "",
    app_id: str | None = None,
    app_key: str | None = None,
    max_pages: int = 1,
    results_per_page: int = 50,
    distance: int | None = None,
    max_days_old: int | None = None,
    what_or: str = "",
    title_only: str = "",
) -> list[Job]:
    """Search Adzuna.

    `what` treats multiple words as *all must match*, which makes a long
    German compound phrase very restrictive. `what_or` matches any of the
    words instead, and `distance` widens the radius around `where` — both
    matter a lot for a small city.
    """
    app_id = app_id or os.environ.get("ADZUNA_APP_ID")
    app_key = app_key or os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise SourceError(
            "Adzuna needs app_id and app_key — set them under sources.adzuna "
            "in config.yaml, or as ADZUNA_APP_ID / ADZUNA_APP_KEY."
        )

    jobs: list[Job] = []
    for page in range(1, max_pages + 1):
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": results_per_page,
            "what": what,
            "content-type": "application/json",
        }
        if where:
            params["where"] = where
        if distance is not None:
            params["distance"] = distance
        if max_days_old is not None:
            params["max_days_old"] = max_days_old
        if what_or:
            params["what_or"] = what_or
        if title_only:
            params["title_only"] = title_only

        data = fetch_json(API.format(country=country, page=page), params=params)
        if not isinstance(data, dict):
            raise SourceError("unexpected Adzuna payload")

        results = data.get("results") or []
        if not results:
            break
        for item in results:
            try:
                jobs.append(_parse(item))
            except (KeyError, TypeError) as exc:
                log.warning("adzuna: skipping malformed job: %s", exc)
    return jobs


def _parse(item: dict) -> Job:
    location = (item.get("location") or {}).get("display_name", "") or ""
    company = (item.get("company") or {}).get("display_name", "") or "unknown"

    def _int(value):
        return int(value) if isinstance(value, (int, float)) else None

    return Job(
        source="adzuna",
        source_id=str(item["id"]),
        company=company,
        title=(item.get("title") or "").strip(),
        url=item.get("redirect_url", ""),
        location=location,
        description=strip_html(item.get("description", "")),
        remote="remote" in location.lower(),
        salary_min=_int(item.get("salary_min")),
        salary_max=_int(item.get("salary_max")),
        posted_at=item.get("created"),
        raw=item,
    )
