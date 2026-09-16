"""Model backends.

Three ways to reach a model, because they bill differently:

- `api`         the Anthropic API, needs ANTHROPIC_API_KEY and API credits.
- `claude-cli`  shells out to the Claude Code CLI in headless mode, which
                runs on a Claude Pro/Max subscription. No API key.
- `antigravity` shells out to the Antigravity CLI (`agy`) in print mode,
                which runs Gemini on a signed-in Google account. No API key.

A Claude Pro subscription does *not* include API access — those are
separate products — so `claude-cli` is the right backend if Pro is what
you have. `antigravity` is the same trade on someone else's model: a
fresh process per call, no key, and whatever plan the CLI is signed in to.

The two CLI backends differ only in how the process is spelled and where
the reply sits in its JSON envelope, so both build the same prompt and
share `extract_json`.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_TIMEOUT = 300


class LLMError(RuntimeError):
    pass


def _around(text: str, position: int, width: int = 60) -> str:
    """A short excerpt either side of `position`, for an error message.

    A parse error reports a character offset, which says nothing on its own
    when the document is four thousand characters of someone's cover letter.
    """
    start = max(0, position - width)
    return text[start : position + width]


def extract_json(text: str) -> dict:
    """Pull the first complete JSON object out of a model's reply.

    The CLI returns prose, so the object may be fenced in ```json, wrapped
    in explanation, or both. Brace matching is used rather than a regex so
    nested objects survive; strings are tracked so a brace inside a quoted
    value doesn't end the scan early.
    """
    if not text:
        raise LLMError("empty response")

    start = text.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in response: {text[:200]!r}")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                block = text[start : i + 1]
                try:
                    # strict=False allows raw control characters inside
                    # strings. A model writing a multi-paragraph cover letter
                    # emits literal newlines there rather than \n escapes, and
                    # the default parser rejects the whole response over it —
                    # losing a call that already cost time and usage. The
                    # newlines are what we want in the text anyway.
                    return json.loads(block, strict=False)
                except json.JSONDecodeError as exc:
                    raise LLMError(
                        f"malformed JSON in response: {exc}\n"
                        f"  near: {_around(block, exc.pos)!r}"
                    ) from exc
    raise LLMError("unterminated JSON object in response")


def _schema_instruction(model_cls: type[BaseModel]) -> str:
    schema = json.dumps(model_cls.model_json_schema(), indent=2)
    return (
        "Reply with a single JSON object and nothing else — no preamble, no "
        "explanation, no markdown fence. It must validate against this JSON "
        f"Schema:\n\n{schema}"
    )


class ApiBackend:
    """Anthropic API via the official SDK."""

    name = "api"

    def __init__(self, model: str):
        import anthropic

        self.model = model
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()

    def complete(self, system: str, prompt: str, model_cls: type[T], max_tokens: int = 8000) -> T:
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=model_cls,
            )
        except self._anthropic.APIError as exc:
            raise LLMError(str(exc)) from exc
        return response.parsed_output


class ClaudeCliBackend:
    """Claude Code CLI in headless mode — runs on a Pro/Max subscription.

    Slower and chattier than the API (each call is a fresh CLI process),
    but it needs no API key and no separate billing.
    """

    name = "claude-cli"

    def __init__(self, model: str, timeout: int = DEFAULT_TIMEOUT):
        self.model = model
        self.timeout = timeout
        if not shutil.which("claude"):
            raise LLMError(
                "The `claude` CLI is not on PATH. Install Claude Code "
                "(https://claude.com/claude-code) and run `claude` once to sign in, "
                "or switch to `backend: api` with an ANTHROPIC_API_KEY."
            )

    def complete(self, system: str, prompt: str, model_cls: type[T], max_tokens: int = 8000) -> T:
        full_prompt = (
            f"{system}\n\n---\n\n{prompt}\n\n---\n\n{_schema_instruction(model_cls)}"
        )
        command = ["claude", "-p", full_prompt, "--output-format", "json"]
        if self.model:
            command += ["--model", self.model]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                # The CLI emits UTF-8. Without this, `text=True` decodes with
                # the system locale — cp1252 on Windows — and every em dash,
                # ü, ö and ß in a tailored resume or a German posting's score
                # comes back mangled. The files are written as UTF-8, so the
                # corruption happens here, before anything is saved.
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude CLI timed out after {self.timeout}s") from exc
        except OSError as exc:
            raise LLMError(f"could not run the claude CLI: {exc}") from exc

        if completed.returncode != 0:
            raise LLMError(
                f"claude CLI exited {completed.returncode}: "
                f"{(completed.stderr or completed.stdout)[:300]}"
            )

        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude CLI returned non-JSON: {exc}") from exc

        if envelope.get("is_error"):
            raise LLMError(f"claude CLI reported an error: {envelope.get('result', '')[:300]}")

        payload = extract_json(envelope.get("result", ""))
        try:
            return model_cls.model_validate(payload)
        except ValidationError as exc:
            raise LLMError(f"response did not match the expected shape: {exc}") from exc


class AntigravityCliBackend:
    """Antigravity CLI (`agy`) in print mode — runs on a Google account.

    Gemini instead of Claude, reached the way `claude-cli` reaches Claude:
    a fresh process per call, no API key. Slower than the API — a scoring
    call is tens of seconds, and the CLI sends a large system prompt of its
    own ahead of ours, so a short prompt is never a short call.
    """

    name = "antigravity"
    binary = "agy"

    def __init__(self, model: str, timeout: int = DEFAULT_TIMEOUT):
        self.model = model
        self.timeout = timeout
        if not shutil.which(self.binary):
            raise LLMError(
                "The `agy` CLI is not on PATH. Install the Antigravity CLI "
                "(https://antigravity.google/cli) and run `agy` once to sign in, "
                "or switch to another backend."
            )

    def complete(self, system: str, prompt: str, model_cls: type[T], max_tokens: int = 8000) -> T:
        full_prompt = (
            f"{system}\n\n---\n\n{prompt}\n\n---\n\n{_schema_instruction(model_cls)}"
        )

        command = [
            self.binary,
            # The prompt has to be attached to the flag. Passed as a separate
            # argument, the CLI takes the *next flag* as its prompt and drops
            # the real one — it says so and exits 2 rather than failing quietly.
            f"--print={full_prompt}",
            "--output-format", "json",
            # A job description is untrusted text that reaches this prompt
            # verbatim. Without this, a posting containing a /word is expanded
            # as a slash command before the model ever sees it.
            "--disable-slash-commands",
            # Let the CLI give up just before we do, so a stall comes back as
            # its own error instead of a killed process with nothing to report.
            "--print-timeout", f"{max(30, self.timeout - 15)}s",
        ]
        if self.model:
            command += ["--model", self.model]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                # Same reason as the Claude CLI backend: the reply carries the
                # umlauts and em dashes of a German posting, and the system
                # locale would mangle them on the way in.
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"agy CLI timed out after {self.timeout}s") from exc
        except OSError as exc:
            raise LLMError(f"could not run the agy CLI: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            if not detail:
                # Seen in practice when the CLI cannot write its own state —
                # a full disk, or a session that is no longer signed in. It
                # exits non-zero with both streams empty and says nothing.
                detail = (
                    "no output at all. Run `agy` yourself to check it is signed "
                    "in and has room to write ~/.gemini/antigravity-cli"
                )
            raise LLMError(f"agy CLI exited {completed.returncode}: {detail[:300]}")

        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"agy CLI returned non-JSON: {exc}") from exc

        # The reply is in "response". Deliberately not --json-schema: that
        # binds the schema to the agent's own completion report rather than
        # to the answer, and returns a filled-in summary of the task instead
        # of the score you asked for.
        status = str(envelope.get("status", "")).upper()
        response = str(envelope.get("response") or "")

        try:
            payload = extract_json(response)
            answer = model_cls.model_validate(payload)
        except (LLMError, ValidationError) as exc:
            detail = f"agy CLI reported {status or 'no status'}" if status != "SUCCESS" else str(exc)
            raise LLMError(
                f"{detail} — response ({len(response)} chars) was not usable: "
                f"{response[:200]!r}"
            ) from exc

        if status != "SUCCESS":
            # Seen at high concurrency: a complete, valid answer arrives with
            # the turn marked ERROR. The status covers the CLI's own session
            # bookkeeping as well as the model, so it can fail after the
            # answer is generated. Parsing is the real check — a truncated or
            # empty reply cannot satisfy the schema and has already raised
            # above — so a call that was paid for and answered is kept rather
            # than thrown away over the CLI's own housekeeping.
            log.warning(
                "agy reported %s but returned a valid answer; using it", status
            )
        return answer


def build(config) -> ApiBackend | ClaudeCliBackend | AntigravityCliBackend:
    """Construct the backend named in config."""
    backend = (getattr(config, "backend", "") or "api").lower()
    if backend == "api":
        return ApiBackend(config.model)
    if backend in {"claude-cli", "claude-code", "cli"}:
        return ClaudeCliBackend(config.cli_model, timeout=config.cli_timeout)
    if backend in {"antigravity", "antigravity-cli", "agy"}:
        return AntigravityCliBackend(config.agy_model, timeout=config.agy_timeout)
    raise LLMError(
        f"unknown backend {backend!r} — use 'api', 'claude-cli' or 'antigravity'"
    )
