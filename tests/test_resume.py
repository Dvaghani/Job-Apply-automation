import json

import pytest

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
