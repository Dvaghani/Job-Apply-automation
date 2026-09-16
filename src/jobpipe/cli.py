"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from . import applicant as applicant_mod
from . import autofill, db, doctor, ingest, portable, score, tailor, web
from .config import ConfigError, load_config
from .applicant import ApplicantError
from .llm import LLMError
from .models import STATUS_APPLIED, STATUS_APPROVED
from .resume import ResumeError


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )


def cmd_ingest(config, args) -> int:
    conn = db.connect(config.db_path)
    report = ingest.run(config, conn)
    print(report.summary())
    for err in report.errors:
        print(f"  ! {err}", file=sys.stderr)
    # A source outage shouldn't look like success in a cron log.
    return 1 if report.errors and report.fetched == 0 else 0


def cmd_score(config, args) -> int:
    conn = db.connect(config.db_path)
    result = score.run(
        config, conn,
        limit=args.limit,
        fingerprints=list(getattr(args, "job", []) or []),
        rescore=getattr(args, "rescore", False),
    )
    print(f"scored {result['scored']} of {result['total']}, {result['failed']} failed")
    return 1 if result["failed"] and not result["scored"] else 0


def cmd_tailor(config, args) -> int:
    conn = db.connect(config.db_path)

    if args.job:
        fingerprints = list(args.job)
    else:
        rows = db.untailored_approved(conn)
        if args.limit:
            rows = rows[: args.limit]
        fingerprints = [r["fingerprint"] for r in rows]

    if not fingerprints:
        print("nothing to tailor — approve some jobs in the review queue first")
        return 0

    result = tailor.run(
        config, conn, fingerprints,
        cover_letter=not args.no_cover_letter,
        language=args.language,
    )
    print(
        f"tailored {result['tailored']}, {result['flagged']} with unverified claims, "
        f"{result['failed']} failed"
    )
    print(f"output in {config.output_dir}/")
    if result["flagged"]:
        print(
            "\nSome claims could not be traced to your master resume. "
            "Read the NOTES.md in each flagged folder before sending.",
            file=sys.stderr,
        )
    if result["failed"] and not result["tailored"]:
        return 1
    return 2 if (result["flagged"] and args.strict) else 0


def _list_applyable(conn) -> int:
    """No fingerprint given — show what's ready, with the command to run."""
    rows = db.by_status(conn, STATUS_APPROVED)
    if not rows:
        print("Nothing approved yet. Run `jobpipe review` and approve some jobs first.")
        return 0

    print(f"{len(rows)} approved job(s):\n")
    for row in rows:
        tailored = "tailored" if row["tailored_at"] else "NOT tailored"
        score = row["score"] if row["score"] is not None else "--"
        print(f"  [{score:>3}] {row['title'][:46]}")
        print(f"        {row['company']}  ·  {tailored}")
        print(f"        jobpipe apply {row['fingerprint']}")
        print()

    untailored = [r for r in rows if not r["tailored_at"]]
    if untailored:
        print(f"{len(untailored)} of these have no tailored resume yet — "
              "run `jobpipe tailor` first.")
    return 0


def cmd_rerender(config, args) -> int:
    """Rebuild tailored documents from saved state — no model call."""
    conn = db.connect(config.db_path)
    fingerprints = list(args.job) or [r["fingerprint"] for r in db.tailored(conn)]
    if not fingerprints:
        print("nothing tailored yet — run `jobpipe tailor` first")
        return 0

    result = tailor.rerender(config, conn, fingerprints)
    print(
        f"rebuilt {result['rerendered']}, skipped {result['skipped']}, "
        f"failed {result['failed']}"
    )
    if result["skipped"]:
        print(
            "\nSkipped applications were tailored before their decisions were "
            "saved, so there is nothing to rebuild from — re-run "
            "`jobpipe tailor <fingerprint>` for those.",
            file=sys.stderr,
        )
    return 1 if result["failed"] and not result["rerendered"] else 0


def cmd_untailor(config, args) -> int:
    """Delete tailored documents so the jobs can be tailored again."""
    conn = db.connect(config.db_path)

    if args.job:
        # Naming a job is explicit enough; no scope filtering.
        rows = [r for r in (db.get(conn, f) for f in args.job) if r is not None]
    else:
        # Default to approved: those are the ones still waiting to be sent,
        # and so the ones a template change is worth rebuilding. Everything
        # ever tailored is a much larger and mostly finished set.
        rows = db.tailored(conn, status=None if args.all else STATUS_APPROVED)

    # A resume you already sent is the only record of what the employer
    # actually received. Re-tailoring gives different wording, so deleting
    # it loses that for good — keep it unless asked twice.
    applied = [r for r in rows if r["status"] == STATUS_APPLIED]
    if applied and not args.include_applied:
        rows = [r for r in rows if r["status"] != STATUS_APPLIED]
        print(
            f"Keeping {len(applied)} already-applied job(s): their documents are "
            "the record of what you sent.\nPass --include-applied to remove "
            "those too.\n"
        )

    if not rows:
        print("nothing to remove")
        return 0

    print(f"This deletes the tailored documents for {len(rows)} job(s):\n")
    for row in rows:
        print(f"  [{row['status']}] {row['title'][:44]}")
        print(f"    {row['company']}  ·  {row['output_dir'] or '(no folder on disk)'}")
    print("\nThe jobs themselves stay — same status, same score. Only the")
    print("generated folders go, so `jobpipe tailor` rebuilds them.")

    if not args.yes:
        try:
            if input("\nRemove them? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("nothing removed")
                return 0
        except EOFError:
            print("nothing removed (no answer)", file=sys.stderr)
            return 1

    result = tailor.untailor(
        config, conn, [r["fingerprint"] for r in rows]
    )
    print(
        f"\nremoved {result['removed']} folder(s), "
        f"{result['cleared']} job(s) back in the tailoring queue"
    )
    if result["failed"]:
        print(f"{result['failed']} failed — see the messages above", file=sys.stderr)
    print("\nRun `jobpipe tailor` to rebuild them with the current template.")
    return 1 if result["failed"] and not result["cleared"] else 0


def cmd_apply(config, args) -> int:
    conn = db.connect(config.db_path)

    if not args.job:
        return _list_applyable(conn)

    row = db.get(conn, args.job)
    if row is None:
        print(f"error: no job with fingerprint {args.job}", file=sys.stderr)
        print("Run `jobpipe apply` with no arguments to list approved jobs.",
              file=sys.stderr)
        return 2

    me = applicant_mod.load(config.applicant_path)

    print(f"{row['title']} @ {row['company']}")
    print(f"  {autofill.apply_form_url(row['url'])}")
    if not row["output_dir"]:
        print("  (not tailored — run `jobpipe tailor` first to attach a resume)")

    # run() prints the report itself: it can fill more than once, and every
    # pass needs reporting, not just the last.
    autofill.run(
        row, me,
        headless=args.headless,
        wait=not args.no_wait,
        language=args.language or config.language,
    )
    print("\nNothing was submitted. Submit the form yourself in the browser.")

    if args.mark_applied:
        db.set_status(conn, args.job, "applied")
        conn.commit()
        print("Marked as applied.")
    return 0


def _serve(config, args, path: str, banner: str) -> int:
    url = f"http://{args.host}:{args.port}{path}"
    print(f"{banner}: {url}")
    if args.open:
        webbrowser.open(url)
    web.serve(config, host=args.host, port=args.port)
    return 0


def cmd_dashboard(config, args) -> int:
    return _serve(config, args, "/", "Dashboard")


def cmd_review(config, args) -> int:
    return _serve(
        config, args, "/review", f"Review queue (min score {config.min_score})"
    )


def cmd_doctor(config, args) -> int:
    return 1 if doctor.run(config, probe=args.probe) else 0


def cmd_stats(config, args) -> int:
    conn = db.connect(config.db_path)
    counts = db.stats(conn)
    width = max(len(k) for k in counts)
    for status, n in counts.items():
        print(f"{status:<{width}}  {n:>5}")
    print(f"{'total':<{width}}  {sum(counts.values()):>5}")
    return 0


def cmd_export(config, args) -> int:
    """Bundle the personal half of a setup, for another machine."""
    try:
        archive, count = portable.export(config, args.output)
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    size = archive.stat().st_size / 1_000_000
    print(f"{count} file(s) -> {archive}  ({size:.1f} MB)")
    print()
    print("On the other machine:")
    print("  git clone https://github.com/Dvaghani/Job-Apply-automation")
    print("  cd Job-Apply-automation")
    print(f"  unzip {archive.name}")
    print("  python3 -m venv .venv && source .venv/bin/activate")
    print("  pip install -e '.[browser]' && playwright install chromium")
    print("  jobpipe doctor")
    return 0


def cmd_run(config, args) -> int:
    """ingest + score in one go — the usual daily command."""
    rc = cmd_ingest(config, args)
    if rc != 0:
        return rc
    return cmd_score(config, args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobpipe", description="Personal job-application pipeline."
    )
    parser.add_argument("-c", "--config", default=None, help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", help="fetch postings from configured sources")

    p_score = sub.add_parser("score", help="score unscored postings with Claude")
    p_score.add_argument("--limit", type=int, default=None, help="max jobs to score")
    p_score.add_argument(
        "job", nargs="*",
        help="job fingerprints to (re)score wherever they are, keeping their status",
    )
    p_score.add_argument(
        "--rescore", action="store_true",
        help="score every live job again (scored + approved), keeping statuses — "
             "what you want after editing profile.md",
    )

    p_run = sub.add_parser("run", help="ingest, then score")
    p_run.add_argument("--limit", type=int, default=None)

    p_tailor = sub.add_parser(
        "tailor", help="tailor your resume to approved jobs"
    )
    p_tailor.add_argument(
        "job", nargs="*", help="job fingerprints (default: all approved, untailored)"
    )
    p_tailor.add_argument("--limit", type=int, default=None)
    p_tailor.add_argument(
        "--no-cover-letter", action="store_true", help="skip the cover letter"
    )
    p_tailor.add_argument(
        "--strict", action="store_true",
        help="exit non-zero if any claim fails verification",
    )
    p_tailor.add_argument(
        "--language", default=None, choices=["en", "de"],
        help="output language (default: `language` in config.yaml). "
             "German needs a German master resume — see resume_paths.",
    )

    p_apply = sub.add_parser(
        "apply", help="open a posting's form and fill it — never submits"
    )
    p_apply.add_argument(
        "job", nargs="?", default=None,
        help="job fingerprint; omit to list approved jobs and their fingerprints",
    )
    p_apply.add_argument(
        "--headless", action="store_true",
        help="run without a visible browser (inspection only — you cannot submit)",
    )
    p_apply.add_argument(
        "--no-wait", action="store_true", help="close the browser immediately"
    )
    p_apply.add_argument(
        "--language", default=None, choices=["en", "de"],
        help="which language's tailored resume to attach (default: config)",
    )
    p_apply.add_argument(
        "--mark-applied", action="store_true",
        help="mark the job applied afterwards (only do this once you have submitted)",
    )

    p_untailor = sub.add_parser(
        "untailor",
        help="delete tailored documents so the jobs can be tailored again",
    )
    p_untailor.add_argument(
        "job", nargs="*",
        help="job fingerprints; omit for every tailored approved job",
    )
    p_untailor.add_argument(
        "--all", action="store_true",
        help="every tailored job, not just the approved ones",
    )
    p_untailor.add_argument(
        "--include-applied", action="store_true",
        help="also remove documents for jobs you already applied to",
    )
    p_untailor.add_argument(
        "-y", "--yes", action="store_true", help="skip the confirmation"
    )

    p_rerender = sub.add_parser(
        "rerender",
        help="rebuild tailored documents after a template change (no model call)",
    )
    p_rerender.add_argument(
        "job", nargs="*",
        help="job fingerprints; omit to rebuild every tailored application",
    )

    # Both serve the same app; they differ only in the page they point you at.
    for name, help_text in [
        ("dashboard", "run the pipeline from a local web page"),
        ("review", "open the local review queue"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=5000)
        p.add_argument(
            "--open", action="store_true", help="open it in your browser too"
        )

    p_doctor = sub.add_parser(
        "doctor", help="check config, files, backend and sources"
    )
    p_doctor.add_argument(
        "--probe", action="store_true",
        help="also make one real Adzuna call to verify the credentials",
    )

    sub.add_parser("stats", help="show counts by status")

    p_export = sub.add_parser(
        "export",
        help="bundle your config, resumes, answers and job history into a zip",
    )
    p_export.add_argument(
        "output", nargs="?", default="jobpipe-setup.zip",
        help="where to write the archive (default: jobpipe-setup.zip)",
    )
    return parser


COMMANDS = {
    "ingest": cmd_ingest,
    "score": cmd_score,
    "tailor": cmd_tailor,
    "apply": cmd_apply,
    "rerender": cmd_rerender,
    "untailor": cmd_untailor,
    "run": cmd_run,
    "dashboard": cmd_dashboard,
    "review": cmd_review,
    "stats": cmd_stats,
    "export": cmd_export,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        return COMMANDS[args.command](config, args)
    except (ConfigError, ResumeError, ApplicantError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
