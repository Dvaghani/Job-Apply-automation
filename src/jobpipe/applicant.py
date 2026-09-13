"""Your details and stock answers, for filling forms."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .fieldmap import EEO


class ApplicantError(RuntimeError):
    pass


@dataclass
class Applicant:
    fields: dict = field(default_factory=dict)
    answers: dict = field(default_factory=dict)
    fill_eeo: bool = False

    def value_for(self, key: str) -> str | None:
        """The value to type into a field of this kind, or None to skip."""
        if key in EEO and not self.fill_eeo:
            return None
        value = self.fields.get(key)
        if value is None and key == "full_name":
            first, last = self.fields.get("first_name"), self.fields.get("last_name")
            if first and last:
                return f"{first} {last}"
        return str(value) if value is not None else None

    def answer_for(self, question: str) -> str | None:
        """Look up a free-text question in the answer bank.

        Matching is substring-based on a normalized question, so one entry
        covers the many phrasings of the same question.
        """
        from .fieldmap import normalize

        asked = normalize(question)
        if not asked:
            return None
        best = None
        for pattern, answer in self.answers.items():
            key = normalize(pattern)
            if key and key in asked:
                # Prefer the most specific match.
                if best is None or len(key) > len(best[0]):
                    best = (key, answer)
        return str(best[1]) if best else None


def load(path: str | Path) -> Applicant:
    p = Path(path)
    if not p.exists():
        raise ApplicantError(
            f"Applicant file not found at {p}. Copy applicant.example.yaml to {p} "
            "and fill in your details."
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ApplicantError(f"{p} must be a YAML mapping.")

    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        raise ApplicantError(f"{p}: `fields` must be a mapping.")
    answers = data.get("answers") or {}
    if not isinstance(answers, dict):
        raise ApplicantError(f"{p}: `answers` must be a mapping.")

    return Applicant(
        fields=fields,
        answers=answers,
        fill_eeo=bool(data.get("fill_self_identification", False)),
    )
