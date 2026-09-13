"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from . import applicant as applicant_mod
from . import autofill, db, doctor, ingest, score, tailor, web
from .config import ConfigError, load_config
from .applicant import ApplicantError
from .llm import LLMError
from .models import STATUS_APPROVED
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
    result = score.run(config, conn, limit=args.limit)
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

    result = tailor.run(config, conn, fingerprints, cover_letter=not args.no_cover_letter)
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

    actions = autofill.run(row, me, headless=args.headless, wait=not args.no_wait)

    filled = sum(1 for a in actions if a.action == "filled")
    print(f"\n{filled} of {len(actions)} fields filled:\n")
    for action in actions:
        print(action)
    print("\nNothing was submitted. Submit the form yourself in the browser.")

    if args.mark_applied:
        db.set_status(conn, args.job, "applied")
        conn.commit()
        print("Marked as applied.")
    return 0


def cmd_review(config, args) -> int:
    print(f"Review queue: http://{args.host}:{args.port}  (min score {config.min_score})")
    web.serve(config, host=args.host, port=args.port)
    return 0


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
        "--mark-applied", action="store_true",
        help="mark the job applied afterwards (only do this once you have submitted)",
    )

    p_review = sub.add_parser("review", help="open the local review queue")
    p_review.add_argument("--host", default="127.0.0.1")
    p_review.add_argument("--port", type=int, default=5000)

    p_doctor = sub.add_parser(
        "doctor", help="check config, files, backend and sources"
    )
    p_doctor.add_argument(
        "--probe", action="store_true",
        help="also make one real Adzuna call to verify the credentials",
    )

    sub.add_parser("stats", help="show counts by status")
    return parser


COMMANDS = {
    "ingest": cmd_ingest,
    "score": cmd_score,
    "tailor": cmd_tailor,
    "apply": cmd_apply,
    "run": cmd_run,
    "review": cmd_review,
    "stats": cmd_stats,
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
