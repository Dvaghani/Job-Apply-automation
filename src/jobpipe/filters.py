"""Hard filters — deterministic rules applied before any LLM call.

Every job these reject is money saved. Keep them cheap and obvious;
anything requiring judgement belongs in the scorer instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Filters
from .models import Job


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def check(job: Job, filters: Filters, now: datetime | None = None) -> str | None:
    """Return a rejection reason, or None if the job passes.

    Salary is only judged when the posting states it — most don't, and
    rejecting on a missing field would throw away most of the pipeline.
    """
    title = (job.title or "").lower()

    if filters.title_exclude:
        for term in filters.title_exclude:
            if term in title:
                return f"title contains excluded term '{term}'"

    if filters.title_include:
        if not any(term in title for term in filters.title_include):
            return "title matches no required term"

    if filters.remote_only and not job.is_remote():
        return "not remote"

    if filters.locations and not job.is_remote():
        location = (job.location or "").lower()
        if not any(loc in location for loc in filters.locations):
            return f"location '{job.location}' not in allowed list"

    if filters.min_salary is not None:
        # Compare against the top of the stated band: a range of
        # 80–120k passes a 100k floor, since the band is negotiable.
        stated = job.salary_max or job.salary_min
        if stated is not None and stated < filters.min_salary:
            return f"salary {stated} below floor {filters.min_salary}"

    if filters.max_age_days is not None:
        posted = _parse_ts(job.posted_at)
        if posted is not None:
            reference = now or datetime.now(timezone.utc)
            if posted < reference - timedelta(days=filters.max_age_days):
                return f"posted {posted.date()}, older than {filters.max_age_days} days"

    return None
