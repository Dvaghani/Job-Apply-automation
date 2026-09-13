"""The dashboard's command runner.

Two things matter here. What it can spawn must be exactly the allowlist —
this is the one place HTTP requests turn into processes. And a command that
waits on stdin (`apply`, holding the browser open) must still be reachable,
because from the dashboard there is no terminal to press Enter in.
"""

from __future__ import annotations

import sys
import time

import pytest

from jobpipe import tasks
from jobpipe.tasks import Runner, TaskError, build_argv

FP = "a" * 16
FP2 = "0123456789abcdef"

# Stands in for the CLI: prints, then blocks on stdin exactly as `apply` does
# while you work through the form.
WAITER = (
    "import sys\n"
    "sys.stdout.write('filled 12 of 16\\n')\n"
    "sys.stdout.write('Press Enter when done... ')\n"
    "sys.stdout.flush()\n"
    "sys.stdin.readline()\n"
    "sys.stdout.write('\\nclosed\\n')\n"
)

ECHO = "import sys; print('args:', ' '.join(sys.argv[1:]))"


def waiter_runner(script: str = WAITER) -> Runner:
    return Runner(config_path="", cli=[sys.executable, "-u", "-c", script])


def wait_until(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- the allowlist -------------------------------------------------------


def test_every_command_builds_its_own_name():
    for name, spec in tasks.COMMANDS.items():
        options = {"jobs": [FP]} if spec.jobs == "one" else None
        assert build_argv(name, options)[0] == name


def test_unknown_command_is_refused():
    with pytest.raises(TaskError, match="Unknown command"):
        build_argv("rm")


def test_unknown_option_is_refused():
    with pytest.raises(TaskError, match="no option"):
        build_argv("ingest", {"probe": True})


def test_flags_are_translated_not_passed_through():
    argv = build_argv("tailor", {"no_cover_letter": True, "strict": True})
    assert argv == ["tailor", "--no-cover-letter", "--strict"]


def test_false_flag_is_omitted():
    assert build_argv("doctor", {"probe": False}) == ["doctor"]


def test_int_option_must_be_a_number():
    with pytest.raises(TaskError, match="whole number"):
        build_argv("score", {"limit": "; rm -rf /"})


def test_int_option_must_be_in_range():
    with pytest.raises(TaskError, match="between"):
        build_argv("score", {"limit": 0})
    with pytest.raises(TaskError, match="between"):
        build_argv("score", {"limit": 99999})


def test_blank_int_option_is_omitted():
    assert build_argv("score", {"limit": ""}) == ["score"]
    assert build_argv("score", {"limit": 5}) == ["score", "--limit", "5"]


def test_only_real_fingerprints_reach_a_command_line():
    for bad in ["../../etc/passwd", "--headless", "", "ZZZZ", FP + "0"]:
        with pytest.raises(TaskError, match="fingerprint"):
            build_argv("apply", {"jobs": [bad]})


def test_apply_needs_exactly_one_job():
    with pytest.raises(TaskError, match="exactly one"):
        build_argv("apply")
    with pytest.raises(TaskError, match="exactly one"):
        build_argv("apply", {"jobs": [FP, FP2]})
    assert build_argv("apply", {"jobs": [FP]}) == ["apply", FP]


def test_commands_that_take_no_job_refuse_one():
    with pytest.raises(TaskError, match="does not take a job"):
        build_argv("ingest", {"jobs": [FP]})


def test_tailor_takes_any_number_of_jobs():
    assert build_argv("tailor") == ["tailor"]
    assert build_argv("tailor", {"jobs": [FP, FP2]}) == ["tailor", FP, FP2]


# -- output buffering ----------------------------------------------------


def test_cursor_returns_only_what_is_new():
    task = tasks.Task("t", tasks.COMMANDS["stats"], ["stats"])
    task.append("one\n")
    text, cursor = task.read(0)
    assert text == "one\n"
    task.append("two\n")
    text, cursor2 = task.read(cursor)
    assert text == "two\n"
    assert cursor2 > cursor


def test_trimmed_output_does_not_replay(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_OUTPUT", 100)
    task = tasks.Task("t", tasks.COMMANDS["stats"], ["stats"])
    task.append("x" * 90)
    _, cursor = task.read(0)
    task.append("y" * 30)  # pushes it over, oldest half dropped
    text, _ = task.read(cursor)
    # The tail is new text only — never a re-run of what the page already had.
    assert set(text) <= {"y"}


# -- running -------------------------------------------------------------


def test_arguments_reach_the_child_as_a_list():
    runner = waiter_runner(ECHO)
    task = runner.start("tailor", {"jobs": [FP], "strict": True})
    assert wait_until(lambda: not task.running)
    assert f"args: tailor --strict {FP}" in task.read()[0]
    assert task.returncode == 0


def test_a_waiting_command_is_answerable_from_the_dashboard():
    """`apply` blocks on input(). The dashboard's Done button is that Enter."""
    runner = waiter_runner()
    task = runner.start("apply", {"jobs": [FP]})

    assert wait_until(lambda: "Press Enter" in task.read()[0])
    # The prompt has no trailing newline: reading by lines would never show it.
    assert task.running

    runner.send(task.id)
    assert wait_until(lambda: not task.running)
    assert "closed" in task.read()[0]
    assert task.returncode == 0


def test_one_command_at_a_time():
    runner = waiter_runner()
    task = runner.start("apply", {"jobs": [FP]})
    assert wait_until(lambda: "Press Enter" in task.read()[0])

    with pytest.raises(TaskError, match="still running"):
        runner.start("ingest")

    runner.send(task.id)
    assert wait_until(lambda: not task.running)
    runner.start("stats")  # the slot is free again


def test_stop_ends_a_waiting_command():
    runner = waiter_runner()
    task = runner.start("apply", {"jobs": [FP]})
    assert wait_until(lambda: "Press Enter" in task.read()[0])
    runner.stop(task.id)
    assert wait_until(lambda: not task.running)
    assert task.returncode != 0


def test_answering_a_finished_command_is_an_error():
    runner = waiter_runner(ECHO)
    task = runner.start("stats")
    assert wait_until(lambda: not task.running)
    with pytest.raises(TaskError, match="already finished"):
        runner.send(task.id)


def test_current_tracks_the_running_command():
    runner = waiter_runner()
    assert runner.current() is None
    task = runner.start("apply", {"jobs": [FP]})
    assert runner.current() is task
    runner.send(task.id)
    assert wait_until(lambda: runner.current() is None)


def test_history_is_bounded(monkeypatch):
    monkeypatch.setattr(tasks, "HISTORY", 3)
    runner = waiter_runner(ECHO)
    ids = []
    for _ in range(5):
        task = runner.start("stats")
        assert wait_until(lambda: not task.running)
        ids.append(task.id)
    assert runner.get(ids[0]) is None
    assert runner.get(ids[-1]) is not None
