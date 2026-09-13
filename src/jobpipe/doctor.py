"""Configuration check: what is actually wired up, and what is silently inert.

A source that is misconfigured produces no jobs and no error, which looks
identical to a source that simply had nothing new. This makes the
difference visible.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

OK = "ok"
WARN = "warn"
FAIL = "fail"

MARK = {OK: "+", WARN: "!", FAIL: "x"}


def _check(status: str, label: str, detail: str = "") -> tuple[str, str, str]:
    return (status, label, detail)


def check_files(config) -> list[tuple[str, str, str]]:
    out = []
    for label, path, needed_for in [
        ("profile.md", config.profile_path, "score"),
        ("resume.json", config.resume_path, "tailor"),
        ("applicant.yaml", config.applicant_path, "apply"),
    ]:
        if Path(path).exists():
            out.append(_check(OK, label, str(path)))
        else:
            out.append(_check(WARN, label, f"missing — `jobpipe {needed_for}` will fail"))
    return out


def check_backend(config) -> list[tuple[str, str, str]]:
    backend = (config.backend or "api").lower()
    if backend == "api":
        if os.environ.get("ANTHROPIC_API_KEY"):
            return [_check(OK, "backend: api", "ANTHROPIC_API_KEY is set")]
        return [_check(FAIL, "backend: api", "ANTHROPIC_API_KEY is not set")]

    if shutil.which("claude"):
        return [_check(OK, f"backend: {backend}", f"claude CLI found, model={config.cli_model}")]
    return [_check(FAIL, f"backend: {backend}", "claude CLI not on PATH")]


# Keys that belong under `sources:`. Indented one level too few, they parse
# fine and do nothing — a silent no-op that reads as "no new jobs".
SOURCE_KEYS = {"greenhouse", "lever", "ashby", "smartrecruiters", "adzuna"}


def check_commented_out(config) -> list[tuple[str, str, str]]:
    """Catch a source block that is present in the file but commented out.

    `#` is easy to leave in place when filling in credentials, and the
    result is a config that looks filled in and parses to nothing.
    """
    if not config.path or not Path(config.path).exists():
        return []
    try:
        text = Path(config.path).read_text(encoding="utf-8")
    except OSError:
        return []

    out = []
    for key in sorted(SOURCE_KEYS):
        if key in (config.raw.get("sources") or {}) or key in (config.raw or {}):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") and stripped.lstrip("# ").startswith(f"{key}:"):
                out.append(_check(
                    FAIL, f"`{key}:` is commented out",
                    f"remove the leading `#` from that block in {config.path}",
                ))
                break
    return out


def check_misplaced(config) -> list[tuple[str, str, str]]:
    """Catch source blocks sitting at the top level instead of under `sources:`."""
    stray = sorted(SOURCE_KEYS & set(config.raw or {}))
    return [
        _check(FAIL, f"`{key}:` at top level",
               f"it must be indented under `sources:` — as written it does nothing")
        for key in stray
    ]


def check_sources(config) -> list[tuple[str, str, str]]:
    """Report every source, including the ones doing nothing."""
    out = check_misplaced(config) + check_commented_out(config)
    configured = 0

    for label, values in [
        ("greenhouse", config.greenhouse_boards),
        ("lever", config.lever_sites),
        ("ashby", config.ashby_orgs),
        ("smartrecruiters", config.smartrecruiters_companies),
    ]:
        if values:
            configured += 1
            out.append(_check(OK, label, f"{len(values)} board(s): {', '.join(values[:4])}"))
        else:
            out.append(_check(WARN, label, "none configured"))

    az = config.adzuna
    if not az:
        if "adzuna" in (config.raw or {}):
            out.append(_check(FAIL, "adzuna", "block found, but not under `sources:`"))
        else:
            out.append(_check(WARN, "adzuna", "no `adzuna:` block under `sources:`"))
    else:
        app_id = az.get("app_id") or os.environ.get("ADZUNA_APP_ID")
        app_key = az.get("app_key") or os.environ.get("ADZUNA_APP_KEY")
        queries = az.get("queries") or []
        if not app_id or not app_key:
            out.append(_check(FAIL, "adzuna", "app_id/app_key missing"))
        elif not queries:
            # The exact shape that fails silently: keys present, nothing to ask for.
            out.append(_check(FAIL, "adzuna", "keys set but `queries:` is empty — "
                                              "nothing will be fetched"))
        else:
            configured += 1
            out.append(_check(
                OK, "adzuna",
                f"{len(queries)} quer(ies), country={az.get('country', 'us')}, "
                f"where={az.get('where') or 'anywhere'}",
            ))

    if configured == 0 and not any(s == FAIL for s, _, _ in out):
        out.append(_check(FAIL, "sources", "nothing is configured — `ingest` will find nothing"))
    return out


def probe_adzuna(config) -> tuple[str, str, str]:
    """Make one real Adzuna call to prove the credentials work."""
    az = config.adzuna
    app_id = az.get("app_id") or os.environ.get("ADZUNA_APP_ID")
    app_key = az.get("app_key") or os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return _check(WARN, "adzuna live check", "skipped — no credentials")

    from .sources import adzuna
    from .sources.base import SourceError

    query = (az.get("queries") or ["engineer"])[0]
    try:
        jobs = adzuna.fetch(
            what=query, country=az.get("country", "us"), where=az.get("where", ""),
            app_id=app_id, app_key=app_key, max_pages=1, results_per_page=10,
        )
    except SourceError as exc:
        detail = str(exc)[:120]
        if "401" in detail or "403" in detail or "503" in detail:
            detail += "  (Adzuna answers bad credentials this way — re-check app_id/app_key)"
        return _check(FAIL, "adzuna live check", detail)
    return _check(OK, "adzuna live check", f"{query!r} returned {len(jobs)} job(s)")


def run(config, probe: bool = False) -> int:
    """Print the report. Returns the number of failures."""
    sections = [
        ("Files", check_files(config)),
        ("Model backend", check_backend(config)),
        ("Sources", check_sources(config)),
    ]
    if probe and config.adzuna:
        sections.append(("Live check", [probe_adzuna(config)]))

    failures = 0
    for title, checks in sections:
        print(f"\n{title}")
        for status, label, detail in checks:
            if status == FAIL:
                failures += 1
            line = f"  {MARK[status]} {label}"
            print(f"{line:<34} {detail}" if detail else line)

    print()
    if failures:
        print(f"{failures} problem(s) to fix.")
    else:
        print("No problems found.")
    return failures
