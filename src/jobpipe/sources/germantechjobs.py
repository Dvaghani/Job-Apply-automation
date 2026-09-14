"""GermanTechJobs RSS feed.

    https://germantechjobs.de/rss

Small next to the federal register, but unusually well suited to a
technical search: every posting is an IT role, and most state a salary
band — which German postings almost never do.

The band is the reason this source earns a parser rather than a generic
RSS reader. Titles arrive as `{role} @ {company} [{min} - {max} €]`, so the
company and the salary can both be recovered, and a stated salary is
something the hard filters can act on.

No location is published. A posting therefore has an empty `location`, and
a `locations:` filter would reject the lot — leave that filter unset if
you want this source.
"""

from __future__ import annotations

import logging
import re
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from ..models import Job, strip_html
from .base import SourceError, fetch_text

log = logging.getLogger(__name__)

FEED = "https://germantechjobs.de/rss"

# "Backend Engineer @ Acme GmbH [48.000 - 78.000 €]"
_TITLE = re.compile(r"^(?P<title>.+?)\s+@\s+(?P<company>.+?)(?:\s*\[(?P<salary>[^\]]*)\])?$")

# German thousands separators: 48.000 - 78.000
_SALARY = re.compile(r"(\d[\d.\s]*\d|\d)")


def parse_salary(text: str) -> tuple[int | None, int | None]:
    """The band out of "48.000 - 78.000 €", or (None, None).

    Values under a plausible annual floor are ignored rather than guessed
    at: a "4" from some unrelated fragment is worse than no salary, because
    the floor filter would act on it.
    """
    if not text:
        return None, None
    numbers = []
    for raw in _SALARY.findall(text):
        try:
            value = int(raw.replace(".", "").replace(" ", ""))
        except ValueError:
            continue
        if value >= 1000:
            numbers.append(value)
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], None
    return min(numbers), max(numbers)


def split_title(raw: str) -> tuple[str, str, tuple[int | None, int | None]]:
    """Role, employer and salary band out of one RSS title."""
    text = (raw or "").strip()
    match = _TITLE.match(text)
    if not match:
        # Not the documented shape — keep the whole title rather than
        # inventing an employer from half of it.
        return text, "", (None, None)
    return (
        match.group("title").strip(),
        (match.group("company") or "").strip(),
        parse_salary(match.group("salary") or ""),
    )


def _posted_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _text(item, tag: str) -> str:
    node = item.find(tag)
    return (node.text or "") if node is not None else ""


def _parse(item) -> Job:
    title, company, (low, high) = split_title(_text(item, "title"))
    link = _text(item, "link").strip()
    return Job(
        source="germantechjobs",
        source_id=_text(item, "guid").strip() or link,
        company=company or "unknown",
        title=title,
        url=link,
        location="",  # not published in the feed
        description=strip_html(_text(item, "description")),
        salary_min=low,
        salary_max=high,
        posted_at=_posted_at(_text(item, "pubDate")),
        raw={"title": _text(item, "title"), "link": link},
    )


def fetch(keywords: list[str] | None = None, limit: int | None = None) -> list[Job]:
    """Every posting in the feed, optionally narrowed by keyword."""
    body = fetch_text(FEED)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise SourceError(f"GermanTechJobs RSS did not parse: {exc}") from exc

    jobs: list[Job] = []
    for item in root.iter("item"):
        try:
            job = _parse(item)
        except (TypeError, ValueError) as exc:
            log.warning("germantechjobs: skipping malformed item: %s", exc)
            continue
        if keywords:
            haystack = f"{job.title} {job.description}".lower()
            if not any(term.lower() in haystack for term in keywords):
                continue
        jobs.append(job)
        if limit and len(jobs) >= limit:
            break
    return jobs
