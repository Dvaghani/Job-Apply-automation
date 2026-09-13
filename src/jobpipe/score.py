"""Score how well a posting fits your profile, using Claude.

This is the step that replaces skimming. It runs only on jobs that already
survived the hard filters, so every call is on a plausible candidate.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from . import db, llm
from .config import Config
from .llm import LLMError

log = logging.getLogger(__name__)

# Descriptions run long and most of the signal is near the top.
MAX_DESCRIPTION_CHARS = 6000

SYSTEM = """You screen job postings for a single candidate.

You are given the candidate's profile and one posting. Score how well the \
posting fits, from 0 to 100:

  85-100  Strong fit. Clearly worth a tailored application.
  70-84   Good fit. Worth applying if the week allows.
  50-69   Marginal. Some overlap, notable gaps.
  0-49    Poor fit. Wrong level, wrong domain, or disqualifying requirements.

Be strict and calibrated. Most postings are not strong fits, and a score \
that is too generous wastes the candidate's limited application budget. \
Weigh, in order: required qualifications the candidate lacks; seniority \
match; domain and tech-stack overlap; and anything disqualifying such as \
clearance, on-site requirements, or visa restrictions.

In `reason`, give one or two sentences of specific, concrete justification \
citing the posting. Do not hedge and do not restate the job title.

In `flags`, list short phrases for anything the candidate should know \
before applying — for example "requires security clearance", "5+ yrs \
Kubernetes", "on-site 3 days/week". Use an empty list if there is nothing \
notable."""

PROMPT = """<candidate_profile>
{profile}
</candidate_profile>

<posting>
Company: {company}
Title: {title}
Location: {location}
{salary}
Description:
{description}
</posting>

Score this posting's fit for the candidate."""


class FitScore(BaseModel):
    score: int = Field(ge=0, le=100, description="Fit score from 0 to 100")
    reason: str = Field(description="One or two sentences justifying the score")
    flags: list[str] = Field(
        default_factory=list, description="Short warnings the candidate should know"
    )


def _salary_line(row) -> str:
    low, high = row["salary_min"], row["salary_max"]
    if low and high:
        return f"Salary: {low:,}-{high:,}"
    if low or high:
        return f"Salary: {low or high:,}"
    return "Salary: not stated"


def build_prompt(row, profile: str) -> str:
    description = (row["description"] or "").strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS] + "\n[truncated]"
    return PROMPT.format(
        profile=profile,
        company=row["company"],
        title=row["title"],
        location=row["location"] or "not stated",
        salary=_salary_line(row),
        description=description or "(no description provided)",
    )


def score_one(backend, row, profile: str) -> FitScore:
    return backend.complete(SYSTEM, build_prompt(row, profile), FitScore, max_tokens=2000)


def run(config: Config, conn, limit: int | None = None) -> dict:
    """Score every unscored job. Returns a small summary dict."""
    profile = config.load_profile()
    pending = db.unscored(conn, limit=limit)
    if not pending:
        return {"scored": 0, "failed": 0, "total": 0}

    backend = llm.build(config)
    log.info("scoring %d job(s) via %s", len(pending), backend.name)
    scored = failed = 0

    for row in pending:
        try:
            result = score_one(backend, row, profile)
        except LLMError as exc:
            # Leave the row unscored so the next run retries it.
            log.error("scoring failed for %s @ %s: %s", row["title"], row["company"], exc)
            failed += 1
            continue

        db.save_score(conn, row["fingerprint"], result.score, result.reason, result.flags)
        scored += 1
        log.info("%3d  %s @ %s", result.score, row["title"], row["company"])
        # Commit per row: scoring costs money, don't lose it to a later crash.
        conn.commit()

    return {"scored": scored, "failed": failed, "total": len(pending)}
