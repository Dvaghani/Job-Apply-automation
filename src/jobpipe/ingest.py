"""Fetch from every configured source, dedupe, filter, and store."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import db
from .config import Config
from .filters import check
from .models import Job
from .sources import (
    adzuna,
    arbeitnow,
    arbeitsagentur,
    ashby,
    germantechjobs,
    greenhouse,
    lever,
    smartrecruiters,
)
from .sources.base import SourceError

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    filtered: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        line = (
            f"fetched {self.fetched}, new {self.inserted}, "
            f"already seen {self.duplicates}, filtered out {self.filtered}"
        )
        if self.errors:
            line += f", {len(self.errors)} source error(s)"
        return line


def collect(config: Config, report: IngestReport) -> list[Job]:
    """Pull from every configured source. One failing source never stops the rest."""
    jobs: list[Job] = []

    for token in config.greenhouse_boards:
        try:
            found = greenhouse.fetch(token)
            log.info("greenhouse/%s: %d jobs", token, len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"greenhouse/{token}: {exc}")

    for site in config.lever_sites:
        try:
            found = lever.fetch(site)
            log.info("lever/%s: %d jobs", site, len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"lever/{site}: {exc}")

    for org in config.ashby_orgs:
        try:
            found = ashby.fetch(org)
            log.info("ashby/%s: %d jobs", org, len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"ashby/{org}: {exc}")

    for company in config.smartrecruiters_companies:
        try:
            found = smartrecruiters.fetch(company, with_descriptions=True)
            log.info("smartrecruiters/%s: %d jobs", company, len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"smartrecruiters/{company}: {exc}")

    az = config.adzuna
    if az and not (az.get("queries") or []):
        # Credentials without queries fetches nothing and raises nothing —
        # the failure mode that looks exactly like "no new jobs".
        report.errors.append(
            "adzuna: configured but `queries:` is empty — nothing was fetched"
        )
    for query in az.get("queries", []) or []:
        try:
            found = adzuna.fetch(
                what=query,
                country=az.get("country", "us"),
                where=az.get("where", ""),
                app_id=az.get("app_id"),
                app_key=az.get("app_key"),
                max_pages=int(az.get("max_pages", 1)),
                distance=az.get("distance"),
                max_days_old=az.get("max_days_old"),
                what_or=az.get("what_or", ""),
                title_only=az.get("title_only", ""),
            )
            log.info("adzuna/%r: %d jobs", query, len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"adzuna/{query}: {exc}")

    ba = config.arbeitsagentur
    if ba.get("queries"):
        try:
            found = arbeitsagentur.fetch(
                queries=list(ba.get("queries") or []),
                where=ba.get("where", ""),
                radius_km=ba.get("umkreis"),
                max_days_old=ba.get("max_days_old"),
                max_pages=int(ba.get("max_pages", 2)),
                max_details=int(ba.get("max_details", 150)),
                offer_types=ba.get("angebotsart", arbeitsagentur.EMPLOYMENT),
                exclude_staffing=bool(ba.get("exclude_staffing", True)),
                # A description costs one request per job here, so let the
                # hard filters throw a job out before it is paid for.
                keep=lambda job: check(job, config.filters) is None,
            )
            log.info("arbeitsagentur: %d jobs", len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"arbeitsagentur: {exc}")

    an = config.arbeitnow
    if an:
        try:
            found = arbeitnow.fetch(
                keywords=list(an.get("keywords") or []),
                location_contains=list(an.get("location_contains") or []),
                max_pages=int(an.get("max_pages", 2)),
            )
            log.info("arbeitnow: %d jobs", len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"arbeitnow: {exc}")

    gtj = config.germantechjobs
    if gtj:
        try:
            found = germantechjobs.fetch(
                keywords=list(gtj.get("keywords") or []),
                limit=gtj.get("limit"),
            )
            log.info("germantechjobs: %d jobs", len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"germantechjobs: {exc}")

    return jobs


def run(config: Config, conn) -> IngestReport:
    report = IngestReport()
    if not any([config.greenhouse_boards, config.lever_sites, config.ashby_orgs,
                config.smartrecruiters_companies, config.adzuna,
                config.arbeitsagentur, config.arbeitnow, config.germantechjobs]):
        report.errors.append(
            "no sources configured — run `jobpipe doctor` to see what's missing"
        )
    jobs = collect(config, report)
    report.fetched = len(jobs)

    seen_this_run: set[str] = set()

    for job in jobs:
        fp = job.fingerprint()
        # Two sources can carry the same role in one run; only handle it once.
        if fp in seen_this_run:
            report.duplicates += 1
            continue
        seen_this_run.add(fp)

        outcome = db.upsert_job(conn, job)
        if outcome == "seen":
            report.duplicates += 1
            continue

        report.inserted += 1
        reason = check(job, config.filters)
        if reason:
            db.reject_by_filter(conn, fp, reason)
            report.filtered += 1

    conn.commit()
    return report
