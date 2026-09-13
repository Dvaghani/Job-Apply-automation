"""SQLite storage. One file, no server, easy to back up and inspect."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import (
    ALL_STATUSES,
    STATUS_APPLIED,
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_SCORED,
    Job,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    fingerprint   TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    location      TEXT,
    description   TEXT,
    remote        INTEGER DEFAULT 0,
    salary_min    INTEGER,
    salary_max    INTEGER,
    posted_at     TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    score         INTEGER,
    score_reason  TEXT,
    score_flags   TEXT,
    filter_reason TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    decided_at    TEXT,
    applied_at    TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_score  ON jobs(score DESC);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def upsert_job(conn: sqlite3.Connection, job: Job) -> str:
    """Insert a job, or refresh `last_seen` if we already have it.

    Returns "inserted" or "seen". An existing row is never overwritten —
    re-ingesting must not resurrect something I already rejected, or wipe
    a score I already paid for.
    """
    fp = job.fingerprint()
    now = utcnow()
    cur = conn.execute("SELECT fingerprint FROM jobs WHERE fingerprint = ?", (fp,))
    if cur.fetchone() is not None:
        conn.execute("UPDATE jobs SET last_seen = ? WHERE fingerprint = ?", (now, fp))
        return "seen"

    conn.execute(
        """
        INSERT INTO jobs (
            fingerprint, source, source_id, company, title, url, location,
            description, remote, salary_min, salary_max, posted_at,
            status, first_seen, last_seen
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            fp, job.source, job.source_id, job.company, job.title, job.url,
            job.location, job.description, int(job.is_remote()),
            job.salary_min, job.salary_max, job.posted_at,
            STATUS_NEW, now, now,
        ),
    )
    return "inserted"


def reject_by_filter(conn: sqlite3.Connection, fingerprint: str, reason: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, filter_reason = ?, decided_at = ? WHERE fingerprint = ?",
        (STATUS_REJECTED, reason, utcnow(), fingerprint),
    )


def save_score(
    conn: sqlite3.Connection,
    fingerprint: str,
    score: int,
    reason: str,
    flags: list[str] | None = None,
) -> None:
    conn.execute(
        """
        UPDATE jobs SET score = ?, score_reason = ?, score_flags = ?, status = ?
        WHERE fingerprint = ?
        """,
        (score, reason, json.dumps(flags or []), STATUS_SCORED, fingerprint),
    )


def set_status(conn: sqlite3.Connection, fingerprint: str, status: str) -> None:
    if status not in ALL_STATUSES:
        raise ValueError(f"Unknown status: {status}")
    applied_at = utcnow() if status == STATUS_APPLIED else None
    conn.execute(
        """
        UPDATE jobs
        SET status = ?,
            decided_at = ?,
            applied_at = COALESCE(?, applied_at)
        WHERE fingerprint = ?
        """,
        (status, utcnow(), applied_at, fingerprint),
    )


def unscored(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM jobs WHERE status = ? ORDER BY first_seen DESC"
    params: tuple = (STATUS_NEW,)
    if limit:
        sql += " LIMIT ?"
        params += (limit,)
    return list(conn.execute(sql, params))


def review_queue(conn: sqlite3.Connection, min_score: int = 0) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = ? AND COALESCE(score, 0) >= ?
            ORDER BY score DESC, first_seen DESC
            """,
            (STATUS_SCORED, min_score),
        )
    )


def by_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY decided_at DESC", (status,)
        )
    )


def get(conn: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    counts = {r["status"]: r["n"] for r in rows}
    return {s: counts.get(s, 0) for s in ALL_STATUSES}
