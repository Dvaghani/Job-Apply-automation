"""Model backends.

Two ways to reach Claude, because they bill differently:

- `api`        the Anthropic API, needs ANTHROPIC_API_KEY and API credits.
- `claude-cli` shells out to the Claude Code CLI in headless mode, which
               runs on a Claude Pro/Max subscription. No API key.

A Claude Pro subscription does *not* include API access — those are
separate products — so `claude-cli` is the right backend if Pro is what
you have.
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


def build(config) -> ApiBackend | ClaudeCliBackend:
    """Construct the backend named in config."""
    backend = (getattr(config, "backend", "") or "api").lower()
    if backend == "api":
        return ApiBackend(config.model)
    if backend in {"claude-cli", "claude-code", "cli"}:
        return ClaudeCliBackend(config.cli_model, timeout=config.cli_timeout)
    raise LLMError(f"unknown backend {backend!r} — use 'api' or 'claude-cli'")
