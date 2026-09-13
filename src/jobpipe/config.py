"""Config loading: watchlist, hard filters, and your profile."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_PROFILE_PATH = Path("profile.md")

# Opus is the default because scoring quality is the whole point of this
# stage — a bad score wastes a real application slot. Override `model` in
# config.yaml with claude-sonnet-5 or claude-haiku-4-5 to cut cost.
DEFAULT_MODEL = "claude-opus-5"

# Backend default. "claude-cli" runs on a Claude Pro/Max subscription via the
# Claude Code CLI; "api" needs ANTHROPIC_API_KEY and separate API credits.
DEFAULT_BACKEND = "api"
# The CLI takes aliases (opus/sonnet/haiku) rather than full model ids.
DEFAULT_CLI_MODEL = "sonnet"


class ConfigError(RuntimeError):
    pass


@dataclass
class Filters:
    """Cheap, deterministic rules applied before any LLM call.

    Every field is optional; an unset field means "don't filter on this".
    """

    min_salary: int | None = None
    locations: list[str] = field(default_factory=list)
    remote_only: bool = False
    title_include: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)
    max_age_days: int | None = None


@dataclass
class Config:
    sources: dict = field(default_factory=dict)
    filters: Filters = field(default_factory=Filters)
    model: str = DEFAULT_MODEL
    backend: str = DEFAULT_BACKEND
    cli_model: str = DEFAULT_CLI_MODEL
    cli_timeout: int = 300
    min_score: int = 60
    db_path: str = "jobs.db"
    profile_path: str = str(DEFAULT_PROFILE_PATH)
    resume_path: str = "resume.json"
    language: str = "en"
    resume_paths: dict = field(default_factory=dict)
    applicant_path: str = "applicant.yaml"
    output_dir: str = "applications"
    raw: dict = field(default_factory=dict)
    path: str = ""

    @property
    def greenhouse_boards(self) -> list[str]:
        return list(self.sources.get("greenhouse", []) or [])

    @property
    def lever_sites(self) -> list[str]:
        return list(self.sources.get("lever", []) or [])

    @property
    def ashby_orgs(self) -> list[str]:
        return list(self.sources.get("ashby", []) or [])

    @property
    def smartrecruiters_companies(self) -> list[str]:
        return list(self.sources.get("smartrecruiters", []) or [])

    @property
    def adzuna(self) -> dict:
        return dict(self.sources.get("adzuna", {}) or {})

    def resume_for(self, language: str = "en") -> str:
        """The master resume to tailor from, for one output language.

        A German application wants German source material: the sections
        copied verbatim from the master (projects, thesis, languages) are not
        translated by the model, so tailoring a German resume from the
        English master produces a half-English document. Falling back
        silently would hide that, so a missing master is an error that says
        what to do about it.
        """
        if not language or language == "en":
            return self.resume_path

        explicit = (self.resume_paths or {}).get(language)
        if explicit:
            return explicit

        # Convention: resume.json -> resume.de.json, alongside it.
        base = Path(self.resume_path)
        guess = base.parent / f"{base.stem}.{language}{base.suffix}"
        if guess.exists():
            return str(guess)

        raise ConfigError(
            f"No {language} master resume. Tailoring in {language} needs one, "
            f"because the sections copied verbatim from the master are not "
            f"translated. Create {guess}, or point at it in config.yaml:\n"
            f"  resume_paths:\n"
            f"    {language}: path/to/your-resume.{language}.json"
        )

    def load_profile(self) -> str:
        path = Path(self.profile_path)
        if not path.exists():
            raise ConfigError(
                f"Profile not found at {path}. Copy profile.example.md to {path} "
                "and fill in your background — the scorer needs it."
            )
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ConfigError(f"Profile at {path} is empty.")
        return text


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(
            f"Config not found at {cfg_path}. Copy config.example.yaml to {cfg_path} "
            "and edit the watchlist."
        )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{cfg_path} must be a YAML mapping.")

    raw_filters = data.get("filters") or {}
    if not isinstance(raw_filters, dict):
        raise ConfigError("`filters` must be a mapping.")

    known = {f.name for f in Filters.__dataclass_fields__.values()}
    unknown = set(raw_filters) - known
    if unknown:
        raise ConfigError(
            f"Unknown filter key(s): {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(sorted(known))}."
        )

    filters = Filters(
        min_salary=raw_filters.get("min_salary"),
        locations=[s.lower() for s in raw_filters.get("locations", []) or []],
        remote_only=bool(raw_filters.get("remote_only", False)),
        title_include=[s.lower() for s in raw_filters.get("title_include", []) or []],
        title_exclude=[s.lower() for s in raw_filters.get("title_exclude", []) or []],
        max_age_days=raw_filters.get("max_age_days"),
    )

    return Config(
        sources=data.get("sources") or {},
        filters=filters,
        model=data.get("model") or DEFAULT_MODEL,
        backend=(data.get("backend") or DEFAULT_BACKEND).lower(),
        cli_model=data.get("cli_model") or DEFAULT_CLI_MODEL,
        cli_timeout=int(data.get("cli_timeout", 300)),
        min_score=int(data.get("min_score", 60)),
        db_path=data.get("db_path") or "jobs.db",
        profile_path=data.get("profile_path") or str(DEFAULT_PROFILE_PATH),
        resume_path=data.get("resume_path") or "resume.json",
        language=(data.get("language") or "en").lower(),
        resume_paths=data.get("resume_paths") or {},
        applicant_path=data.get("applicant_path") or "applicant.yaml",
        output_dir=data.get("output_dir") or "applications",
        raw=data,
        path=str(cfg_path),
    )
