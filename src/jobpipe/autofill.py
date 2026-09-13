"""Assisted autofill: fill an application form, never submit it.

The safety property is structural, not a promise. The element query below
selects only `input`, `select` and `textarea`, and buttons are excluded
from it — so there is no code path that can click Submit. Filling a field
never presses Enter either, so a single-field form can't submit by
accident. The browser is left open with the form filled, and a human
reads it over and clicks Submit themselves.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .applicant import Applicant
from .fieldmap import classify, is_eeo

log = logging.getLogger(__name__)

# Only ever these three tags. `input` is filtered further in JS to drop
# submit/button/reset/image/hidden types.
FIELD_SELECTOR = "input, select, textarea"

# Runs in the page. Tags each field with a stable id and reports the
# metadata needed to classify it.
EXTRACT_JS = """
() => {
  const SKIP = new Set(['hidden', 'submit', 'button', 'reset', 'image']);
  const text = (el) => (el ? (el.innerText || el.textContent || '').trim() : '');
  const out = [];
  let index = 0;

  document.querySelectorAll('input, select, textarea').forEach((el) => {
    if (el.tagName === 'INPUT' && SKIP.has((el.type || '').toLowerCase())) return;
    if (el.disabled) return;
    if (el.offsetParent === null && el.type !== 'file') return;  // not visible

    let label = '';
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      label = text(l);
    }
    if (!label) label = text(el.closest('label'));
    if (!label && el.getAttribute('aria-labelledby')) {
      label = el.getAttribute('aria-labelledby').split(/\\s+/)
        .map((id) => text(document.getElementById(id))).join(' ').trim();
    }
    if (!label) {
      const fs = el.closest('fieldset');
      if (fs) label = text(fs.querySelector('legend'));
    }
    if (!label) {
      // Common ATS shape: a div wrapper whose first text node is the label.
      const parent = el.parentElement;
      if (parent) {
        const own = Array.from(parent.childNodes)
          .filter((n) => n.nodeType === 3)
          .map((n) => n.textContent.trim())
          .filter(Boolean);
        if (own.length) label = own[0];
      }
    }

    const id = 'jp-' + (index++);
    el.setAttribute('data-jobpipe-id', id);

    out.push({
      id,
      tag: el.tagName.toLowerCase(),
      type: (el.type || '').toLowerCase(),
      name: el.name || '',
      label: label.replace(/\\s+/g, ' ').slice(0, 200),
      placeholder: el.placeholder || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      required: !!el.required,
      value: el.value || '',
      options: el.tagName === 'SELECT'
        ? Array.from(el.options).map((o) => ({ text: o.text.trim(), value: o.value }))
        : [],
    });
  });
  return out;
}
"""


def launch_chromium(playwright, headless: bool):
    """Launch Chromium, honouring JOBPIPE_CHROMIUM.

    Set that variable to a Chrome/Chromium binary to use an existing
    install instead of Playwright's bundled one — handy when the bundled
    build doesn't match, or when you'd rather drive your own browser.
    """
    executable = os.environ.get("JOBPIPE_CHROMIUM")
    if executable:
        return playwright.chromium.launch(headless=headless, executable_path=executable)
    return playwright.chromium.launch(headless=headless)


@dataclass
class FieldAction:
    """What happened to one field."""

    label: str
    key: str | None
    action: str   # "filled" | "skipped" | "unmatched" | "failed"
    detail: str = ""

    def __str__(self) -> str:
        mark = {"filled": "+", "skipped": "-", "unmatched": "?", "failed": "!"}[self.action]
        head = f" {mark} {self.label[:48]:<48}"
        return f"{head} {self.detail}" if self.detail else head


def apply_form_url(url: str) -> str:
    """The URL of the application form for a posting, where it differs.

    Derived from the posting URL, never by clicking through the page —
    keeping navigation explicit is part of not touching buttons.
    """
    clean = (url or "").split("?")[0].rstrip("/")
    if "jobs.lever.co" in clean and not clean.endswith("/apply"):
        return clean + "/apply"
    if "jobs.ashbyhq.com" in clean and not clean.endswith("/application"):
        return clean + "/application"
    return url


def choose_option(options: list[dict], value: str) -> str | None:
    """Pick the dropdown option matching `value`, or None."""
    wanted = (value or "").strip().lower()
    if not wanted:
        return None
    texts = [(o.get("text", ""), o.get("value", "")) for o in options]
    for text, val in texts:                       # exact
        if text.strip().lower() == wanted:
            return val
    for text, val in texts:                       # option contains answer
        if wanted and wanted in text.strip().lower():
            return val
    for text, val in texts:                       # answer contains option
        stripped = text.strip().lower()
        if stripped and stripped in wanted:
            return val
    return None


def resolve_value(field: dict, key: str | None, applicant: Applicant) -> str | None:
    """The value for a field: canonical first, then the answer bank.

    The self-identification opt-in lives solely in `Applicant.value_for`;
    duplicating that policy here previously made opting in a no-op.
    """
    if key:
        value = applicant.value_for(key)
        if value:
            return value
    question = field.get("label") or field.get("ariaLabel") or field.get("name") or ""
    return applicant.answer_for(question)


def fill_page(page, applicant: Applicant, attachments: dict[str, Path]) -> list[FieldAction]:
    """Classify and fill every field on `page`. Returns what was done."""
    fields = page.evaluate(EXTRACT_JS)
    actions: list[FieldAction] = []

    for field in fields:
        label = (
            field["label"] or field["ariaLabel"] or field["placeholder"]
            or field["name"] or "(unlabelled)"
        )
        key = classify(
            label=field["label"],
            name=field["name"],
            placeholder=field["placeholder"],
            aria_label=field["ariaLabel"],
            field_type=field["type"],
        )
        locator = page.locator(f'[data-jobpipe-id="{field["id"]}"]')

        if is_eeo(key) and not applicant.fill_eeo:
            actions.append(
                FieldAction(label, key, "skipped", "self-identification — yours to answer")
            )
            continue

        try:
            action = _fill_field(locator, field, key, applicant, attachments)
        except Exception as exc:  # a form quirk must not abort the rest
            actions.append(FieldAction(label, key, "failed", str(exc)[:80]))
            continue
        actions.append(FieldAction(label, key, action[0], action[1]))

    return actions


def _fill_field(locator, field, key, applicant, attachments) -> tuple[str, str]:
    field_type, tag = field["type"], field["tag"]

    if field_type == "file":
        path = attachments.get(key or "resume")
        if not path:
            return "unmatched", "no file to attach"
        locator.set_input_files(str(path))
        return "filled", path.name

    value = resolve_value(field, key, applicant)
    if not value:
        return "unmatched", "no value in your applicant file"

    if tag == "select":
        option = choose_option(field["options"], value)
        if option is None:
            available = ", ".join(o["text"] for o in field["options"][:4])
            return "unmatched", f"no option matches {value!r} (has: {available})"
        locator.select_option(option)
        return "filled", value

    if field_type == "checkbox":
        if value.strip().lower() in {"yes", "true", "1", "on"}:
            locator.check()
            return "filled", "checked"
        return "skipped", f"value {value!r} is not a yes"

    if field_type == "radio":
        # One radio per option; fill the one whose own label matches.
        own = (field["label"] or "").strip().lower()
        if own and (own in value.lower() or value.lower() in own):
            locator.check()
            return "filled", field["label"][:40]
        return "skipped", "not the selected option"

    # Text, email, tel, url, number, date, textarea. `fill` sets the value
    # directly and does not press Enter, so nothing can submit here.
    locator.fill(value)
    return "filled", value[:60]


def html_to_pdf(html_path: Path, pdf_path: Path) -> Path:
    """Render the tailored resume to PDF, for forms that want an upload."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=True)
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.pdf(path=str(pdf_path), format="Letter", print_background=True)
        browser.close()
    return pdf_path


def gather_attachments(output_dir: Path) -> dict[str, Path]:
    """Find the tailored documents to upload, making a PDF if needed."""
    attachments: dict[str, Path] = {}
    if not output_dir or not output_dir.exists():
        return attachments

    pdf = output_dir / "resume.pdf"
    html = output_dir / "resume.html"
    if not pdf.exists() and html.exists():
        log.info("rendering %s", pdf)
        html_to_pdf(html, pdf)
    if pdf.exists():
        attachments["resume"] = pdf

    cover = output_dir / "cover-letter.md"
    if cover.exists():
        attachments["cover_letter"] = cover
    return attachments


def run(row, applicant: Applicant, headless: bool = False, wait: bool = True) -> list[FieldAction]:
    """Open the posting's form, fill it, and hand control to the human."""
    from playwright.sync_api import sync_playwright

    url = apply_form_url(row["url"])
    output_dir = Path(row["output_dir"]) if row["output_dir"] else None
    attachments = gather_attachments(output_dir) if output_dir else {}

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
        page = browser.new_context().new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)  # let client-rendered forms mount

        actions = fill_page(page, applicant, attachments)

        if wait and not headless:
            print("\nForm filled. Review every field, then submit it yourself.")
            print("This tool does not and cannot click Submit.")
            input("Press Enter here when you're done to close the browser... ")
        browser.close()

    return actions
