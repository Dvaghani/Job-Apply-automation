"""Master resume: load, validate, render.

The master lives in JSON Resume format (https://jsonresume.org/schema/) —
a widely used, structured, machine-readable shape. Structure matters here:
tailoring selects and rewrites individual bullets, and that only works if
the bullets are addressable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class ResumeError(RuntimeError):
    pass


# Section headings per output language. A German resume under English
# headings reads as a translation accident, and "Present" in a date range
# gives it away immediately.
HEADINGS = {
    "en": {
        "summary": "Summary", "experience": "Experience", "projects": "Projects",
        "skills": "Skills", "education": "Education", "languages": "Languages",
        "present": "Present",
    },
    "de": {
        "summary": "Profil", "experience": "Berufserfahrung", "projects": "Projekte",
        "skills": "Kenntnisse", "education": "Ausbildung", "languages": "Sprachen",
        "present": "heute",
    },
}


def headings(language: str) -> dict:
    return HEADINGS.get(language, HEADINGS["en"])


@dataclass
class Role:
    """One `work` entry, with its highlights addressable by index."""

    index: int
    name: str
    position: str
    start: str = ""
    end: str = ""
    location: str = ""
    highlights: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.highlights is None:
            self.highlights = []

    @property
    def dates(self) -> str:
        if self.start and self.end:
            return f"{self.start} – {self.end}"
        if self.start:
            return f"{self.start} – Present"
        return self.end or ""


@dataclass
class Resume:
    basics: dict
    roles: list[Role]
    education: list[dict]
    skills: list[dict]
    projects: list[dict]
    raw: dict
    # Defaulted so it trails `raw`: appending here keeps every existing
    # positional construction of Resume working.
    languages: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.basics.get("name", "")

    def all_skill_keywords(self) -> list[str]:
        out: list[str] = []
        for group in self.skills:
            out.extend(group.get("keywords") or [])
            if group.get("name"):
                out.append(group["name"])
        return out

    def full_text(self) -> str:
        """Every word in the master, for fabrication checks."""
        return json.dumps(self.raw, ensure_ascii=False)


def load(path: str | Path) -> Resume:
    p = Path(path)
    if not p.exists():
        raise ResumeError(
            f"Resume not found at {p}. Copy resume.example.json to {p} "
            "and fill it in with your real history."
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResumeError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ResumeError(f"{p} must be a JSON object.")

    work = data.get("work") or []
    if not isinstance(work, list) or not work:
        raise ResumeError(f"{p} has no `work` entries — nothing to tailor.")

    roles = []
    for i, item in enumerate(work):
        if not isinstance(item, dict):
            raise ResumeError(f"{p}: work[{i}] is not an object.")
        highlights = item.get("highlights") or []
        if not isinstance(highlights, list):
            raise ResumeError(f"{p}: work[{i}].highlights must be a list.")
        roles.append(
            Role(
                index=i,
                name=item.get("name") or item.get("company") or "",
                position=item.get("position", ""),
                start=item.get("startDate", ""),
                end=item.get("endDate", ""),
                location=item.get("location", ""),
                highlights=[str(h) for h in highlights],
            )
        )

    if not any(r.highlights for r in roles):
        raise ResumeError(
            f"{p}: no work entry has any `highlights`. Tailoring selects and "
            "rephrases bullets, so there must be bullets to work from."
        )

    return Resume(
        basics=data.get("basics") or {},
        roles=roles,
        education=data.get("education") or [],
        skills=data.get("skills") or [],
        projects=data.get("projects") or [],
        raw=data,
        languages=data.get("languages") or [],
    )


def role_dates(role: Role, language: str = "en") -> str:
    """A role's date range, with "Present" in the output language."""
    if role.start and role.end:
        return f"{role.start} – {role.end}"
    if role.start:
        return f"{role.start} – {headings(language)['present']}"
    return role.end or ""


def render_markdown(resume: Resume, tailored, language: str = "en") -> str:
    """Render a tailored resume to Markdown.

    `tailored` is a `tailor.Tailoring`; only the bullets it selected are
    emitted, in the order it chose.
    """
    h = headings(language)
    b = resume.basics
    lines: list[str] = [f"# {b.get('name', '')}"]

    contact = [b.get("email"), b.get("phone")]
    loc = b.get("location") or {}
    city = ", ".join(x for x in [loc.get("city"), loc.get("region")] if x)
    if city:
        contact.append(city)
    for profile in b.get("profiles") or []:
        if profile.get("url"):
            contact.append(profile["url"])
    contact = [c for c in contact if c]
    if contact:
        lines += ["", " · ".join(contact)]

    if tailored.summary:
        lines += ["", "## " + h["summary"], "", tailored.summary]

    selected = {r.role_index: r for r in tailored.roles}
    if selected:
        lines += ["", "## " + h["experience"]]
        for role in resume.roles:
            chosen = selected.get(role.index)
            if not chosen or not chosen.bullets:
                continue
            lines += ["", f"### {role.position} — {role.name}"]
            meta = " · ".join(
                x for x in [role_dates(role, language), role.location] if x
            )
            if meta:
                lines += [f"*{meta}*"]
            lines += [""]
            lines += [f"- {bullet.text}" for bullet in chosen.bullets]

    # Everything below is copied from the master, not rewritten by the model.
    # That is deliberate: it is the reason these sections need no verification
    # pass — there is no route by which a project or a thesis could acquire a
    # claim you did not write yourself.
    if resume.projects:
        lines += ["", "## " + h["projects"]]
        for project in resume.projects:
            lines += ["", f"### {project.get('name', '')}"]
            if project.get("description"):
                lines += [f"*{project['description']}*", ""]
            lines += [f"- {item}" for item in project.get("highlights") or []]

    if tailored.selected_skills:
        lines += ["", "## " + h["skills"], "", ", ".join(tailored.selected_skills)]

    if resume.education:
        lines += ["", "## " + h["education"]]
        for edu in resume.education:
            degree = " ".join(
                x for x in [edu.get("studyType"), edu.get("area")] if x
            )
            dates = " – ".join(
                x for x in [edu.get("startDate"), edu.get("endDate")] if x
            )
            entry = f"**{edu.get('institution', '')}**"
            if degree:
                entry += f" — {degree}"
            if dates:
                entry += f" ({dates})"
            lines += ["", entry]
            if edu.get("location"):
                lines += [f"*{edu['location']}*"]
            # The thesis lives here. On a perception or ADAS posting it is
            # often the single most relevant thing on the page, and it was
            # being dropped entirely.
            if edu.get("summary"):
                lines += ["", edu["summary"]]

    if resume.languages:
        spoken = ", ".join(
            f"{item.get('language', '')} ({item.get('fluency', '')})".replace(" ()", "")
            for item in resume.languages
            if item.get("language")
        )
        if spoken:
            lines += ["", "## " + h["languages"], "", spoken]

    return "\n".join(lines).strip() + "\n"


HTML_SHELL = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<title>{name} — {role}</title>
<style>
  body {{ max-width: 7.5in; margin: 0 auto; padding: 0.6in 0.5in;
         font: 11pt/1.45 Georgia, "Times New Roman", serif; color: #111; }}
  h1 {{ font-size: 20pt; margin: 0 0 2px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 11pt; text-transform: uppercase; letter-spacing: 0.08em;
        border-bottom: 1px solid #bbb; padding-bottom: 3px;
        margin: 18px 0 8px; }}
  h3 {{ font-size: 11.5pt; margin: 12px 0 1px; }}
  p, li {{ margin: 3px 0; }}
  em {{ color: #555; font-size: 10pt; }}
  ul {{ margin: 5px 0 0; padding-left: 18px; }}
  .contact {{ color: #444; font-size: 10pt; margin-bottom: 4px; }}
  @media print {{ body {{ padding: 0.4in; }} @page {{ margin: 0.5in; }} }}
</style></head><body>
{body}
</body></html>
"""


def render_html(
    resume: Resume, tailored, job_title: str = "", language: str = "en"
) -> str:
    """Very small Markdown subset -> HTML. Print this to get a PDF."""
    md = render_markdown(resume, tailored, language)
    out: list[str] = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for i, line in enumerate(md.splitlines()):
        stripped = line.strip()
        if not stripped:
            close_list()
            continue
        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        close_list()
        if stripped.startswith("### "):
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif i < 4 and "·" in stripped:
            out.append(f'<p class="contact">{_inline(stripped)}</p>')
        else:
            out.append(f"<p>{_inline(stripped)}</p>")
    close_list()

    return HTML_SHELL.format(
        name=_escape(resume.name), role=_escape(job_title),
        lang=language, body="\n".join(out),
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _inline(text: str) -> str:
    """Escape, then apply **bold** and *italic*."""
    import re

    out = _escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", out)
    return out


# --- cover letter ---------------------------------------------------------
#
# Application forms take a PDF. A Markdown file is rejected outright by most
# ATS upload fields, so the letter needs the same treatment as the resume:
# rendered to something an employer can actually open.

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"],
}

SUBJECT = {
    "en": "Application for {title}",
    "de": "Bewerbung als {title}",
}

LETTER_SHELL = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<title>{name} — {subject}</title>
<style>
  body {{ max-width: 7.5in; margin: 0 auto; padding: 0.8in 0.7in;
         font: 11pt/1.6 Georgia, "Times New Roman", serif; color: #111; }}
  .sender {{ font-size: 10pt; color: #444; margin-bottom: 28px; }}
  .sender b {{ display: block; font-size: 12pt; color: #111; margin-bottom: 2px; }}
  .to {{ margin-bottom: 18px; }}
  .date {{ margin-bottom: 26px; }}
  .subject {{ font-weight: bold; margin-bottom: 22px; }}
  p {{ margin: 0 0 12px; text-align: justify; }}
  @media print {{ body {{ padding: 0.6in; }} @page {{ margin: 0.6in; }} }}
</style></head><body>
<div class="sender"><b>{name}</b>{contact}</div>
<div class="to">{company}</div>
<div class="date">{date}</div>
<div class="subject">{subject}</div>
{body}
</body></html>
"""


def letter_date(language: str = "en", city: str = "", today=None) -> str:
    """Today's date as a letter carries it.

    German letters conventionally lead with the place of writing; English
    ones do not.
    """
    import datetime

    day = today or datetime.date.today()
    month = MONTHS.get(language, MONTHS["en"])[day.month - 1]
    if language == "de":
        stamp = f"{day.day}. {month} {day.year}"
        return f"{city}, {stamp}" if city else stamp
    return f"{day.day} {month} {day.year}"


def render_cover_html(
    resume: Resume, text: str, company: str = "", job_title: str = "",
    language: str = "en", today=None,
) -> str:
    """Lay the cover letter out as a letter.

    The body is emitted verbatim — the salutation and sign-off come from the
    model, so nothing is added around it that could end up duplicated.
    """
    basics = resume.basics
    location = basics.get("location") or {}
    city = location.get("city", "")

    bits = [basics.get("email"), basics.get("phone"), city]
    contact = " · ".join(_escape(b) for b in bits if b)

    paragraphs = [
        f"<p>{_escape(block.strip()).replace(chr(10), '<br>')}</p>"
        for block in (text or "").split("\n\n")
        if block.strip()
    ]

    return LETTER_SHELL.format(
        lang=language,
        name=_escape(resume.name),
        contact=contact,
        company=_escape(company),
        date=_escape(letter_date(language, city, today)),
        subject=_escape(
            SUBJECT.get(language, SUBJECT["en"]).format(title=job_title)
        ),
        body="\n".join(paragraphs),
    )
