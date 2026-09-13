"""Fetch from every configured source, dedupe, filter, and store."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import db
from .config import Config
from .filters import check
from .models import Job
from .sources import adzuna, ashby, greenhouse, lever, smartrecruiters
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
            )
            log.info("adzuna/%r: %d jobs", query, len(found))
            jobs.extend(found)
        except SourceError as exc:
            report.errors.append(f"adzuna/{query}: {exc}")

    return jobs


def run(config: Config, conn) -> IngestReport:
    report = IngestReport()
    if not any([config.greenhouse_boards, config.lever_sites, config.ashby_orgs,
                config.smartrecruiters_companies, config.adzuna]):
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
