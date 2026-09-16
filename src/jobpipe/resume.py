"""Master resume: load, validate, render.

The master lives in JSON Resume format (https://jsonresume.org/schema/) —
a widely used, structured, machine-readable shape. Structure matters here:
tailoring selects and rewrites individual bullets, and that only works if
the bullets are addressable.
"""

from __future__ import annotations

import json
import re
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


# Abbreviated months, per language. A master stores "2023-07" because it is
# machine-readable; a resume that prints it that way looks machine-written.
SHORT_MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "de": ["Jan.", "Feb.", "Mär.", "Apr.", "Mai", "Jun.",
           "Jul.", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."],
}


def format_month(value: str, language: str = "en") -> str:
    """Turn "2023-07" into "Jul 2023". Anything else is passed through."""
    text = (value or "").strip()
    parts = text.split("-")
    if len(parts) < 2:
        return text
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return text
    if not 1 <= month <= 12:
        return text
    return f"{SHORT_MONTHS.get(language, SHORT_MONTHS['en'])[month - 1]} {year}"


def role_dates(role: Role, language: str = "en") -> str:
    """A role's date range, with "Present" in the output language."""
    start = format_month(role.start, language)
    end = format_month(role.end, language)
    if start and end:
        return f"{start} – {end}"
    if start:
        return f"{start} – {headings(language)['present']}"
    return end or ""


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
        url = profile.get("url")
        if not url:
            continue
        # Labeled as plain text, never an icon or a bare unlabeled URL —
        # an ATS parser (and a human skimming) should be able to tell
        # LinkedIn from GitHub from the text alone, no image involved.
        network = profile.get("network", "").strip()
        contact.append(f"{network}: {url}" if network else url)
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
    projects = resume.projects
    wanted = list(getattr(tailored, "selected_projects", None) or [])
    if wanted:
        # Selection only — the text stays verbatim, so choosing which projects
        # to show cannot introduce a claim. An out-of-range index is dropped
        # here and reported by `verify`; selecting none of them falls back to
        # all, since an empty Projects section is worse than a long one.
        picked = [resume.projects[i] for i in wanted if 0 <= i < len(resume.projects)]
        projects = picked or resume.projects

    if projects:
        lines += ["", "## " + h["projects"]]
        for project in projects:
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
                format_month(x, language)
                for x in [edu.get("startDate"), edu.get("endDate")] if x
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
  body {{ max-width: 7.5in; margin: 0 auto; padding: 0.5in;
         font: 10.5pt/1.38 Georgia, "Times New Roman", serif; color: #111;
         /* Never strand a single line across a page break. */
         orphans: 2; widows: 2; }}
  h1 {{ font-size: 19pt; margin: 0 0 1px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 10pt; text-transform: uppercase; letter-spacing: 0.09em;
        color: #333; border-bottom: 0.7pt solid #999; padding-bottom: 2px;
        margin: 14px 0 6px; }}
  h3 {{ font-size: 11pt; margin: 9px 0 0; }}
  p, li {{ margin: 2px 0; }}
  em {{ color: #555; font-size: 9.5pt; font-style: normal; }}
  ul {{ margin: 3px 0 0; padding-left: 16px; }}
  .contact {{ color: #444; font-size: 9.5pt; margin-bottom: 2px; }}

  /* A heading alone at the foot of a page, or an entry split from its own
     title, is what makes a generated resume look generated. */
  h2, h3 {{ break-after: avoid; page-break-after: avoid; }}
  h3 + em, h3 + p {{ break-before: avoid; page-break-before: avoid; }}
  li {{ break-inside: avoid; page-break-inside: avoid; }}

  @media print {{
    /* The page box already carries the margin. Padding here as well was
       costing 1.8in of every 11in page. */
    body {{ padding: 0; max-width: none; }}
    @page {{ margin: 0.5in 0.55in; }}
  }}
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


# --- LaTeX -----------------------------------------------------------------
#
# The resume ships as LaTeX and is compiled with pdflatex. The HTML render
# above stays: it is what `write_outputs` falls back to on a machine with no
# TeX installed, and it is what the dashboard links to for a quick read.
#
# Layout follows the sb2nov resume (MIT), with a two-column header so the
# photo sits beside the contact block rather than above it.

# Every character that would otherwise be a compile error, and the en dash
# role_dates() produces.
_LATEX_ESCAPES = str.maketrans({
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    # An en dash is valid UTF-8 source under inputenc, but LaTeX's own
    # spelling is what the rest of the template uses.
    "–": "--",
})


def latex_escape(text: str) -> str:
    """Make arbitrary resume text safe to drop into a .tex file.

    Everything the model writes passes through here. A stray `C#`, `100%` or
    `snake_case` in a bullet is otherwise a compile error, and a resume that
    does not compile is a resume that does not get sent.

    One pass over the input, never over its own output: several of these
    replacements contain braces, and a second pass would escape those too —
    turning `\\textbackslash{}` into `\\textbackslash\\{\\}`.
    """
    return str(text or "").translate(_LATEX_ESCAPES)


_LATEX_PREAMBLE = r"""%-------------------------
% Generated by jobpipe -- do not edit by hand.
% Edit resume.json (the master) or the renderer in src/jobpipe/resume.py.
% Based off of: https://github.com/sb2nov/resume  (MIT)
%
% PHOTO: expects "@PHOTO@" beside this file. If it is missing a
% placeholder box is drawn instead, so the document still compiles.
%-------------------------
\documentclass[letterpaper,11pt]{article}

\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[@BABEL@]{babel}
\usepackage{tabularx}
\usepackage{fontawesome5}
\usepackage{multicol}
\usepackage{graphicx}
\setlength{\multicolsep}{-3.0pt}
\setlength{\columnsep}{-1pt}
\input{glyphtounicode}

\pagestyle{fancy}
\fancyhf{}
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

% Margins
\addtolength{\oddsidemargin}{-0.6in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1.19in}
\addtolength{\topmargin}{-.7in}
\addtolength{\textheight}{1.4in}

\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}

\titleformat{\section}{
  \vspace{-4pt}\scshape\raggedright\large\bfseries
}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]

% Keep the generated PDF machine-readable, so an ATS parses it.
\pdfgentounicode=1

% f-ligatures are extracted by many CV parsers as "workow", "verication",
% "eciency". Disabling them guarantees clean text extraction.
\usepackage[expansion=false]{microtype}
\DisableLigatures[f]{encoding = *, family = *}

%-------------------------
% Custom commands
\newcommand{\resumeItem}[1]{
  \item\small{
    {#1 \vspace{-2pt}}
  }
}

\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{1.0\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & \textbf{\small #2} \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}

% p-column, not l: a project description is free text from the master and
% has to wrap rather than run off the right edge.
\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{1.0\textwidth}{@{}p{0.8\textwidth}@{\extracolsep{\fill}}r@{}}
      \small\raggedright#1 & \textbf{\small #2}\\
    \end{tabular*}\vspace{-5pt}
}

\renewcommand\labelitemi{$\vcenter{\hbox{\tiny$\bullet$}}$}
\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}

\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.0in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}

%%%%%%  RESUME STARTS HERE  %%%%%%
\begin{document}
"""

# babel's language name, not the config's two-letter code.
_BABEL = {"en": "english", "de": "ngerman"}

def _display_url(url: str) -> str:
    """The scheme-free form a resume shows: github.com/x, not https://github.com/x/.

    "https://" is redundant on a printed resume and, worse, an icon-font
    glyph in front of a generic word like "LinkedIn" is what this function
    replaces — a PDF text extractor reads the glyph stream, not the
    `\\href` target, so "LinkedIn" alone told an ATS nothing. The domain
    and path are the only part worth showing: self-describing as LinkedIn
    vs. GitHub, and the one piece of information a recruiter or parser can
    actually use.
    """
    return re.sub(r"^https?://", "", url.strip()).rstrip("/")


def _profile_link(profile: dict) -> str:
    """The profile URL itself, underlined and linked — no icon, no label."""
    url = (profile.get("url") or "").strip()
    if not url:
        return ""
    shown = latex_escape(_display_url(url))
    return f"\\href{{{latex_escape(url)}}}{{\\underline{{{shown}}}}}"


def _latex_header(resume: Resume, photo: str) -> list[str]:
    """Name and contact block on the left, photo on the right."""
    b = resume.basics
    lines = [
        r"%----------HEADING----------",
        r"\begin{minipage}[c]{0.74\textwidth}",
        r"    \raggedright",
        f"    {{\\Huge \\scshape {latex_escape(b.get('name', ''))}}} \\\\ \\vspace{{4pt}}",
    ]
    if b.get("label"):
        lines.append(
            f"    {{\\large {latex_escape(b['label'])}}} \\\\ \\vspace{{6pt}}"
        )

    contact = []
    if b.get("phone"):
        contact.append(
            f"\\raisebox{{-0.1\\height}}\\faPhone\\ {latex_escape(b['phone'])}"
        )
    if b.get("email"):
        email = latex_escape(b["email"])
        contact.append(
            f"\\href{{mailto:{email}}}"
            f"{{\\raisebox{{-0.2\\height}}\\faEnvelope\\ \\underline{{{email}}}}}"
        )
    if contact:
        lines.append("    \\small " + " ~ ".join(contact) + r" \\ \vspace{3pt}")

    links = [link for link in (
        _profile_link(p) for p in b.get("profiles") or []
    ) if link]
    if links:
        lines.append("    \\small " + " ~ ".join(links) + r" \\ \vspace{3pt}")

    location = b.get("location") or {}
    where = ", ".join(
        x for x in [
            location.get("address"), location.get("postalCode"),
            location.get("city"), location.get("region"),
        ] if x
    )
    if where:
        lines.append(
            f"    \\small \\raisebox{{-0.2\\height}}\\faMapMarker\\ "
            f"{latex_escape(where)} \\\\ \\vspace{{3pt}}"
        )

    # \IfFileExists rather than a hard \includegraphics: the .tex is handed
    # to the user as a file they can recompile, and a missing headshot should
    # leave a visible gap to fill, not a broken build.
    lines += [
        r"\end{minipage}%",
        r"\begin{minipage}[c]{0.26\textwidth}",
        r"    \raggedleft",
        f"    \\IfFileExists{{{photo}}}",
        f"        {{\\includegraphics[width=1.15in]{{{photo}}}}}",
        f"        {{\\framebox[1.15in][c]{{\\rule{{0pt}}{{1.45in}}"
        f"\\footnotesize {latex_escape(photo)}}}}}",
        r"\end{minipage}",
        "",
        r"\vspace{4pt}",
        "",
    ]
    return lines


def _latex_skills(resume: Resume, tailored, heading: str) -> list[str]:
    """Skills, kept in the master's groups but filtered to the selection.

    The tailoring returns one flat, relevance-ordered list. Printing it flat
    loses the grouping that makes a long skills section readable, so the
    selection is mapped back onto the master's own groups; a group with
    nothing selected is dropped entirely.
    """
    selected = list(tailored.selected_skills or [])
    if not selected:
        return []

    wanted = {s.strip().lower() for s in selected}
    rows = []
    covered = set()
    for group in resume.skills:
        keywords = [
            k for k in (group.get("keywords") or [])
            if str(k).strip().lower() in wanted
        ]
        if not keywords:
            continue
        covered.update(str(k).strip().lower() for k in keywords)
        rows.append((group.get("name", ""), keywords))

    # Anything the model selected that no master group claims. A group's own
    # name counts as one of its keywords for verification, so the model may
    # return "Automotive" as a skill; that is already the row label and
    # printing it again just adds a row of headings.
    group_names = {(g.get("name") or "").strip().lower() for g in resume.skills}
    leftover = [
        s for s in selected
        if s.strip().lower() not in covered and s.strip().lower() not in group_names
    ]
    if leftover and not rows:
        rows = [("", leftover)]
    elif leftover:
        rows.append(("", leftover))

    lines = [
        f"\\section{{{latex_escape(heading)}}}",
        r" \begin{itemize}[leftmargin=0.15in, label={}]",
        r"    \small{\item{",
    ]
    for name, keywords in rows:
        body = latex_escape(", ".join(str(k) for k in keywords))
        if name:
            lines.append(
                f"     \\resumeItem{{\\textbf{{{latex_escape(name)}}}"
                f"{{: {body}}}}} \\\\"
            )
        else:
            lines.append(f"     \\resumeItem{{{body}}} \\\\")
    lines += [r"    }}", r" \end{itemize}", r" \vspace{-16pt}", "", r"\hfill", ""]
    return lines


def render_latex(
    resume: Resume,
    tailored,
    job_title: str = "",
    language: str = "en",
    photo: str = "photo.jpg",
) -> str:
    """Render a tailored resume to a standalone LaTeX document.

    `photo` is the image filename as the .tex will reference it — a bare
    name, resolved beside the .tex file, so the folder stays portable.

    Like `render_markdown`, only the bullets the tailoring selected are
    emitted; education, projects and languages are copied from the master.
    """
    h = headings(language)
    out = [
        _LATEX_PREAMBLE
        .replace("@PHOTO@", photo)
        .replace("@BABEL@", _BABEL.get(language, _BABEL["en"]))
    ]
    lines = _latex_header(resume, photo)

    if tailored.summary:
        lines += [
            f"\\section{{{latex_escape(h['summary'])}}}",
            r" \begin{itemize}[leftmargin=0.15in, label={}]",
            r"    \small{\item{",
            f"    {latex_escape(tailored.summary)}",
            r"    }}",
            r" \end{itemize}",
            r" \vspace{-14pt}",
            "",
            r"\hfill",
            "",
        ]

    selected = {r.role_index: r for r in tailored.roles}
    if selected:
        lines += [
            f"\\section{{{latex_escape(h['experience'])}}}",
            r"  \resumeSubHeadingListStart",
            "",
        ]
        for role in resume.roles:
            chosen = selected.get(role.index)
            if not chosen or not chosen.bullets:
                continue
            lines += [
                r"    \resumeSubheading",
                f"      {{{latex_escape(role.name)}}}"
                f"{{{latex_escape(role_dates(role, language))}}}",
                f"      {{{latex_escape(role.position)}}}"
                f"{{{latex_escape(role.location)}}}",
                r"      \resumeItemListStart",
            ]
            lines += [
                f"        \\resumeItem{{{latex_escape(bullet.text)}}}"
                for bullet in chosen.bullets
            ]
            lines += [r"      \resumeItemListEnd \vspace{6pt}", ""]
        lines += [r"  \resumeSubHeadingListEnd", r"\vspace{-14pt}", "", r"\hfill", ""]

    projects = resume.projects
    wanted = list(getattr(tailored, "selected_projects", None) or [])
    if wanted:
        picked = [resume.projects[i] for i in wanted if 0 <= i < len(resume.projects)]
        projects = picked or resume.projects

    if projects:
        lines += [
            f"\\section{{{latex_escape(h['projects'])}}}",
            r"    \vspace{-5pt}",
            r"    \resumeSubHeadingListStart",
        ]
        for project in projects:
            name = latex_escape(project.get("name", ""))
            url = (project.get("url") or "").strip()
            title = (
                f"\\href{{{latex_escape(url)}}}{{\\textbf{{{name}}}}}"
                if url else f"\\textbf{{{name}}}"
            )
            if project.get("description"):
                title += f" $|$ \\emph{{{latex_escape(project['description'])}}}"
            lines += [
                r"      \resumeProjectHeading",
                f"          {{{title}}}{{}}",
                r"          \resumeItemListStart",
            ]
            lines += [
                f"            \\resumeItem{{{latex_escape(item)}}}"
                for item in project.get("highlights") or []
            ]
            lines += [r"          \resumeItemListEnd", r"          \vspace{-6pt}"]
        lines += [r"    \resumeSubHeadingListEnd", r"\vspace{-5pt}", "", r"\hfill", ""]

    lines += _latex_skills(resume, tailored, h["skills"])

    if resume.education:
        lines += [
            f"\\section{{{latex_escape(h['education'])}}}",
            r"  \resumeSubHeadingListStart",
        ]
        for edu in resume.education:
            degree = " ".join(
                x for x in [edu.get("studyType"), edu.get("area")] if x
            )
            dates = " -- ".join(
                format_month(x, language)
                for x in [edu.get("startDate"), edu.get("endDate")] if x
            )
            lines += [
                r"    \resumeSubheading",
                f"      {{{latex_escape(edu.get('institution', ''))}}}"
                f"{{{latex_escape(dates)}}}",
                f"      {{{latex_escape(degree)}}}"
                f"{{{latex_escape(edu.get('location', ''))}}}",
            ]
            # The thesis lives in the education summary. On a perception or
            # ADAS posting it is often the most relevant line on the page.
            if edu.get("summary"):
                lines += [
                    r"      \resumeItemListStart",
                    f"        \\resumeItem{{{latex_escape(edu['summary'])}}}",
                    r"      \resumeItemListEnd \vspace{2pt}",
                ]
        lines += [r"  \resumeSubHeadingListEnd", r"\vspace{-10pt}", "", r"\hfill", ""]

    if resume.languages:
        spoken = " $\\cdot$ ".join(
            latex_escape(
                f"{item.get('language', '')} ({item.get('fluency', '')})"
                .replace(" ()", "")
            )
            for item in resume.languages
            if item.get("language")
        )
        if spoken:
            lines += [
                f"\\section{{{latex_escape(h['languages'])}}}",
                r" \begin{itemize}[leftmargin=0.15in, label={}]",
                r"    \small{\item{",
                f"     {spoken} \\\\",
                r"    }}",
                r" \end{itemize}",
                r" \vspace{-16pt}",
                "",
            ]

    out.append("\n".join(lines))
    out.append("\n\\end{document}\n")
    return "\n".join(out)
