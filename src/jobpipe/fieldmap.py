"""Classify a form field into a canonical key.

Pure logic, no browser — which is the point. Field matching is where an
autofiller is actually right or wrong, so it stays testable in isolation.
"""

from __future__ import annotations

import re

# Fields we will fill from your applicant file.
FILLABLE = {
    "first_name", "last_name", "full_name", "preferred_name", "email", "phone",
    "location", "address", "city", "linkedin", "github", "portfolio",
    "current_company", "current_title", "resume", "cover_letter",
    "work_authorization", "sponsorship", "years_experience",
    "salary_expectation", "start_date", "how_heard", "pronouns",
}

# Voluntary self-identification. Never filled unless you opt in: these are
# yours to answer or decline, and a bot guessing them is not acceptable.
EEO = {"gender", "race", "ethnicity", "veteran_status", "disability_status"}

# First match wins, so order is the specification. More specific patterns
# must precede the general ones they would otherwise be swallowed by —
# "current company" before "company", "first name" before "name".
PATTERNS: list[tuple[str, str]] = [
    ("first_name", r"\bfirst[\s_-]*name\b|\bgiven[\s_-]*name\b|\bfname\b|\bforename\b"),
    ("last_name", r"\blast[\s_-]*name\b|\bsurname\b|\bfamily[\s_-]*name\b|\blname\b"),
    ("preferred_name", r"\bpreferred[\s_-]*(first[\s_-]*)?name\b|\bnickname\b"),
    ("email", r"\be[\s_-]?mail\b"),
    ("phone", r"\bphone\b|\bmobile\b|\btelephone\b|\bcell\b"),

    ("linkedin", r"\blinked[\s_-]?in\b"),
    ("github", r"\bgit[\s_-]?hub\b"),
    ("portfolio", r"\bportfolio\b|\bpersonal[\s_-]*(web)?site\b|\bwebsite\b|\bweb[\s_-]*site\b"),

    ("cover_letter", r"\bcover[\s_-]*letter\b"),
    ("resume", r"\bresum\w*\b|\bcv\b|\bc\.v\.\b"),

    # Sponsorship first: "require sponsorship to work" also contains "work".
    ("sponsorship", r"\bsponsor\w*\b|\bvisa[\s_-]*support\b"),
    ("work_authorization",
     r"\bauthoriz\w*\b|\bauthoris\w*\b|\blegally\b|\bright[\s_-]*to[\s_-]*work\b"
     r"|\bwork[\s_-]*permit\b|\beligible[\s_-]*to[\s_-]*work\b"),

    ("years_experience",
     r"\byears?\b.{0,20}\bexperience\b|\bhow[\s_-]*many[\s_-]*years\b|\byoe\b"),
    ("salary_expectation",
     r"\bsalary\b|\bcompensation\b|\bexpected[\s_-]*(pay|comp\w*)\b|\bdesired[\s_-]*pay\b"),
    ("start_date",
     r"\bstart[\s_-]*date\b|\bavailab\w*[\s_-]*(to[\s_-]*start|date)\b|\bnotice[\s_-]*period\b"),
    ("how_heard", r"\bhow[\s_-]*did[\s_-]*you[\s_-]*hear\b|\breferr?al[\s_-]*source\b|\bsource\b"),

    ("current_company", r"\bcurrent[\s_-]*(company|employer)\b|\bemployer\b|\bcompany\b"),
    ("current_title", r"\bcurrent[\s_-]*(title|role|position)\b|\bjob[\s_-]*title\b|\btitle\b"),

    ("pronouns", r"\bpronouns?\b"),

    ("location", r"\blocation\b|\bwhere[\s_-]*are[\s_-]*you\b|\bbased\b"),
    ("city", r"\bcity\b|\btown\b"),
    ("address", r"\baddress\b|\bstreet\b"),

    # Self-ID, detected so it can be deliberately skipped.
    ("gender", r"\bgender\b|\bsex\b"),
    ("ethnicity", r"\bethnic\w*\b|\bhispanic\b|\blatino\b"),
    ("race", r"\brace\b|\bracial\b"),
    ("veteran_status", r"\bveteran\b|\bmilitary\b|\bprotected[\s_-]*veteran\b"),
    ("disability_status", r"\bdisabilit\w*\b|\bdisabled\b"),

    # Last: a bare "name" only after every qualified name has been tried.
    ("full_name", r"\bfull[\s_-]*name\b|\byour[\s_-]*name\b|^name$|\bname\b"),
]

COMPILED = [(key, re.compile(pattern)) for key, pattern in PATTERNS]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase and reduce punctuation to single spaces."""
    return _NON_ALNUM.sub(" ", (text or "").lower()).strip()


def classify(
    label: str = "",
    name: str = "",
    placeholder: str = "",
    aria_label: str = "",
    field_type: str = "",
) -> str | None:
    """Return the canonical key for a field, or None if unrecognized.

    The visible label is the strongest signal and is tried alone first —
    a `name` attribute like "cand_email_2" shouldn't outvote a label
    reading "Current company".
    """
    if field_type == "file":
        # A file input's label is often just "Attach"; resume is the
        # overwhelmingly common case, so fall through to patterns and
        # default to resume only if nothing matches.
        matched = _match(" ".join([label, aria_label, name, placeholder]))
        return matched if matched in {"resume", "cover_letter"} else "resume"

    for candidate in (label, aria_label, placeholder, name):
        if not candidate:
            continue
        matched = _match(candidate)
        if matched:
            return matched
    return None


def _match(text: str) -> str | None:
    normalized = normalize(text)
    if not normalized:
        return None
    for key, pattern in COMPILED:
        if pattern.search(normalized):
            return key
    return None


def is_eeo(key: str | None) -> bool:
    return key in EEO
