"""The extension's own files.

These are static checks on things that are easy to loosen by accident and
hard to notice afterwards: how much of your browsing the extension can see,
and whether its client-side refusals are still there. The behaviour they
guard is tested for real in test_extapi.py (the plan) and
test_autofill_browser.py (the extraction, in a live Chromium).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobpipe import autofill

EXT = Path(autofill.__file__).parent / "extension"


@pytest.fixture(scope="module")
def manifest():
    return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def read(name: str) -> str:
    return (EXT / name).read_text(encoding="utf-8")


# -- what it can reach ---------------------------------------------------


def test_manifest_is_v3(manifest):
    assert manifest["manifest_version"] == 3


def test_it_cannot_read_every_site_you_visit(manifest):
    """activeTab only: the panel appears on tabs you invoke it on, nowhere else."""
    assert "activeTab" in manifest["permissions"]
    assert "<all_urls>" not in manifest["permissions"]
    assert "tabs" not in manifest["permissions"]


def test_host_permissions_are_only_the_local_server(manifest):
    for host in manifest["host_permissions"]:
        assert "127.0.0.1:5000" in host or "localhost:5000" in host


def test_it_declares_no_always_on_content_scripts(manifest):
    """A content_scripts block would run on pages without being asked."""
    assert "content_scripts" not in manifest


def test_every_referenced_file_exists(manifest):
    named = {manifest["background"]["service_worker"], manifest["options_page"]}
    named |= {"extract.js", "content.js", "panel.css"}  # injected by background.js
    for name in named:
        assert (EXT / name).is_file(), f"{name} is referenced but missing"


# -- the shared extractor ------------------------------------------------


def test_python_reads_the_extension_copy():
    """One file, two callers. A second copy would drift."""
    assert autofill.EXTRACT_PATH == EXT / "extract.js"
    assert "function jobpipeExtractFields" in autofill.extract_source()


def test_the_extractor_excludes_buttons():
    """This exclusion is what makes "cannot submit" structural."""
    source = read("extract.js")
    for kind in ["'hidden'", "'submit'", "'button'", "'reset'", "'image'"]:
        assert kind in source


def test_the_extractor_only_reads():
    source = read("extract.js")
    assert ".click(" not in source
    assert ".value =" not in source


# -- the client-side refusals --------------------------------------------


def test_the_content_script_refuses_credentials_itself():
    """The server never plans one; this is the second line of that defence."""
    source = read("content.js")
    assert "NEVER_WRITE" in source
    assert '"password"' in source
    assert '"submit"' in source


def code_lines(name: str) -> str:
    """The file with its comment lines dropped — assertions about what the
    code does should not be satisfiable by a comment that mentions it."""
    return "\n".join(
        line for line in read(name).splitlines()
        if not line.strip().startswith("//")
    )


def test_the_content_script_clicks_nothing_but_a_tick_box():
    """Exactly one .click() in the code, guarded by an explicit type check."""
    code = code_lines("content.js")
    assert code.count(".click()") == 1
    assert 'type !== "checkbox" && type !== "radio"' in code


def test_the_content_script_never_touches_a_form_submit():
    code = code_lines("content.js")
    for forbidden in [".submit()", "requestSubmit", 'querySelector("button']:
        assert forbidden not in code
