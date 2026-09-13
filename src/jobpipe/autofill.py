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
import re
from dataclasses import dataclass
from pathlib import Path

from .applicant import Applicant
from .fieldmap import classify, is_credential, is_eeo

log = logging.getLogger(__name__)

# Only ever these three tags. `input` is filtered further in JS to drop
# submit/button/reset/image/hidden types.
FIELD_SELECTOR = "input, select, textarea"

# The extraction script, shared verbatim with the Chrome extension. Keeping
# one copy is the point: the extension classifies fields by sending them to
# this same code, so both must see the page the same way.
EXTRACT_PATH = Path(__file__).parent / "extension" / "extract.js"


def extract_source() -> str:
    return EXTRACT_PATH.read_text(encoding="utf-8")


def extract_fields(page) -> list[dict]:
    """Every fillable field on the page, as the extension also sees them."""
    return page.evaluate(
        "(() => {" + extract_source() + "; return jobpipeExtractFields(); })()"
    )


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


# Adzuna stores a link to its own listing, not to the employer.
ADZUNA_DETAIL = re.compile(r"https?://(?:www\.)?adzuna\.[a-z.]+/details/(\d+)")

# How long to wait for Adzuna's client-side hop off its own domain.
LAND_TIMEOUT_MS = 15_000

# How long to let a portal parse an uploaded CV and prefill the form from it
# before we go over the top with your own values.
PARSE_SETTLE_MS = 2_500


def adzuna_job_id(url: str) -> str | None:
    """The Adzuna listing id in a posting URL, if it is one."""
    match = ADZUNA_DETAIL.match(url or "")
    return match.group(1) if match else None


def resolve_posting(page, url: str) -> tuple[str, str | None]:
    """Navigate to the employer's own page, through an aggregator if needed.

    Adzuna's API hands back a link to an adzuna.de listing rather than to the
    employer, so opening it lands you on Adzuna with no form to fill. The
    employer link on that listing carries a short-lived signed token, which
    means it cannot be derived offline — it has to be read off the page, and
    read in a real browser, because Adzuna answers 403 to anything else.

    Still navigation by URL: the href is read from the DOM and handed to
    goto(). No button is clicked here either.

    Adzuna serves that hop a bot check, which a headless browser fails — so
    this works under `apply` as it normally runs, and not under `--headless`.
    Where it lands may itself be another aggregator (XING, StepStone) rather
    than the employer's form, which is the case `fill` on demand exists for.

    Returns the URL actually landed on, and a note when it went wrong.
    """
    page.goto(url, wait_until="domcontentloaded")

    job_id = adzuna_job_id(url)
    if job_id is None:
        return page.url, None

    link = page.locator(f'a[href*="/land/ad/{job_id}"]').first
    try:
        if link.count() == 0:
            return page.url, "no employer link on the Adzuna listing — open it yourself"
        href = link.get_attribute("href")
    except Exception as exc:
        return page.url, f"could not read the Adzuna link: {str(exc)[:60]}"

    if not href:
        return page.url, "the Adzuna employer link was empty"

    log.info("following Adzuna listing to the employer")
    try:
        page.goto(href, wait_until="domcontentloaded")
    except Exception as exc:
        return page.url, f"the employer link did not load: {str(exc)[:60]}"

    # That last hop is client-side, so domcontentloaded fires while still on
    # Adzuna. Without this wait the fill runs against the interstitial.
    try:
        page.wait_for_url(
            lambda current: "adzuna." not in (current or ""), timeout=LAND_TIMEOUT_MS
        )
    except Exception:
        return page.url, (
            "the Adzuna redirect did not complete — click through yourself, "
            "then ask for a fill"
        )
    return page.url, None


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
    """Classify and fill every field on `page`. Returns what was done.

    Attachments go first. Plenty of portals parse an uploaded CV and prefill
    the form from what they find in it, and that parse is asynchronous — so
    uploading last means the employer's guesses land on top of the values you
    actually typed into `applicant.yaml`. Uploading first inverts it: let the
    parser have its go, then correct it.
    """
    fields = extract_fields(page)
    files = [f for f in fields if f["type"] == "file"]
    actions = _fill_fields(page, files, applicant, attachments)

    if files and attachments:
        page.wait_for_timeout(PARSE_SETTLE_MS)
        # Re-read: a CV parse rewrites values, and stale ones would make the
        # report claim we replaced something that is no longer there.
        fields = extract_fields(page)

    rest = [f for f in fields if f["type"] != "file"]
    return actions + _fill_fields(page, rest, applicant, attachments)


@dataclass
class FieldPlan:
    """What to do with one field, decided without touching a browser.

    Splitting the decision from the doing is what lets the Chrome extension
    reuse this policy instead of reimplementing it in JavaScript. The
    credential refusal especially: two implementations of that agree right up
    until someone edits one of them.
    """

    id: str
    label: str
    key: str | None
    action: str        # "filled" | "skipped" | "unmatched"
    how: str = "none"  # "text" | "select" | "check" | "radio" | "file"
    value: str = ""    # text to type, option value, or attachment kind
    detail: str = ""

    def to_action(self) -> FieldAction:
        return FieldAction(self.label, self.key, self.action, self.detail)


def field_label(field: dict) -> str:
    """The most human name for a field, for the report."""
    return (
        field.get("label") or field.get("ariaLabel") or field.get("placeholder")
        or field.get("name") or "(unlabelled)"
    )


def plan_field(field: dict, applicant: Applicant, attachments: dict) -> FieldPlan:
    """Decide what one field should get. No browser, no side effects."""
    label = field_label(field)
    key = classify(
        label=field.get("label", ""),
        name=field.get("name", ""),
        placeholder=field.get("placeholder", ""),
        aria_label=field.get("ariaLabel", ""),
        field_type=field.get("type", ""),
    )

    def plan(action, how="none", value="", detail="", override_key=None):
        return FieldPlan(
            field.get("id", ""), label,
            key if override_key is None else override_key,
            action, how, value, detail,
        )

    # Before any value is looked up, so there is no path from a form field to
    # your answer bank for a credential. No opt-in, unlike self-identification.
    if is_credential(key, field.get("type", "")):
        return plan("skipped", detail="a credential — yours to type",
                    override_key="password")

    if is_eeo(key) and not applicant.fill_eeo:
        return plan("skipped", detail="self-identification — yours to answer")

    if field.get("type") == "file":
        kind = key or "resume"
        if kind not in attachments:
            return plan("unmatched", detail="no file to attach")
        return plan("filled", "file", kind, Path(str(attachments[kind])).name)

    value = resolve_value(field, key, applicant)
    if not value:
        return plan("unmatched", detail="no value in your applicant file")

    if field.get("tag") == "select":
        option = choose_option(field.get("options", []), value)
        if option is None:
            available = ", ".join(o["text"] for o in field.get("options", [])[:4])
            return plan("unmatched", detail=f"no option matches {value!r} (has: {available})")
        return plan("filled", "select", option, value)

    if field.get("type") == "checkbox":
        if value.strip().lower() in {"yes", "true", "1", "on"}:
            return plan("filled", "check", "true", "checked")
        return plan("skipped", detail=f"value {value!r} is not a yes")

    if field.get("type") == "radio":
        # One radio per option; take the one whose own label matches.
        own = (field.get("label") or "").strip().lower()
        if own and (own in value.lower() or value.lower() in own):
            return plan("filled", "radio", "true", (field.get("label") or "")[:40])
        return plan("skipped", detail="not the selected option")

    # Text, email, tel, url, number, date, textarea. Your applicant file wins
    # over a CV parser's guess — you wrote one and something inferred the
    # other — but say so, because a portal quietly disagreeing with you about
    # your own phone number is worth seeing.
    existing = (field.get("value") or "").strip()
    detail = value[:60]
    if existing and existing != value:
        detail = f"{value[:40]}  (replaced {existing[:24]!r})"
    return plan("filled", "text", value, detail)


def _fill_fields(page, fields, applicant: Applicant, attachments) -> list[FieldAction]:
    actions: list[FieldAction] = []

    for field in fields:
        plan = plan_field(field, applicant, attachments)
        if plan.action != "filled":
            actions.append(plan.to_action())
            continue
        try:
            _apply(page.locator(f'[data-jobpipe-id="{plan.id}"]'), plan, attachments)
        except Exception as exc:  # a form quirk must not abort the rest
            actions.append(FieldAction(plan.label, plan.key, "failed", str(exc)[:80]))
            continue
        actions.append(plan.to_action())

    return actions


def _apply(locator, plan: FieldPlan, attachments) -> None:
    """Carry out one plan. The only place this module writes to a page.

    Note what is absent: no click on anything but a checkbox or radio it was
    told to check, and `fill` sets a value directly without pressing Enter.
    """
    if plan.how == "file":
        locator.set_input_files(str(attachments[plan.value]))
    elif plan.how == "select":
        locator.select_option(plan.value)
    elif plan.how in {"check", "radio"}:
        locator.check()
    elif plan.how == "text":
        locator.fill(plan.value)


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


def gather_attachments(
    output_dir: Path, language: str = "en"
) -> dict[str, Path]:
    """Find the tailored documents to upload, making a PDF if needed.

    Prefers the requested language and falls back to whatever the folder
    has: asking for German when only the English resume was ever generated
    should attach the English one rather than nothing at all.
    """
    attachments: dict[str, Path] = {}
    if not output_dir or not output_dir.exists():
        return attachments

    suffixes = ["" if language == "en" else f".{language}", ""]

    for sfx in suffixes:
        pdf = output_dir / f"resume{sfx}.pdf"
        html = output_dir / f"resume{sfx}.html"
        if not pdf.exists() and html.exists():
            log.info("rendering %s", pdf)
            html_to_pdf(html, pdf)
        if pdf.exists():
            attachments["resume"] = pdf
            break

    # PDF first: an upload field takes a document, not Markdown. The .md is
    # a last resort so a folder generated before this existed still attaches
    # something rather than nothing.
    for extension in ("pdf", "md"):
        found = None
        for sfx in suffixes:
            candidate = output_dir / f"cover-letter{sfx}.{extension}"
            if candidate.exists():
                found = candidate
                break
        if found is not None:
            attachments["cover_letter"] = found
            break

    return attachments


def summarize(actions: list[FieldAction], url: str) -> str:
    """The report for one pass over a page."""
    filled = sum(1 for a in actions if a.action == "filled")
    lines = [f"\n{url}", f"\n{filled} of {len(actions)} fields filled:\n"]
    lines += [str(a) for a in actions] or ["  (no form fields on this page)"]

    if any(a.key == "password" for a in actions):
        lines.append(
            "\nThis page wants an account. Sign in or register yourself in the "
            "browser — this will not type a credential — then ask for a fill "
            "again once the form is on screen."
        )
    return "\n".join(lines)


HOLD_HELP = "\n[fill] fill the page you are on now   [Enter] close the browser"


def _hold(page, applicant: Applicant, attachments, actions) -> list[FieldAction]:
    """Keep the browser open, filling again whenever you ask.

    One fill at load time only works when the form is the first thing you
    see. Often it isn't: a login, a cookie wall, a "create an account" step
    or a multi-page wizard sits in front of it, and a single pass at the
    wrong moment fills nothing. So the browser stays open, you get to the
    real form yourself, and you ask for another pass once you're on it.

    Signing in is yours. This types no credential, and `fill` runs the very
    same fill_page as the first pass — so it still cannot submit.
    """
    print(HOLD_HELP)
    while True:
        try:
            command = input("jobpipe> ").strip().lower()
        except EOFError:  # no terminal attached; nothing more to wait for
            return actions

        if command in {"", "done", "close", "q", "quit", "exit"}:
            return actions
        if command in {"fill", "f", "refill"}:
            actions = fill_page(page, applicant, attachments)
            print(summarize(actions, page.url))
            print(HOLD_HELP)
            continue
        print("type 'fill', or press Enter to close")


def run(
    row,
    applicant: Applicant,
    headless: bool = False,
    wait: bool = True,
    language: str = "en",
) -> list[FieldAction]:
    """Open the posting's form, fill it, and hand control to the human."""
    from playwright.sync_api import sync_playwright

    url = apply_form_url(row["url"])
    output_dir = Path(row["output_dir"]) if row["output_dir"] else None
    attachments = gather_attachments(output_dir, language) if output_dir else {}

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
        page = browser.new_context().new_page()

        landed, note = resolve_posting(page, url)
        if note:
            print(f"  ! {note}")
        page.wait_for_timeout(1500)  # let client-rendered forms mount

        actions = fill_page(page, applicant, attachments)
        print(summarize(actions, landed))

        if wait and not headless:
            actions = _hold(page, applicant, attachments, actions)
        browser.close()

    return actions
