"""Tailor the master resume to one posting, using Claude.

The model may only *select and rephrase* what the master resume already
says. It cannot add experience, inflate a number, or claim a technology
you never listed — and `verify` checks that it didn't.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from . import llm
from .llm import LLMError
from .resume import Resume, render_cover_html, render_html, render_markdown
from .verify import check_tailoring

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 8000

# Two pages is the convention almost everywhere, and the length a
# recruiter actually reads. Past it, say so rather than let it ship.
RESUME_PAGE_LIMIT = 2

SYSTEM = """You tailor one candidate's existing resume to one job posting.

THE ONE RULE: you may only select, reorder and rephrase material that is \
already in the master resume. You may not add experience, invent or inflate \
a metric, claim a technology the master does not list, or change a job title \
or seniority. If the candidate lacks something the posting wants, leave it \
out — do not paper over it. Inventing a qualification is worse than a weaker \
application: it is a false claim on a document the candidate will be \
interviewed against.

Within that rule, tailor hard:

- Select the bullets that matter for THIS posting and drop the rest. A \
focused resume beats a complete one. Prefer 3-5 bullets on recent, relevant \
roles and 1-2 on older ones.
- Rephrase each selected bullet toward the posting's own vocabulary, so the \
same true fact is stated in the words this employer uses. Keep every number \
exactly as the master states it.
- Lead each bullet with the outcome, not the activity.
- Order skills by relevance to the posting; include only skills the master \
already lists.
- Write a summary of 2-3 lines aimed squarely at this role.
- In `selected_projects`, give the indices of the projects worth the space \
for THIS posting and leave the rest out. Drop one whose substance already \
appears in a role bullet: repeating it costs a third of a page and tells the \
reader nothing new.
- Aim for two pages. Education, projects and languages are printed in full \
from the master and you cannot shorten them, so the length you control is the \
number of bullets — cut harder rather than keep a weak one.

For `source_index`, give the index of the master bullet each rewrite came \
from, counting from 0 within that role. This is checked, so it must be \
accurate.

The cover letter, if asked for, is at most 200 words: why this company, what \
you would do in the role, grounded only in facts from the master resume. No \
throat-clearing, no "I am writing to apply", no restating the resume.

Write it as a complete letter, because it is sent as one: open with a \
salutation, break it into paragraphs, and close with a sign-off and the \
candidate's name. Follow the conventions of the output language — a German \
Anschreiben opens "Sehr geehrte Damen und Herren," and closes "Mit \
freundlichen Grüßen". Do not write a subject line, a date or an address \
block; those are laid out around your text."""

PROMPT = """<master_resume>
{resume}
</master_resume>

<posting>
Company: {company}
Title: {title}
Location: {location}

{description}
</posting>

Tailor the resume to this posting.{cover}{language}"""


class TailoredBullet(BaseModel):
    source_index: int = Field(
        description="0-based index of the master bullet this came from, within its role"
    )
    text: str = Field(description="The rewritten bullet")


class TailoredRole(BaseModel):
    role_index: int = Field(description="0-based index of the role in the master resume")
    bullets: list[TailoredBullet]


class Tailoring(BaseModel):
    summary: str = Field(description="2-3 line summary aimed at this posting")
    roles: list[TailoredRole]
    selected_skills: list[str] = Field(description="Relevant skills, most relevant first")
    cover_letter: str = Field(default="", description="Cover letter, or empty if not requested")
    selected_projects: list[int] = Field(
        default_factory=list,
        description=(
            "0-based indices of the master's projects worth including for this "
            "posting, most relevant first. Empty means include them all."
        ),
    )
    keywords_matched: list[str] = Field(
        default_factory=list,
        description="Terms from the posting the tailored resume genuinely supports",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="What the posting wants that the master resume does not support",
    )


def _resume_for_prompt(resume: Resume) -> str:
    """Render the master with explicit indices, so the model can cite them."""
    lines = [f"Name: {resume.name}", ""]
    lines.append("WORK (role_index / bullet source_index):")
    for role in resume.roles:
        lines.append(f"\n[role {role.index}] {role.position} — {role.name} ({role.dates})")
        for i, highlight in enumerate(role.highlights):
            lines.append(f"    [{i}] {highlight}")
    if resume.skills:
        lines.append("\nSKILLS:")
        for group in resume.skills:
            keywords = ", ".join(group.get("keywords") or [])
            lines.append(f"  {group.get('name', '')}: {keywords}")
    if resume.projects:
        # Indexed, because the model can only cite an index it was shown.
        lines.append("\nPROJECTS (selected_projects index):")
        for i, project in enumerate(resume.projects):
            lines.append(
                f"  [{i}] {project.get('name', '')}: {project.get('description', '')}"
            )
    if resume.education:
        lines.append("\nEDUCATION:")
        for edu in resume.education:
            lines.append(
                f"  {edu.get('institution', '')} — "
                f"{edu.get('studyType', '')} {edu.get('area', '')} ({edu.get('endDate', '')})"
            )
    return "\n".join(lines)


LANGUAGE_INSTRUCTION = {
    # English is stated as explicitly as German. Left unsaid, the model takes
    # its cue from the posting and writes a German letter for a German job —
    # which is a reasonable guess, and not what was asked for.
    "en": (
        "\n\nWrite every piece of output — summary, bullets and cover letter — "
        "in English, even when the posting itself is written in another "
        "language."
    ),
    "de": (
        "\n\nWrite every piece of output — summary, bullets and cover letter — "
        "in German, in the register a German Lebenslauf and Anschreiben use. "
        "The master resume below is already German: keep its terminology, and "
        "keep established English technical terms (HiL, ECU-TEST, CAN, MQTT) "
        "as they are, because that is how German automotive engineers write "
        "them. Do not translate proper nouns or tool names."
    ),
}


def build_prompt(
    resume: Resume, row, cover_letter: bool, language: str = "en"
) -> str:
    description = (row["description"] or "").strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS] + "\n[truncated]"
    return PROMPT.format(
        resume=_resume_for_prompt(resume),
        company=row["company"],
        title=row["title"],
        location=row["location"] or "not stated",
        description=description or "(no description provided)",
        cover=" Also write the cover letter." if cover_letter else
              " Leave cover_letter empty.",
        language=LANGUAGE_INSTRUCTION.get(language, LANGUAGE_INSTRUCTION["en"]),
    )


def tailor_one(
    backend, resume: Resume, row, cover_letter: bool = True, language: str = "en"
) -> Tailoring:
    return backend.complete(
        SYSTEM, build_prompt(resume, row, cover_letter, language),
        Tailoring, max_tokens=8000,
    )


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or "untitled"


def output_dir(base: str | Path, row) -> Path:
    return Path(base) / f"{slugify(row['company'])}-{slugify(row['title'])}"


def suffix_for(language: str) -> str:
    """File-name suffix for a language. English keeps the plain names.

    So a job tailored in both languages keeps both sets side by side —
    `resume.md` and `resume.de.md` — rather than the second run silently
    overwriting the first.
    """
    return "" if not language or language == "en" else f".{language}"


def write_pdf(html_path: Path, pdf_path: Path) -> tuple[Path | None, int]:
    """Render the resume to PDF now, rather than when a form asks for one.

    Application forms want a PDF, and so does anyone uploading by hand after
    the autofill misses. Rendering it at tailor time means the folder is
    always ready to use. It needs a browser, so a missing Playwright is a
    warning and not a failed tailoring run.
    """
    from .autofill import html_to_pdf, pdf_page_count

    try:
        written = html_to_pdf(html_path, pdf_path)
    except Exception as exc:
        log.warning("could not render %s: %s", pdf_path.name, str(exc)[:100])
        return None, 0
    return written, pdf_page_count(written)


def write_outputs(
    directory: Path,
    resume: Resume,
    tailoring: Tailoring,
    row,
    findings: list,
    language: str = "en",
) -> list[Path]:
    """Write the tailored artifacts. Returns the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    sfx = suffix_for(language)

    resume_md = directory / f"resume{sfx}.md"
    resume_md.write_text(render_markdown(resume, tailoring, language), encoding="utf-8")
    written.append(resume_md)

    resume_html = directory / f"resume{sfx}.html"
    resume_html.write_text(
        render_html(resume, tailoring, row["title"], language), encoding="utf-8"
    )
    written.append(resume_html)

    pdf, pages = write_pdf(resume_html, directory / f"resume{sfx}.pdf")
    if pdf is not None:
        written.append(pdf)
        if pages > RESUME_PAGE_LIMIT:
            log.warning(
                "%s is %d pages — trim bullets, or shorten the master's "
                "education summary", pdf.name, pages,
            )

    if tailoring.cover_letter.strip():
        body = tailoring.cover_letter.strip()

        # Markdown is the editable source. It is not what gets uploaded:
        # an ATS file field rejects .md outright.
        cover = directory / f"cover-letter{sfx}.md"
        cover.write_text(body + "\n", encoding="utf-8")
        written.append(cover)

        cover_html = directory / f"cover-letter{sfx}.html"
        cover_html.write_text(
            render_cover_html(resume, body, row["company"], row["title"], language),
            encoding="utf-8",
        )
        written.append(cover_html)

        cover_pdf, _ = write_pdf(cover_html, directory / f"cover-letter{sfx}.pdf")
        if cover_pdf is not None:
            written.append(cover_pdf)

    notes = [f"# {row['title']} — {row['company']}", "", row["url"], ""]
    if tailoring.keywords_matched:
        notes += ["## Posting terms this resume supports", ""]
        notes += [f"- {k}" for k in tailoring.keywords_matched]
        notes += [""]
    if tailoring.gaps:
        notes += ["## Gaps — what the posting wants that you can't claim", ""]
        notes += [f"- {g}" for g in tailoring.gaps]
        notes += [""]
    notes += ["## Verification", ""]
    if findings:
        notes += [
            "These claims could not be traced back to your master resume.",
            "**Check each one before sending.**",
            "",
        ]
        notes += [f"- {f}" for f in findings]
    else:
        notes += ["Every claim traces back to the master resume. No fabrication found."]

    if language != "en":
        notes += [
            "",
            f"**Read the {language} text yourself.** Verification is weaker "
            "outside English: German capitalises every noun, so a capital "
            "letter no longer marks a proper noun and only distinctively "
            "shaped names (AWS, ECU-TEST, PostgreSQL) can be checked against "
            "your master. An invented technology whose name looks like an "
            "ordinary noun would not be caught here. Numbers are still "
            "checked exactly as in English.",
        ]
    notes_path = directory / f"NOTES{sfx}.md"
    notes_path.write_text("\n".join(notes) + "\n", encoding="utf-8")
    written.append(notes_path)

    return written


def run(
    config,
    conn,
    fingerprints: list[str],
    cover_letter: bool = True,
    language: str | None = None,
) -> dict:
    """Tailor for each given job. Returns a summary dict."""
    from . import db
    from .resume import load as load_resume

    language = (language or config.language or "en").lower()
    # Raises if there is no master in this language — better than quietly
    # producing a half-translated document.
    resume = load_resume(config.resume_for(language))
    backend = llm.build(config)
    log.info(
        "tailoring %d job(s) in %s via %s", len(fingerprints), language, backend.name
    )
    tailored = failed = flagged = 0

    for fingerprint in fingerprints:
        row = db.get(conn, fingerprint)
        if row is None:
            log.error("no such job: %s", fingerprint)
            failed += 1
            continue

        try:
            result = tailor_one(backend, resume, row, cover_letter, language)
        except LLMError as exc:
            log.error("tailoring failed for %s @ %s: %s", row["title"], row["company"], exc)
            failed += 1
            continue

        # The company name and title are legitimate context for a cover
        # letter, and often appear nowhere in the description text.
        job_text = " ".join(
            filter(None, [row["company"], row["title"], row["description"]])
        )
        findings = check_tailoring(resume, result, job_text, language)
        directory = output_dir(config.output_dir, row)
        write_outputs(directory, resume, result, row, findings, language)
        db.mark_tailored(conn, fingerprint, str(directory))
        conn.commit()

        tailored += 1
        if findings:
            flagged += 1
            log.warning(
                "%s @ %s: %d unverified claim(s) — see %s/NOTES%s.md",
                row["title"], row["company"], len(findings), directory,
                suffix_for(language),
            )
            for finding in findings:
                log.warning("    %s", finding)
        else:
            log.info("%s @ %s -> %s", row["title"], row["company"], directory)

    return {"tailored": tailored, "failed": failed, "flagged": flagged}
