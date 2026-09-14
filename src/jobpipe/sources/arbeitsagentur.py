"""Bundesagentur für Arbeit — Germany's official vacancy register.

    https://rest.arbeitsagentur.de/jobboerse/jobsuche-service

The largest job source in the country by a wide margin, public, and free.
For a German search it covers the Mittelstand that never reaches a
commercial aggregator, and — unlike an aggregator — it links to the
employer rather than to itself.

Two version numbers, which is not a typo. Search is **v6**: `/pc/v4/jobs`
answers 403 and is gone, whatever the docs say. Job details are still on
**v4**, and the reference number must be Base64-encoded to address one.

Search results carry no description, so a description costs a second
request per job. That is the expensive part of this source and the reason
`fetch` takes a `keep` predicate — see below.
"""

from __future__ import annotations

import base64
import logging
import time

from ..models import Job, strip_html
from .base import NotFound, SourceError, fetch_json

log = logging.getLogger(__name__)

SEARCH = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
DETAILS = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails"
POSTING = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"

# Published in the job board's own web client. There is no registration:
# it identifies the caller as a job-search client, nothing more.
API_KEY = "jobboerse-jobsuche"

MAX_PAGE_SIZE = 100

# Courtesy delay between detail requests. The endpoint answers 429 (and
# sometimes 403) to bursts, and this is somebody's public infrastructure.
DETAIL_DELAY = 0.25
BACKOFF = (1.0, 3.0, 8.0)

# Offer types. 1 is ordinary employment; 4 and 34 are apprenticeships and
# internships, which are worth asking for separately rather than by default.
EMPLOYMENT = 1


def _headers() -> dict:
    return {"X-API-Key": API_KEY}


def search(
    what: str,
    where: str = "",
    radius_km: int | None = None,
    max_days_old: int | None = None,
    offer_types: str | int = EMPLOYMENT,
    exclude_staffing: bool = True,
    page: int = 1,
    size: int = MAX_PAGE_SIZE,
) -> tuple[list[dict], int]:
    """One page of search results, plus the total the query matched.

    `exclude_staffing` drops Zeitarbeit and private recruiters. It is on by
    default because those postings are the bulk of the noise in a German
    technical search: the same role, relisted by six agencies, none of whom
    are the employer.
    """
    params: dict = {
        "was": what,
        "page": page,
        "size": min(size, MAX_PAGE_SIZE),
        "angebotsart": offer_types,
    }
    if where:
        params["wo"] = where
    if radius_km is not None:
        params["umkreis"] = radius_km
    if max_days_old is not None:
        params["veroeffentlichtseit"] = max_days_old
    if exclude_staffing:
        params["zeitarbeit"] = "false"
        params["pav"] = "false"

    data = fetch_json(SEARCH, params=params, headers=_headers())
    if not isinstance(data, dict):
        raise SourceError("unexpected Bundesagentur payload")
    return list(data.get("ergebnisliste") or []), int(data.get("maxErgebnisse") or 0)


def encode_reference(refnr: str) -> str:
    """The detail endpoint addresses a job by its Base64-encoded refnr."""
    return base64.b64encode(refnr.encode("utf-8")).decode("ascii")


def details(refnr: str) -> dict:
    """Full record for one posting, including the description.

    Raises NotFound when the posting has been filled — the register purges
    those immediately, so it is a normal outcome of looking one up a few
    minutes after finding it.
    """
    last: Exception | None = None
    for attempt, pause in enumerate((0.0,) + BACKOFF):
        if pause:
            time.sleep(pause)
        try:
            data = fetch_json(f"{DETAILS}/{encode_reference(refnr)}", headers=_headers())
            return data if isinstance(data, dict) else {}
        except NotFound:
            raise
        except SourceError as exc:
            last = exc
            log.debug("arbeitsagentur: detail attempt %d failed: %s", attempt + 1, exc)
    raise SourceError(f"details for {refnr} failed: {last}")


def _location(item: dict) -> str:
    for place in item.get("stellenlokationen") or []:
        address = place.get("adresse") or {}
        town = address.get("ort")
        if town:
            return town
    return ""


def _parse(item: dict, detail: dict | None = None) -> Job:
    detail = detail or {}
    refnr = str(item.get("referenznummer") or detail.get("referenznummer") or "")
    # An external URL means the employer takes applications on their own
    # site, which is both a better link and the only one `apply` can fill.
    url = detail.get("externeURL") or POSTING.format(refnr=refnr)
    return Job(
        source="arbeitsagentur",
        source_id=refnr,
        company=(item.get("firma") or detail.get("firma") or "unbekannt").strip(),
        title=(item.get("stellenangebotsTitel") or "").strip(),
        url=url,
        location=_location(item) or _location(detail),
        description=strip_html(detail.get("stellenangebotsBeschreibung") or ""),
        remote=bool(item.get("homeofficemoeglich")),
        posted_at=item.get("datumErsteVeroeffentlichung"),
        raw={"search": item, "detail": detail},
    )


def fetch(
    queries: list[str],
    where: str = "",
    radius_km: int | None = None,
    max_days_old: int | None = None,
    max_pages: int = 2,
    max_details: int = 150,
    offer_types: str | int = EMPLOYMENT,
    exclude_staffing: bool = True,
    keep=None,
) -> list[Job]:
    """Search every query and return the postings, with descriptions.

    `keep` is the reason this signature is not just a list of queries. A
    description costs one request per job, and a Chemnitz-radius search runs
    to several hundred. The pipeline already refuses to spend an LLM call on
    a job the hard filters would reject; this applies the same rule one stage
    earlier, so a title the caller will throw away never costs a round trip
    either. Pass a callable taking a description-less Job and returning
    whether it is worth fetching.
    """
    jobs: list[Job] = []
    seen: set[str] = set()
    fetched_details = 0
    capped = False

    for what in queries:
        for page in range(1, max_pages + 1):
            results, total = search(
                what, where=where, radius_km=radius_km, max_days_old=max_days_old,
                offer_types=offer_types, exclude_staffing=exclude_staffing, page=page,
            )
            if page == 1:
                log.info("arbeitsagentur/%s: %d match(es)", what, total)
            if not results:
                break

            for item in results:
                refnr = str(item.get("referenznummer") or "")
                if not refnr or refnr in seen:
                    continue
                seen.add(refnr)

                job = _parse(item)
                if keep is not None and not keep(job):
                    # Rejected on title or location alone — no point paying
                    # for the description.
                    jobs.append(job)
                    continue

                if fetched_details >= max_details:
                    capped = True
                    jobs.append(job)
                    continue

                try:
                    detail = details(refnr)
                    fetched_details += 1
                    jobs.append(_parse(item, detail))
                except NotFound:
                    log.debug("arbeitsagentur: %s was filled and purged", refnr)
                except SourceError as exc:
                    # Keep the job without its description rather than lose
                    # it; the scorer copes with a missing one.
                    log.warning("arbeitsagentur: no detail for %s: %s", refnr, exc)
                    jobs.append(job)
                time.sleep(DETAIL_DELAY)

            if len(results) < MAX_PAGE_SIZE:
                break

    if capped:
        log.warning(
            "arbeitsagentur: stopped fetching descriptions at %d — raise "
            "`max_details` or narrow the queries", max_details,
        )
    return jobs
