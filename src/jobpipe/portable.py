"""Move a working setup to another machine.

The code is in git; none of what makes it *yours* is. Your config, profile,
master resumes, applicant answers, the job database and everything already
tailored are all gitignored on purpose — a public repo is the wrong place for
an API key and a job search.

Copying them by hand has one trap worth automating around. SQLite in WAL mode
keeps recent writes in a `-wal` file beside the database, and that file is
routinely larger than the database itself. Copy `jobs.db` alone and you lose
every decision made since the last checkpoint. `export` checkpoints first, so
the archive holds one complete file.
"""

from __future__ import annotations

import logging
import sqlite3
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

# What a working setup consists of, beyond the code.
PERSONAL_FILES = [
    ("config.yaml", "path", "search terms, filters and credentials"),
    ("profile.md", "profile_path", "your background, for scoring"),
    ("resume.json", "resume_path", "master resume"),
    ("applicant.yaml", "applicant_path", "form answers"),
    ("photo.jpg", "photo_path", "headshot for the resume header"),
]

# Not carried over: .jobpipe-token. A secret is better regenerated on the new
# machine than copied around, and the extension needs re-pointing there anyway.
SKIP = {".jobpipe-token"}


def checkpoint(db_path: Path) -> None:
    """Fold the write-ahead log back into the database file.

    Without this the database on disk is missing everything written since the
    last automatic checkpoint — which, on a busy day, is most of it.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def collect(config) -> list[Path]:
    """Every personal file that exists, for this config."""
    found: list[Path] = []

    for _, attribute, _ in PERSONAL_FILES:
        value = getattr(config, attribute, None) or ""
        path = Path(value)
        if value and path.is_file():
            found.append(path)

    # Per-language masters: resume.de.json and friends.
    master = Path(config.resume_path)
    if master.parent.is_dir():
        found += sorted(
            p for p in master.parent.glob(f"{master.stem}.*{master.suffix}")
            if p.is_file() and p not in found
        )
    for path in (config.resume_paths or {}).values():
        candidate = Path(path)
        if candidate.is_file() and candidate not in found:
            found.append(candidate)

    database = Path(config.db_path)
    if database.is_file():
        found.append(database)

    output = Path(config.output_dir)
    if output.is_dir():
        found += sorted(p for p in output.rglob("*") if p.is_file())

    return [p for p in found if p.name not in SKIP]


def export(config, archive: str | Path) -> tuple[Path, int]:
    """Bundle a working setup into one zip. Returns the path and file count."""
    target = Path(archive)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")

    database = Path(config.db_path)
    if database.is_file():
        checkpoint(database)

    files = collect(config)
    if not files:
        raise FileNotFoundError(
            "nothing to export — is this the folder your config.yaml lives in?"
        )

    base = Path.cwd()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            try:
                name = path.resolve().relative_to(base.resolve()).as_posix()
            except ValueError:
                # Configured somewhere outside the project; keep the name only.
                name = path.name
            bundle.write(path, name)
        bundle.writestr("WHERE-THIS-CAME-FROM.txt", _readme(files))

    return target, len(files)


def _readme(files: list[Path]) -> str:
    listing = "\n".join(f"  {p.as_posix()}" for p in files[:40])
    if len(files) > 40:
        listing += f"\n  ... and {len(files) - 40} more"
    return (
        "jobpipe — personal setup\n"
        "========================\n\n"
        "Unzip this into a fresh clone of the repository, over the top:\n\n"
        "  git clone https://github.com/Dvaghani/Job-Apply-automation\n"
        "  cd Job-Apply-automation\n"
        "  unzip /path/to/this.zip\n\n"
        "Then set the machine up:\n\n"
        "  python3 -m venv .venv\n"
        "  source .venv/bin/activate      # Windows: .venv\\\\Scripts\\\\activate\n"
        "  pip install -e '.[browser]'\n"
        "  playwright install chromium\n"
        "  jobpipe doctor\n\n"
        "The extension token is deliberately not included — a new one is\n"
        "written on first run, and the extension needs re-pointing anyway.\n\n"
        "Contents:\n" + listing + "\n"
    )
