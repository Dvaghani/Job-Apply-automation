import json
import subprocess

import pytest
from pydantic import BaseModel

from jobpipe import llm
from jobpipe.config import Config
from jobpipe.llm import ClaudeCliBackend, LLMError, extract_json


class Shape(BaseModel):
    score: int
    reason: str


# --- JSON extraction from chatty CLI output ------------------------------

def test_extract_plain_object():
    assert extract_json('{"score": 73, "reason": "ok"}')["score"] == 73


def test_extract_from_markdown_fence():
    text = 'Here you go:\n```json\n{"score": 80, "reason": "fit"}\n```\nHope that helps!'
    assert extract_json(text)["score"] == 80


def test_extract_ignores_surrounding_prose():
    text = 'I reviewed the posting.\n{"score": 42, "reason": "weak"}\nLet me know.'
    assert extract_json(text)["reason"] == "weak"


def test_extract_handles_nested_objects():
    text = '{"a": {"b": {"c": 1}}, "d": 2}'
    assert extract_json(text)["a"]["b"]["c"] == 1


def test_extract_handles_braces_inside_strings():
    # A brace in a quoted value must not end the scan early.
    text = '{"reason": "uses {braces} in text", "score": 5}'
    assert extract_json(text)["score"] == 5


def test_extract_handles_escaped_quotes():
    text = r'{"reason": "they said \"hire\" twice", "score": 9}'
    assert extract_json(text)["score"] == 9


def test_extract_rejects_missing_object():
    with pytest.raises(LLMError, match="no JSON object"):
        extract_json("I'd rather not answer that.")


def test_extract_rejects_unterminated_object():
    with pytest.raises(LLMError, match="unterminated"):
        extract_json('{"score": 1')


def test_extract_rejects_empty():
    with pytest.raises(LLMError, match="empty"):
        extract_json("")


# --- CLI backend ----------------------------------------------------------

def fake_run(stdout, returncode=0):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return run


def cli(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    return ClaudeCliBackend("sonnet")


def test_cli_parses_a_good_response(monkeypatch):
    backend = cli(monkeypatch)
    envelope = json.dumps({
        "is_error": False,
        "result": 'Sure:\n```json\n{"score": 88, "reason": "strong"}\n```',
    })
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    got = backend.complete("sys", "prompt", Shape)
    assert got.score == 88 and got.reason == "strong"


def test_cli_sends_the_schema_in_the_prompt(monkeypatch):
    backend = cli(monkeypatch)
    captured = {}

    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": '{"score":1,"reason":"r"}'}), stderr=""
        )

    monkeypatch.setattr(llm.subprocess, "run", run)
    backend.complete("SYSTEM TEXT", "PROMPT TEXT", Shape)
    prompt = captured["cmd"][2]
    assert "SYSTEM TEXT" in prompt and "PROMPT TEXT" in prompt
    assert "JSON Schema" in prompt and '"score"' in prompt
    assert "--model" in captured["cmd"] and "sonnet" in captured["cmd"]


def test_cli_surfaces_a_nonzero_exit(monkeypatch):
    backend = cli(monkeypatch)
    monkeypatch.setattr(llm.subprocess, "run", fake_run("boom", returncode=1))
    with pytest.raises(LLMError, match="exited 1"):
        backend.complete("s", "p", Shape)


def test_cli_surfaces_a_reported_error(monkeypatch):
    backend = cli(monkeypatch)
    envelope = json.dumps({"is_error": True, "result": "usage limit reached"})
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    with pytest.raises(LLMError, match="usage limit"):
        backend.complete("s", "p", Shape)


def test_cli_rejects_a_response_of_the_wrong_shape(monkeypatch):
    backend = cli(monkeypatch)
    envelope = json.dumps({"result": '{"unexpected": true}'})
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    with pytest.raises(LLMError, match="did not match"):
        backend.complete("s", "p", Shape)


def test_cli_reports_a_refusal_clearly(monkeypatch):
    # The model declining leaves no JSON; that must be a clean error, not a crash.
    backend = cli(monkeypatch)
    envelope = json.dumps({"result": "I won't produce a fabricated score."})
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    with pytest.raises(LLMError, match="no JSON object"):
        backend.complete("s", "p", Shape)


def test_cli_times_out_cleanly(monkeypatch):
    backend = cli(monkeypatch)

    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr(llm.subprocess, "run", run)
    with pytest.raises(LLMError, match="timed out"):
        backend.complete("s", "p", Shape)


def test_missing_cli_explains_the_alternatives(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    with pytest.raises(LLMError, match="Claude Code"):
        ClaudeCliBackend("sonnet")


# --- backend selection ----------------------------------------------------

def test_build_selects_cli_backend(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    assert llm.build(Config(backend="claude-cli")).name == "claude-cli"


def test_build_accepts_aliases(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    for alias in ("claude-cli", "claude-code", "cli", "CLI"):
        assert llm.build(Config(backend=alias)).name == "claude-cli"


def test_build_rejects_an_unknown_backend():
    with pytest.raises(LLMError, match="unknown backend"):
        llm.build(Config(backend="gpt"))


def test_cli_reports_a_setup_error_without_a_traceback(monkeypatch, tmp_path, capsys):
    """A missing CLI is the likeliest first-run failure — it must read as
    guidance, not a stack trace."""
    from jobpipe.cli import main
    from jobpipe import db
    from jobpipe.models import Job

    (tmp_path / "profile.md").write_text("Backend engineer.")
    (tmp_path / "config.yaml").write_text(
        f"backend: claude-cli\ndb_path: {tmp_path / 'j.db'}\n"
        f"profile_path: {tmp_path / 'profile.md'}\n"
    )
    conn = db.connect(tmp_path / "j.db")
    db.upsert_job(conn, Job(source="s", source_id="1", company="C", title="T", url="u"))
    conn.commit()

    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    code = main(["-c", str(tmp_path / "config.yaml"), "score"])

    assert code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "Claude Code" in err


# --- control characters in model output -----------------------------------
#
# A cover letter is prose with paragraphs. Models write those as literal
# newlines inside the JSON string rather than \n escapes, and the default
# parser rejects the entire response over it — throwing away a call that
# already cost time and usage. It showed up first on German output, where
# the text is longer.

def test_a_literal_newline_in_a_string_is_accepted():
    block = '{"cover_letter": "Sehr geehrte Damen und Herren,\n\nmit Interesse..."}'
    assert "\n\n" in extract_json(block)["cover_letter"]


def test_paragraph_breaks_survive_rather_than_being_stripped():
    block = '{"summary": "One.\n\nTwo."}'
    assert extract_json(block)["summary"] == "One.\n\nTwo."


def test_a_tab_inside_a_string_is_accepted():
    assert extract_json('{"a": "x\ty"}')["a"] == "x\ty"


def test_escaped_newlines_still_work():
    assert extract_json('{"a": "x\ny"}')["a"] == "x\ny"


def test_genuinely_broken_json_still_fails():
    with pytest.raises(LLMError, match="malformed JSON"):
        extract_json('{"a": "b",, }')


def test_a_parse_error_shows_the_text_around_it():
    """A character offset alone says nothing in 4,000 characters of prose."""
    with pytest.raises(LLMError, match="near:"):
        extract_json('{"a": "b",, }')


# --- Antigravity CLI backend ----------------------------------------------

def agy(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/agy")
    return llm.AntigravityCliBackend("gemini-3.8-flash-high")


def test_agy_parses_a_good_response(monkeypatch):
    backend = agy(monkeypatch)
    envelope = json.dumps({
        "conversation_id": "abc",
        "status": "SUCCESS",
        "response": '{"score": 45, "reason": "thin"}\n',
    })
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    got = backend.complete("sys", "prompt", Shape)
    assert got.score == 45 and got.reason == "thin"


def test_agy_attaches_the_prompt_to_the_flag(monkeypatch):
    """Passed as a separate argument the CLI takes the next flag as its prompt."""
    backend = agy(monkeypatch)
    captured = {}

    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=json.dumps({
                "status": "SUCCESS", "response": '{"score": 1, "reason": "x"}'
            }),
            stderr="",
        )

    monkeypatch.setattr(llm.subprocess, "run", run)
    backend.complete("sys", "prompt", Shape)

    prompts = [a for a in captured["cmd"] if a.startswith("--print=")]
    assert len(prompts) == 1
    assert "prompt" in prompts[0] and "sys" in prompts[0]
    # the schema has to reach the model, since --json-schema cannot be used
    assert "score" in prompts[0]
    assert "-p" not in captured["cmd"]
    # untrusted posting text must not be expanded as slash commands
    assert "--disable-slash-commands" in captured["cmd"]
    assert "--model" in captured["cmd"]


def test_agy_surfaces_a_failed_status(monkeypatch):
    backend = agy(monkeypatch)
    envelope = json.dumps({"status": "ERROR", "response": "quota exhausted"})
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    with pytest.raises(LLMError, match="ERROR"):
        backend.complete("sys", "prompt", Shape)


def test_agy_explains_a_silent_nonzero_exit(monkeypatch):
    """The CLI exits non-zero with both streams empty when it cannot write."""
    backend = agy(monkeypatch)
    monkeypatch.setattr(llm.subprocess, "run", fake_run("", returncode=2))
    with pytest.raises(LLMError, match="no output at all"):
        backend.complete("sys", "prompt", Shape)


def test_agy_needs_the_binary(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    with pytest.raises(LLMError, match="not on PATH"):
        llm.AntigravityCliBackend("gemini-3.8-flash-high")


def test_build_selects_the_antigravity_backend(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/agy")

    class Cfg:
        backend = "antigravity"
        model = "claude-opus-5"
        cli_model = "sonnet"
        cli_timeout = 300
        agy_model = "gemini-3.8-flash-high"
        agy_timeout = 600

    backend = llm.build(Cfg())
    assert backend.name == "antigravity"
    assert backend.model == "gemini-3.8-flash-high"
    assert backend.timeout == 600


def test_agy_keeps_a_valid_answer_marked_error(monkeypatch):
    """At high concurrency a complete answer arrives with the turn ERRORed.

    The status covers the CLI's own bookkeeping as well as the model, so it
    can fail after the answer exists. Parsing is the real check.
    """
    backend = agy(monkeypatch)
    envelope = json.dumps({
        "status": "ERROR",
        "response": '{"score": 62, "reason": "solid"}\n',
    })
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    got = backend.complete("sys", "prompt", Shape)
    assert got.score == 62 and got.reason == "solid"


def test_agy_still_rejects_a_truncated_answer(monkeypatch):
    """A reply cut off mid-generation cannot satisfy the schema."""
    backend = agy(monkeypatch)
    envelope = json.dumps({
        "status": "ERROR",
        "response": '{"score": 62, "reason": "it was going fine until',
    })
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    with pytest.raises(LLMError, match="ERROR"):
        backend.complete("sys", "prompt", Shape)


def test_agy_rejects_a_wrong_shape_even_when_successful(monkeypatch):
    backend = agy(monkeypatch)
    envelope = json.dumps({"status": "SUCCESS", "response": '{"score": "high"}'})
    monkeypatch.setattr(llm.subprocess, "run", fake_run(envelope))
    with pytest.raises(LLMError, match="not usable"):
        backend.complete("sys", "prompt", Shape)
