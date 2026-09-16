import json

import pytest

from jobpipe import resume
from jobpipe.resume import ResumeError, load, render_html, render_markdown

MASTER = {
    "basics": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "location": {"city": "Toronto", "region": "ON"},
        "profiles": [{"network": "GitHub", "url": "https://github.com/ada"}],
    },
    "work": [
        {
            "name": "Acme",
            "position": "Senior Engineer",
            "startDate": "2022-03",
            "endDate": "",
            "location": "Toronto",
            "highlights": ["Built a ledger.", "Mentored 4 engineers.", "Ran on-call."],
        },
        {
            "name": "Northwind",
            "position": "Engineer",
            "startDate": "2019-06",
            "endDate": "2022-02",
            "highlights": ["Shipped an API."],
        },
    ],
    "education": [
        {"institution": "U of T", "area": "CS", "studyType": "BSc", "endDate": "2019-05"}
    ],
    "skills": [{"name": "Languages", "keywords": ["Python", "Go"]}],
}


@pytest.fixture
def master(tmp_path):
    path = tmp_path / "resume.json"
    path.write_text(json.dumps(MASTER))
    return load(path)


class B:
    def __init__(self, i, text):
        self.source_index, self.text = i, text


class R:
    def __init__(self, i, bullets):
        self.role_index, self.bullets = i, bullets


class T:
    summary = "Backend engineer."
    cover_letter = ""
    def __init__(self, roles, skills=None):
        self.roles = roles
        self.selected_skills = skills or ["Python"]


def test_load_indexes_roles_and_highlights(master):
    assert [r.index for r in master.roles] == [0, 1]
    assert master.roles[0].highlights[1] == "Mentored 4 engineers."


def test_current_role_dates_say_present(master):
    assert master.roles[0].dates == "2022-03 – Present"
    assert master.roles[1].dates == "2019-06 – 2022-02"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ResumeError, match="not found"):
        load(tmp_path / "nope.json")


def test_resume_without_work_is_rejected(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"basics": {"name": "X"}, "work": []}))
    with pytest.raises(ResumeError, match="no `work`"):
        load(path)


def test_resume_without_any_highlights_is_rejected(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"work": [{"name": "A", "highlights": []}]}))
    with pytest.raises(ResumeError, match="highlights"):
        load(path)


def test_invalid_json_is_a_clear_error(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{not json")
    with pytest.raises(ResumeError, match="not valid JSON"):
        load(path)


def test_render_emits_only_selected_bullets(master):
    tailoring = T([R(0, [B(0, "Built a ledger."), B(2, "Ran on-call.")])])
    md = render_markdown(master, tailoring)
    assert "Built a ledger." in md
    assert "Ran on-call." in md
    assert "Mentored" not in md          # not selected
    assert "Northwind" not in md         # role contributed nothing


def test_render_preserves_chosen_bullet_order(master):
    tailoring = T([R(0, [B(2, "Ran on-call."), B(0, "Built a ledger.")])])
    md = render_markdown(master, tailoring)
    assert md.index("Ran on-call.") < md.index("Built a ledger.")


def test_render_includes_contact_and_education(master):
    md = render_markdown(master, T([R(0, [B(0, "Built a ledger.")])]))
    assert "ada@example.com" in md and "Toronto" in md
    assert "U of T" in md and "BSc" in md


def test_profile_links_are_labeled_plain_text_not_bare_urls(master):
    # ATS-friendly means no icon/image and no unlabeled URL — the network
    # name must appear as visible text next to its link.
    md = render_markdown(master, T([R(0, [B(0, "Built a ledger.")])]))
    assert "GitHub: https://github.com/ada" in md
    assert "<svg" not in md and "<img" not in md


def test_profile_without_a_network_name_falls_back_to_a_bare_link(tmp_path):
    data = dict(MASTER)
    data["basics"] = dict(MASTER["basics"])
    data["basics"]["profiles"] = [{"url": "https://example.dev/ada"}]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data))
    resume = load(path)
    md = render_markdown(resume, T([R(0, [B(0, "Built a ledger.")])]))
    assert "https://example.dev/ada" in md
    assert ": https://example.dev/ada" not in md


def test_html_escapes_user_content(tmp_path):
    data = dict(MASTER)
    data["work"] = [{"name": "A<script>", "position": "P", "highlights": ["x"]}]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data))
    resume = load(path)
    html = render_html(resume, T([R(0, [B(0, "5 < 10 & rising")])]), "Role")
    assert "&lt;script&gt;" in html
    assert "5 &lt; 10 &amp; rising" in html


def test_html_renders_bullets_as_list(master):
    html = render_html(master, T([R(0, [B(0, "Built a ledger.")])]), "Role")
    assert "<ul>" in html and "<li>Built a ledger.</li>" in html
    assert html.count("<ul>") == html.count("</ul>")


# --- sections copied straight from the master ----------------------------
#
# None of these go through the model, so none of them can acquire a claim
# that isn't already in resume.json. They were being dropped entirely, which
# cost the thesis — often the most relevant thing on a perception posting.

MASTER_WITH_EXTRAS = {
    "basics": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "location": {"city": "Chemnitz", "region": "Saxony"},
    },
    "work": [{
        "name": "Acme",
        "position": "Engineer",
        "highlights": ["Built a ledger in Python."],
    }],
    "education": [{
        "institution": "TU Chemnitz",
        "studyType": "M.Sc.",
        "area": "Automotive Software Engineering",
        "startDate": "2022-10",
        "endDate": "2026-09",
        "location": "Chemnitz, Germany",
        "summary": "Thesis: Stereo Depth Estimation using Classical and Deep Learning.",
    }],
    "projects": [{
        "name": "HiL Camera Calibration Bench",
        "description": "IAV GmbH, 2024-2025.",
        "highlights": ["Designed the glass-projector structure in Creo Parametric."],
    }],
    "languages": [
        {"language": "English", "fluency": "Fluent"},
        {"language": "German", "fluency": "A2"},
    ],
}


def _rendered(tmp_path):
    import json as _json

    from jobpipe.tailor import TailoredBullet, TailoredRole, Tailoring

    path = tmp_path / "master.json"
    path.write_text(_json.dumps(MASTER_WITH_EXTRAS), encoding="utf-8")
    master = resume.load(path)
    tailoring = Tailoring(
        summary="Engineer.",
        roles=[TailoredRole(role_index=0, bullets=[
            TailoredBullet(source_index=0, text="Built a ledger in Python."),
        ])],
        selected_skills=[],
        cover_letter="",
        keywords_matched=[],
        gaps=[],
    )
    return resume.render_markdown(master, tailoring)


def test_the_thesis_reaches_the_rendered_resume(tmp_path):
    assert "Stereo Depth Estimation" in _rendered(tmp_path)


def test_projects_reach_the_rendered_resume(tmp_path):
    out = _rendered(tmp_path)
    assert "## Projects" in out
    assert "HiL Camera Calibration Bench" in out
    assert "Creo Parametric" in out


def test_languages_reach_the_rendered_resume(tmp_path):
    out = _rendered(tmp_path)
    assert "## Languages" in out
    assert "English (Fluent)" in out
    assert "German (A2)" in out


def test_education_shows_the_full_date_range(tmp_path):
    assert "(Oct 2022 – Sep 2026)" in _rendered(tmp_path)


def test_a_master_without_the_extra_sections_renders_without_empty_headings(tmp_path):
    import json as _json

    from jobpipe.tailor import TailoredBullet, TailoredRole, Tailoring

    bare = {"basics": {"name": "Ada"}, "work": MASTER_WITH_EXTRAS["work"]}
    path = tmp_path / "bare.json"
    path.write_text(_json.dumps(bare), encoding="utf-8")
    out = resume.render_markdown(resume.load(path), Tailoring(
        summary="", roles=[TailoredRole(role_index=0, bullets=[
            TailoredBullet(source_index=0, text="Built a ledger in Python.")])],
        selected_skills=[], cover_letter="", keywords_matched=[], gaps=[],
    ))
    assert "## Projects" not in out
    assert "## Languages" not in out


# --- the cover letter as a document ---------------------------------------
#
# Markdown is the editable source; an ATS upload field rejects it. The letter
# needs a letterhead around the model's prose so the PDF is a real letter.

def _letter(tmp_path, text, language="en", today=None):
    import datetime

    path = tmp_path / "m.json"
    path.write_text(json.dumps(MASTER_WITH_EXTRAS), encoding="utf-8")
    master = resume.load(path)
    return resume.render_cover_html(
        master, text, "Globex GmbH", "Firmware Engineer", language,
        today or datetime.date(2026, 9, 13),
    )


def test_the_letter_carries_sender_recipient_and_subject(tmp_path):
    html = _letter(tmp_path, "Dear team,\n\nHello.")
    assert "Ada Lovelace" in html
    assert "Globex GmbH" in html
    assert "Application for Firmware Engineer" in html


def test_paragraphs_become_paragraphs(tmp_path):
    html = _letter(tmp_path, "One.\n\nTwo.\n\nThree.")
    assert html.count("<p>") == 3


def test_a_single_newline_stays_inside_its_paragraph(tmp_path):
    """A sign-off is two lines of one block, not two paragraphs."""
    html = _letter(tmp_path, "Regards,\nAda")
    assert "<p>Regards,<br>Ada</p>" in html


def test_nothing_is_added_around_the_body(tmp_path):
    """The salutation and sign-off come from the model — adding our own
    would duplicate whatever it already wrote."""
    html = _letter(tmp_path, "Sehr geehrte Damen und Herren,\n\nText.", "de")
    assert html.count("Sehr geehrte") == 1
    assert "Dear" not in html
    assert "Mit freundlichen" not in html


def test_the_german_letter_uses_german_conventions(tmp_path):
    html = _letter(tmp_path, "Text.", "de")
    assert "Bewerbung als Firmware Engineer" in html
    assert "13. September 2026" in html
    assert 'lang="de"' in html


def test_the_english_letter_uses_english_conventions(tmp_path):
    html = _letter(tmp_path, "Text.", "en")
    assert "13 September 2026" in html
    assert "Application for" in html


def test_a_german_letter_leads_with_the_place_of_writing(tmp_path):
    assert "Chemnitz, 13. September 2026" in _letter(tmp_path, "Text.", "de")


def test_html_in_the_letter_text_is_escaped(tmp_path):
    html = _letter(tmp_path, "I use <script>alert(1)</script> daily.")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- how the resume reads -------------------------------------------------

def test_dates_are_human_not_machine():
    """A master stores "2023-07"; printing that makes it look machine-written."""
    assert resume.format_month("2023-07") == "Jul 2023"
    assert resume.format_month("2023-07", "de") == "Jul. 2023"


def test_a_year_only_date_is_left_alone():
    assert resume.format_month("2020") == "2020"


def test_an_unparseable_date_passes_through_untouched():
    for odd in ["", "Present", "2023-13", "soon-ish"]:
        assert resume.format_month(odd) == odd


def test_role_dates_read_as_a_range(tmp_path):
    role = resume.Role(index=0, name="IAV", position="Werkstudent",
                       start="2023-07", end="2024-09")
    assert resume.role_dates(role) == "Jul 2023 – Sep 2024"
    assert resume.role_dates(role, "de") == "Jul. 2023 – Sep. 2024"


def test_an_open_ended_role_says_present_in_the_output_language(tmp_path):
    role = resume.Role(index=0, name="IAV", position="Werkstudent", start="2023-07")
    assert resume.role_dates(role) == "Jul 2023 – Present"
    assert resume.role_dates(role, "de") == "Jul. 2023 – heute"


def test_the_print_layout_does_not_double_its_margins():
    """@page margin plus body padding was costing 1.8in of every page."""
    css = resume.HTML_SHELL
    assert "body { padding: 0; max-width: none; }" in css.replace("{{", "{").replace("}}", "}")


def test_headings_are_kept_with_what_follows_them():
    css = resume.HTML_SHELL
    assert "page-break-after: avoid" in css
    assert "page-break-inside: avoid" in css


# --- LaTeX ----------------------------------------------------------------

def tex(master_resume, tailoring=None, **kw):
    return resume.render_latex(
        master_resume, tailoring or T([R(0, [B(0, "Built a ledger.")])]), **kw
    )


def test_latex_escapes_the_characters_that_would_not_compile(master):
    out = tex(master, T([R(0, [B(0, "Shipped C# at 100% under budget_x & Co.")])]))
    assert r"C\# at 100\% under budget\_x \& Co." in out
    assert "100%" not in out


def test_latex_escaping_does_not_double_escape_its_own_backslashes():
    assert resume.latex_escape("a_b") == r"a\_b"
    assert resume.latex_escape("50%") == r"50\%"
    # The backslash rule runs first, so the backslashes it introduces are
    # not re-escaped by the rules after it.
    assert resume.latex_escape(r"C:\path") == r"C:\textbackslash{}path"


def test_the_document_compiles_as_a_whole(master):
    out = tex(master)
    assert out.startswith("%")
    assert r"\begin{document}" in out
    assert out.rstrip().endswith(r"\end{document}")


def test_the_photo_is_referenced_by_the_name_it_will_have(master):
    out = tex(master, photo="photo.png")
    assert r"\IfFileExists{photo.png}" in out
    assert r"\includegraphics[width=1.15in]{photo.png}" in out


def test_a_missing_photo_still_compiles(master):
    """The placeholder branch is what makes a photo-less run survive."""
    out = tex(master)
    assert r"\framebox" in out


def test_only_the_selected_bullets_are_rendered(master):
    out = tex(master, T([R(0, [B(1, "Mentored 4 engineers.")])]))
    assert "Mentored 4 engineers." in out
    assert "Built a ledger." not in out


def test_skills_keep_the_masters_grouping(master):
    out = tex(master, T([R(0, [B(0, "x")])], skills=["Python"]))
    assert r"\textbf{Languages}{: Python}" in out
    # "Go" was not selected for this posting and must not appear.
    assert "Go" not in out


def test_a_group_name_selected_as_a_skill_does_not_become_its_own_row(master):
    """`all_skill_keywords` counts group names, so the model may return one."""
    out = tex(master, T([R(0, [B(0, "x")])], skills=["Python", "Languages"]))
    assert out.count("Languages") == 1


def test_the_german_resume_uses_german_headings_and_babel(master):
    out = tex(master, language="de")
    assert r"\section{Berufserfahrung}" in out
    assert r"\usepackage[ngerman]{babel}" in out


def test_the_thesis_reaches_the_latex_resume(tmp_path):
    data = json.loads(json.dumps(MASTER))
    data["education"][0]["summary"] = "Thesis: stereo depth estimation."
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data))
    assert "Thesis: stereo depth estimation." in tex(load(path))


def test_a_long_project_description_is_not_dropped(tmp_path):
    data = json.loads(json.dumps(MASTER))
    data["projects"] = [{
        "name": "Bench",
        "description": "A description far too long to sit on one heading line.",
        "highlights": ["Did the thing."],
    }]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data))
    out = tex(load(path))
    assert "A description far too long to sit on one heading line." in out
    # A p-column wraps; the l-column of the original template would have run
    # the description off the right edge of the page.
    assert r"p{0.8\textwidth}" in out


def test_profile_links_show_the_url_not_a_generic_label(master):
    # An icon glyph followed by the bare word "GitHub" put nothing
    # ATS-readable on the page: a PDF text extractor reads the visible
    # glyph stream, never a \href target, so "GitHub" alone told a parser
    # nothing about which profile it was. No icon, and the visible text
    # is the URL itself.
    out = tex(master)
    assert r"\faGithub" not in out
    assert r"\href{https://github.com/ada}" in out
    assert r"\underline{github.com/ada}" in out


def test_profile_link_display_text_drops_scheme_and_trailing_slash(master):
    from jobpipe.resume import _display_url

    assert _display_url("https://github.com/ada") == "github.com/ada"
    assert _display_url("https://www.linkedin.com/in/ada/") == "www.linkedin.com/in/ada"
    assert _display_url("http://example.dev") == "example.dev"


# --- projects: length, not just selection --------------------------------
#
# A master is a superset by design. Projects used to print in full — every
# highlight, and the whole `description` inside a one-line heading — so a
# thesis entry with eight bullets of measurements spent a page on its own.

from jobpipe.resume import (  # noqa: E402
    PROJECT_BULLET_LIMIT, project_blurb, resolve_projects,
)

THESIS_DESC = (
    "M.Sc. thesis, Technische Universität Chemnitz — Professorship of Computer "
    "Engineering. Registered title: Depth Map Generation based on Application "
    "Specific Stereo-Vision. In thesis phase; submission expected 2026."
)


def test_blurb_keeps_a_short_description_whole():
    assert project_blurb("IAV GmbH, 2024–2025. Team project.") == "IAV GmbH, 2024–2025."
    assert project_blurb("B.E. Final Year Project, 2020.") == "B.E. Final Year Project, 2020."


def test_blurb_is_not_fooled_by_an_abbreviation():
    # "M.Sc." ends in a period; taking the first sentence alone left a stub.
    assert project_blurb(THESIS_DESC).startswith("M.Sc. thesis, Technische")


def test_blurb_fits_on_one_heading_line():
    out = project_blurb(THESIS_DESC)
    assert len(out) <= 90
    # The registered-title/submission-date housekeeping is gone.
    assert "Registered title" not in out and "2026" not in out


def test_blurb_of_nothing_is_nothing():
    assert project_blurb("") == ""
    assert project_blurb(None) == ""


class _PB:
    def __init__(self, i, text):
        self.source_index, self.text = i, text


class _TP:
    def __init__(self, index, bullets):
        self.project_index, self.bullets = index, bullets


class _TWithProjects:
    summary = "x"
    cover_letter = ""
    selected_skills = []
    selected_projects = []
    roles = []

    def __init__(self, projects):
        self.projects = projects


def _master_with_projects(tmp_path, highlights):
    data = json.loads(json.dumps(MASTER))
    data["projects"] = [{
        "name": "Stereo Depth", "description": THESIS_DESC, "highlights": highlights,
    }]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data))
    return load(path)


def test_untailored_projects_are_capped(tmp_path):
    master = _master_with_projects(tmp_path, [f"Highlight {i}." for i in range(8)])
    tailoring = T([R(0, [B(0, "Built a ledger.")])])
    (_, bullets), = resolve_projects(master, tailoring)
    assert len(bullets) == PROJECT_BULLET_LIMIT


def test_tailored_project_bullets_replace_the_masters(tmp_path):
    master = _master_with_projects(tmp_path, ["A very long original bullet.", "Second."])
    tailoring = _TWithProjects([_TP(0, [_PB(0, "Short rewrite.")])])
    (project, bullets) = resolve_projects(master, tailoring)[0]
    assert bullets == ["Short rewrite."]
    assert project["name"] == "Stereo Depth"


def test_tailored_project_bullets_are_capped_too(tmp_path):
    master = _master_with_projects(tmp_path, [f"H{i}." for i in range(6)])
    tailoring = _TWithProjects(
        [_TP(0, [_PB(i, f"Rewrite {i}.") for i in range(6)])]
    )
    (_, bullets), = resolve_projects(master, tailoring)
    assert len(bullets) == PROJECT_BULLET_LIMIT


def test_out_of_range_project_falls_back_rather_than_emptying(tmp_path):
    master = _master_with_projects(tmp_path, ["Only one."])
    tailoring = _TWithProjects([_TP(99, [_PB(0, "Rewrite.")])])
    # Nothing selectable, so the master's own text still renders.
    assert resolve_projects(master, tailoring) == [(master.projects[0], ["Only one."])]


def test_rendered_project_heading_is_one_line(tmp_path):
    master = _master_with_projects(tmp_path, ["Did the thing."])
    out = tex(master)
    assert "Registered title" not in out
    assert r"\emph{M.Sc. thesis, Technische Universität Chemnitz" in out
