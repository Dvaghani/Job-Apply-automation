"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from . import db, ingest, score, web
from .config import ConfigError, load_config


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


def cmd_review(config, args) -> int:
    print(f"Review queue: http://{args.host}:{args.port}  (min score {config.min_score})")
    web.serve(config, host=args.host, port=args.port)
    return 0


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

    p_review = sub.add_parser("review", help="open the local review queue")
    p_review.add_argument("--host", default="127.0.0.1")
    p_review.add_argument("--port", type=int, default=5000)

    sub.add_parser("stats", help="show counts by status")
    return parser


COMMANDS = {
    "ingest": cmd_ingest,
    "score": cmd_score,
    "run": cmd_run,
    "review": cmd_review,
    "stats": cmd_stats,
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
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
