"""Fabrication checks on tailored output.

An LLM rewriting your resume can quietly invent things: inflate a metric,
add a technology you never used, promote you a level. That is the single
worst failure mode here — it is a lie on a document with your name on it,
and you may not notice before an interviewer does.

So nothing generated is trusted. Every tailored bullet is checked back
against the bullet it claims to come from, and every claim-like token is
checked against the master resume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A number with optional thousands separators, decimal, magnitude suffix
# and/or percent: 2,000,000  2M  1.5k  40%  6
_NUMBER = re.compile(r"(\d[\d,]*\.?\d*)\s*([kKmMbB])?(%?)")

_MAGNITUDE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*")

# Shapes that assert a technology even at the start of a sentence: all-caps
# acronyms (AWS, SQL), internal-capital names (PostgreSQL, GraphQL), or
# anything carrying a digit (S3, EC2).
_STRONG_CLAIM = re.compile(
    r"^(?:[A-Z]{2,}[A-Za-z0-9+#.]*|[A-Za-z]+[A-Z][A-Za-z0-9+#.]*|[A-Za-z]*\d[A-Za-z0-9+#.]*)$"
)

# Ordinary English words and structural acronyms — capitalized, but not claims.
_IGNORE_TOKENS = {
    "I", "A", "AN", "AND", "OR", "THE", "OF", "TO", "IN", "ON", "AT", "FOR",
    "WITH", "BY", "AS", "IS", "IT", "CV", "OK", "US", "UK", "EU", "AM", "PM",
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
    "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
}


@dataclass
class Finding:
    """One thing that failed verification."""

    kind: str      # "number" | "vocabulary" | "index"
    where: str     # human-readable location
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.detail}"


def numbers_in(text: str) -> set[float]:
    """Every numeric value in `text`, normalized.

    "2,000,000", "2M" and "2 M" all reduce to 2000000.0, so a model
    reformatting a figure is not mistaken for changing it.
    """
    values: set[float] = set()
    for raw, suffix, _pct in _NUMBER.findall(text or ""):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suffix:
            value *= _MAGNITUDE[suffix.lower()]
        values.add(value)
    return values


def claim_tokens(text: str) -> set[str]:
    """Technology/proper-noun-like tokens asserted by `text`.

    Capitalization alone can't separate "Kubernetes" from "Built", so
    position decides: a capitalized word *mid-sentence* is a proper noun,
    while the first word of a sentence is capitalized by grammar and only
    counts if its shape is independently claim-like (AWS, PostgreSQL, S3).
    """
    found = set()
    for sentence in _SENTENCE.split(text or ""):
        for position, word in enumerate(_WORD.findall(sentence)):
            token = word.strip(".-")
            if len(token) < 2 or token.upper() in _IGNORE_TOKENS:
                continue
            if _STRONG_CLAIM.match(token) or (token[0].isupper() and position > 0):
                found.add(token)
    return found


def check_numbers(source: str, tailored: str, where: str) -> list[Finding]:
    """Any figure in the rewrite must already exist in the source bullet."""
    invented = numbers_in(tailored) - numbers_in(source)
    if not invented:
        return []
    shown = ", ".join(_fmt(n) for n in sorted(invented))
    return [
        Finding(
            "number",
            where,
            f"rewrite asserts {shown}, absent from the source bullet",
        )
    ]


def check_vocabulary(corpus: str, tailored: str, where: str) -> list[Finding]:
    """Any technology named in the rewrite must appear somewhere in the master."""
    haystack = (corpus or "").lower()
    invented = sorted(
        t for t in claim_tokens(tailored) if t.lower() not in haystack
    )
    if not invented:
        return []
    return [
        Finding(
            "vocabulary",
            where,
            f"names {', '.join(invented)}, absent from your master resume",
        )
    ]


def _fmt(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def check_tailoring(resume, tailoring, job_text: str = "") -> list[Finding]:
    """Verify a whole `Tailoring` against the master resume.

    Returns every problem found. An empty list means the output asserts
    nothing the master doesn't already support.
    """
    findings: list[Finding] = []
    master = resume.full_text()
    by_index = {role.index: role for role in resume.roles}

    for chosen in tailoring.roles:
        role = by_index.get(chosen.role_index)
        if role is None:
            findings.append(
                Finding(
                    "index",
                    f"role {chosen.role_index}",
                    "no such role in the master resume",
                )
            )
            continue

        for bullet in chosen.bullets:
            where = f"{role.name} bullet {bullet.source_index}"
            if not 0 <= bullet.source_index < len(role.highlights):
                findings.append(
                    Finding(
                        "index",
                        where,
                        f"role has {len(role.highlights)} bullets",
                    )
                )
                continue
            source = role.highlights[bullet.source_index]
            findings += check_numbers(source, bullet.text, where)
            findings += check_vocabulary(master, bullet.text, where)

    # The summary and skills draw on the whole resume, not one bullet.
    findings += check_numbers(master, tailoring.summary, "summary")
    findings += check_vocabulary(master, tailoring.summary, "summary")

    for skill in tailoring.selected_skills:
        if skill.lower() not in master.lower():
            findings.append(
                Finding("vocabulary", "skills", f"'{skill}' is not in your master resume")
            )

    if tailoring.cover_letter:
        # A cover letter may legitimately name the employer and quote the
        # posting, so the posting counts as supporting context here.
        findings += check_numbers(master, tailoring.cover_letter, "cover letter")
        findings += check_vocabulary(
            master + " " + job_text, tailoring.cover_letter, "cover letter"
        )

    return findings
