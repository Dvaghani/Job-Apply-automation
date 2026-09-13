"""Master resume: load, validate, render.

The master lives in JSON Resume format (https://jsonresume.org/schema/) —
a widely used, structured, machine-readable shape. Structure matters here:
tailoring selects and rewrites individual bullets, and that only works if
the bullets are addressable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ResumeError(RuntimeError):
    pass


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
    )


def render_markdown(resume: Resume, tailored) -> str:
    """Render a tailored resume to Markdown.

    `tailored` is a `tailor.Tailoring`; only the bullets it selected are
    emitted, in the order it chose.
    """
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
        lines += ["", "## Summary", "", tailored.summary]

    selected = {r.role_index: r for r in tailored.roles}
    if selected:
        lines += ["", "## Experience"]
        for role in resume.roles:
            chosen = selected.get(role.index)
            if not chosen or not chosen.bullets:
                continue
            lines += ["", f"### {role.position} — {role.name}"]
            meta = " · ".join(x for x in [role.dates, role.location] if x)
            if meta:
                lines += [f"*{meta}*"]
            lines += [""]
            lines += [f"- {bullet.text}" for bullet in chosen.bullets]

    if tailored.selected_skills:
        lines += ["", "## Skills", "", ", ".join(tailored.selected_skills)]

    if resume.education:
        lines += ["", "## Education"]
        for edu in resume.education:
            degree = " ".join(
                x for x in [edu.get("studyType"), edu.get("area")] if x
            )
            dates = edu.get("endDate", "")
            entry = f"**{edu.get('institution', '')}**"
            if degree:
                entry += f" — {degree}"
            if dates:
                entry += f" ({dates})"
            lines += ["", entry]

    return "\n".join(lines).strip() + "\n"


HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
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


def render_html(resume: Resume, tailored, job_title: str = "") -> str:
    """Very small Markdown subset -> HTML. Print this to get a PDF."""
    md = render_markdown(resume, tailored)
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
        name=_escape(resume.name), role=_escape(job_title), body="\n".join(out)
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
