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

    if backend in {"antigravity", "antigravity-cli", "agy"}:
        if shutil.which("agy"):
            return [_check(
                OK, f"backend: {backend}", f"agy CLI found, model={config.agy_model}"
            )]
        return [_check(
            FAIL, f"backend: {backend}",
            "agy CLI not on PATH — install from https://antigravity.google/cli",
        )]

    if shutil.which("claude"):
        return [_check(OK, f"backend: {backend}", f"claude CLI found, model={config.cli_model}")]
    return [_check(FAIL, f"backend: {backend}", "claude CLI not on PATH")]


# Keys that belong under `sources:`. Indented one level too few, they parse
# fine and do nothing — a silent no-op that reads as "no new jobs".
SOURCE_KEYS = {
    "greenhouse", "lever", "ashby", "smartrecruiters", "adzuna",
    "arbeitsagentur", "arbeitnow", "germantechjobs",
}


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

    ba = config.arbeitsagentur
    if not ba:
        out.append(_check(WARN, "arbeitsagentur", "no `arbeitsagentur:` block"))
    elif not (ba.get("queries") or []):
        # The silent-failure shape again: a block with nothing to ask for.
        out.append(_check(FAIL, "arbeitsagentur",
                          "`queries:` is empty — nothing will be fetched"))
    else:
        configured += 1
        staffing = "excluded" if ba.get("exclude_staffing", True) else "INCLUDED"
        out.append(_check(
            OK, "arbeitsagentur",
            f"{len(ba['queries'])} quer(ies), where={ba.get('where') or 'anywhere'}, "
            f"{ba.get('umkreis', '-')}km, staffing agencies {staffing}",
        ))

    for label, block, detail in [
        ("arbeitnow", config.arbeitnow,
         lambda b: f"{len(b.get('keywords') or []) or 'no'} keyword filter(s), "
                   f"{len(b.get('location_contains') or []) or 'no'} location filter(s)"),
        ("germantechjobs", config.germantechjobs,
         lambda b: f"{len(b.get('keywords') or []) or 'no'} keyword filter(s)"),
    ]:
        if not block:
            out.append(_check(WARN, label, f"no `{label}:` block"))
        else:
            configured += 1
            out.append(_check(OK, label, detail(block)))

    if configured == 0 and not any(s == FAIL for s, _, _ in out):
        out.append(_check(FAIL, "sources", "nothing is configured — `ingest` will find nothing"))
    return out


def probe_adzuna(config) -> list[tuple[str, str, str]]:
    """Run every configured query and report how many jobs each returns.

    Per-query counts are what makes tuning possible: a zero means the
    query is too narrow, not that the credentials are wrong.
    """
    az = config.adzuna
    app_id = az.get("app_id") or os.environ.get("ADZUNA_APP_ID")
    app_key = az.get("app_key") or os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return [_check(WARN, "adzuna live check", "skipped — no credentials")]

    from .sources import adzuna
    from .sources.base import SourceError

    queries = az.get("queries") or []
    where = az.get("where", "")
    distance = az.get("distance")
    out = []
    total = 0

    for query in queries:
        try:
            jobs = adzuna.fetch(
                what=query, country=az.get("country", "us"), where=where,
                app_id=app_id, app_key=app_key, max_pages=1, results_per_page=50,
                distance=distance, max_days_old=az.get("max_days_old"),
            )
        except SourceError as exc:
            detail = str(exc)[:110]
            if any(code in detail for code in ("401", "403", "503")):
                detail += "  (Adzuna answers bad credentials this way)"
            out.append(_check(FAIL, f"  {query[:34]}", detail))
            continue
        total += len(jobs)
        status = OK if jobs else WARN
        note = f"{len(jobs)} job(s)" + ("" if jobs else "  — too narrow, try fewer words")
        out.append(_check(status, f"  {query[:34]}", note))

    if queries and total == 0:
        hint = "every query returned nothing"
        if where and not distance:
            hint += f" — `where: {where}` with no `distance:` searches a tight radius; try `distance: 50`"
        out.append(_check(FAIL, "adzuna live check", hint))
    elif queries:
        out.append(_check(OK, "adzuna live check", f"{total} job(s) across {len(queries)} quer(ies)"))
    return out


def probe_arbeitsagentur(config) -> list[tuple[str, str, str]]:
    """Run every configured query for real and report the counts.

    Same purpose as the Adzuna probe: a query that matches nothing is
    indistinguishable from a quiet day until you can see the number.
    """
    from .sources import arbeitsagentur
    from .sources.base import SourceError

    ba = config.arbeitsagentur
    out = []
    total = 0
    for query in ba.get("queries") or []:
        try:
            _, found = arbeitsagentur.search(
                query,
                where=ba.get("where", ""),
                radius_km=ba.get("umkreis"),
                max_days_old=ba.get("max_days_old"),
                offer_types=ba.get("angebotsart", arbeitsagentur.EMPLOYMENT),
                exclude_staffing=bool(ba.get("exclude_staffing", True)),
                size=1,
            )
        except SourceError as exc:
            out.append(_check(FAIL, str(query), str(exc)[:90]))
            continue
        total += found
        if found == 0:
            out.append(_check(WARN, str(query), "0 job(s) — too narrow, or widen `umkreis`"))
        else:
            out.append(_check(OK, str(query), f"{found} job(s)"))

    if total:
        out.append(_check(OK, "total", f"{total} match(es) before dedup and filters"))
    return out


def check_resume_rendering(config) -> list[tuple[str, str, str]]:
    """What the tailored resume will actually come out looking like.

    Neither of these is fatal — a missing pdflatex falls back to the HTML
    renderer and a missing headshot leaves a placeholder box — but both
    change the document that gets sent, silently, which is exactly the kind
    of thing this command exists to surface.
    """
    out = []

    if shutil.which("pdflatex"):
        out.append(_check(OK, "pdflatex", "resume.tex will be compiled to PDF"))
    else:
        out.append(_check(
            WARN, "pdflatex",
            "not on PATH — falling back to the HTML layout. "
            "Install TeX Live for the LaTeX one.",
        ))

    photo = (config.photo_path or "").strip()
    if not photo:
        out.append(_check(WARN, "headshot", "photo_path is empty — no photo"))
    elif not Path(photo).exists():
        out.append(_check(
            WARN, "headshot",
            f"no file at {photo} — the resume will show a placeholder box",
        ))
    elif Path(photo).suffix.lower() not in {".jpg", ".jpeg", ".png", ".pdf"}:
        out.append(_check(
            WARN, "headshot",
            f"{photo} is not a format pdflatex can include (jpg, png, pdf)",
        ))
    else:
        out.append(_check(OK, "headshot", photo))

    return out


def run(config, probe: bool = False) -> int:
    """Print the report. Returns the number of failures."""
    sections = [
        ("Files", check_files(config)),
        ("Model backend", check_backend(config)),
        ("Resume rendering", check_resume_rendering(config)),
        ("Sources", check_sources(config)),
    ]
    if probe and config.adzuna:
        sections.append(("Adzuna live check", probe_adzuna(config)))
    if probe and config.arbeitsagentur.get("queries"):
        sections.append(("Bundesagentur live check", probe_arbeitsagentur(config)))

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
