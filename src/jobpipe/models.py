"""Normalized job posting shared by every source."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Status lifecycle. A job only ever moves forward through these.
STATUS_NEW = "new"            # ingested, not yet scored
STATUS_SCORED = "scored"      # has a fit score, waiting in the review queue
STATUS_APPROVED = "approved"  # I said yes — ready to tailor and apply
STATUS_REJECTED = "rejected"  # I said no, or a hard filter killed it
STATUS_APPLIED = "applied"    # submitted

ALL_STATUSES = (
    STATUS_NEW,
    STATUS_SCORED,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_APPLIED,
)

_WHITESPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
# Trailing noise companies append to titles. Two shapes, both common:
#   a bracketed suffix of any content — "(Bengaluru, India)", "[L5]" — which
#   is nearly always office or level; and a dash/comma suffix naming a
#   work arrangement or region — "- US", ", Remote".
_TITLE_BRACKET = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
_TITLE_SUFFIX = re.compile(r"[\-–—,]\s*(remote|hybrid|on-?site|us|usa|emea|uk)\b.*$", re.I)


def strip_html(raw: str) -> str:
    """Crude HTML -> text. Good enough for feeding a description to an LLM."""
    if not raw:
        return ""
    text = raw.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    text = text.replace("&quot;", '"')
    text = _TAG.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def normalize_title(title: str) -> str:
    """Lowercase a title and drop trailing location/level noise, for dedup."""
    cleaned = title or ""
    # Repeat: a title can carry several, e.g. "Engineer (Backend) (Remote)".
    while True:
        stripped = _TITLE_BRACKET.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
    cleaned = _TITLE_SUFFIX.sub("", cleaned)
    return _WHITESPACE.sub(" ", cleaned).strip().lower()


def normalize_company(company: str) -> str:
    """Lowercase a company name and drop legal suffixes, for dedup."""
    name = (company or "").strip().lower()
    name = re.sub(r"[.,]", "", name)
    name = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|co)\b", "", name)
    return _WHITESPACE.sub(" ", name).strip()


@dataclass
class Job:
    """A posting, normalized across sources.

    `source` and `source_id` together identify the posting upstream;
    `fingerprint` identifies the *role* so the same job listed on two
    boards collapses into one row.
    """

    source: str
    source_id: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    remote: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: str | None = None  # ISO 8601
    raw: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable hash of (company, title) — the dedup key.

        Location is deliberately excluded: the same role posted to three
        offices is one decision for me, not three.
        """
        basis = f"{normalize_company(self.company)}|{normalize_title(self.title)}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    def is_remote(self) -> bool:
        """True if the posting looks remote, from either flag or location text."""
        if self.remote:
            return True
        return "remote" in (self.location or "").lower()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
