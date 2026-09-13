"""Background command runner behind the dashboard.

The dashboard doesn't reimplement the pipeline — it shells out to the same
CLI the terminal uses, one subprocess per command, and streams the output
back to the page. That's deliberate: two entry points into the same logic
drift apart, two entry points into the same *process* can't.

What may be started is an allowlist (`COMMANDS`), not free-form arguments.
This turns HTTP requests into processes, so the set of processes it can make
is fixed here, in code, rather than assembled from whatever the request
happened to contain.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

# Fingerprints are 16 hex chars (models.Job.fingerprint). Anything else never
# reaches a command line.
FINGERPRINT_RE = re.compile(r"[0-9a-f]{16}")

# Output is held in memory for the page to poll. A `doctor --probe` over a big
# watchlist is a few KB; this is far above anything the CLI actually emits, and
# past it the oldest half is dropped rather than growing without bound.
MAX_OUTPUT = 512 * 1024

# How many finished tasks to keep for the console history.
HISTORY = 20


class TaskError(RuntimeError):
    """A command could not be started as asked."""


@dataclass(frozen=True)
class CommandSpec:
    """One runnable command and the options the dashboard may pass it."""

    name: str
    label: str
    blurb: str = ""
    flags: dict[str, str] = field(default_factory=dict)  # option -> flag
    ints: dict[str, str] = field(default_factory=dict)   # option -> flag with value
    # option -> (flag, allowed values). A fixed set, so the page can offer a
    # dropdown without the value itself becoming free-form input.
    choices: dict = field(default_factory=dict)
    jobs: str = "no"           # "no" | "optional" | "one"
    interactive: bool = False  # holds itself open waiting on you
    costly: bool = False       # spends model usage or API quota


COMMANDS: dict[str, CommandSpec] = {
    "run": CommandSpec(
        "run", "Run", "Ingest, then score. The daily command.",
        ints={"limit": "--limit"}, costly=True,
    ),
    "ingest": CommandSpec(
        "ingest", "Ingest", "Fetch postings from every configured source.",
    ),
    "score": CommandSpec(
        "score", "Score", "Score everything unscored with Claude.",
        flags={"rescore": "--rescore"}, ints={"limit": "--limit"},
        jobs="optional", costly=True,
    ),
    "tailor": CommandSpec(
        "tailor", "Tailor", "Write a tailored resume for approved jobs.",
        flags={"no_cover_letter": "--no-cover-letter", "strict": "--strict"},
        choices={"language": ("--language", ("en", "de"))},
        jobs="optional", costly=True,
    ),
    "apply": CommandSpec(
        "apply", "Open & fill form",
        "Open the posting's form and fill it. Never submits.",
        jobs="one", interactive=True,
    ),
    "doctor": CommandSpec(
        "doctor", "Doctor", "Check config, files, backend and sources.",
        flags={"probe": "--probe"},
    ),
    "stats": CommandSpec("stats", "Stats", "Counts by status."),
}


def build_argv(command: str, options: dict | None = None) -> list[str]:
    """Translate a dashboard request into an argument list.

    Raises TaskError on anything outside the allowlist. Returns a list, never
    a string — nothing here is ever handed to a shell.
    """
    spec = COMMANDS.get(command)
    if spec is None:
        raise TaskError(f"Unknown command: {command}")

    options = options or {}
    unknown = (
        set(options) - set(spec.flags) - set(spec.ints) - set(spec.choices) - {"jobs"}
    )
    if unknown:
        raise TaskError(f"{command} takes no option {', '.join(sorted(unknown))}")

    argv = [spec.name]

    for key, flag in spec.flags.items():
        if options.get(key):
            argv.append(flag)

    for key, flag in spec.ints.items():
        value = options.get(key)
        # Unset only. Not `value in (None, "", False)` — 0 == False in Python,
        # so that spelling quietly swallows a typed 0 instead of rejecting it.
        if value is None or value is False or (
            isinstance(value, str) and not value.strip()
        ):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise TaskError(f"{flag} must be a whole number") from None
        if not 1 <= number <= 10_000:
            raise TaskError(f"{flag} must be between 1 and 10000")
        argv += [flag, str(number)]

    for key, (flag, allowed) in spec.choices.items():
        value = options.get(key)
        if value in (None, ""):
            continue
        if value not in allowed:
            raise TaskError(
                f"{flag} must be one of {', '.join(allowed)}, not {value!r}"
            )
        argv += [flag, str(value)]

    jobs = options.get("jobs") or []
    if isinstance(jobs, str):
        jobs = [jobs]
    for fingerprint in jobs:
        if not FINGERPRINT_RE.fullmatch(str(fingerprint)):
            raise TaskError(f"Not a job fingerprint: {fingerprint!r}")

    if spec.jobs == "no" and jobs:
        raise TaskError(f"{command} does not take a job")
    if spec.jobs == "one" and len(jobs) != 1:
        raise TaskError(f"{command} needs exactly one job")
    argv += [str(j) for j in jobs]

    return argv


class Task:
    """One running or finished command, and everything it has printed."""

    def __init__(self, task_id: str, spec: CommandSpec, argv: list[str]):
        self.id = task_id
        self.command = spec.name
        self.label = spec.label
        self.interactive = spec.interactive
        self.argv = argv
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.returncode: int | None = None
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._output = ""
        self._dropped = 0  # chars trimmed off the front, so cursors stay absolute

    @property
    def running(self) -> bool:
        return self.finished_at is None

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    def append(self, text: str) -> None:
        with self._lock:
            self._output += text
            if len(self._output) > MAX_OUTPUT:
                half = len(self._output) // 2
                self._output = self._output[half:]
                self._dropped += half

    def read(self, cursor: int = 0) -> tuple[str, int]:
        """Return output from `cursor` on, plus the new cursor.

        The cursor is an absolute character offset into everything the task
        has ever printed, so trimming old output can't make the page replay
        text it already has.
        """
        with self._lock:
            start = max(0, int(cursor) - self._dropped)
            return self._output[start:], self._dropped + len(self._output)

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        self.finished_at = time.time()

    def state(self, cursor: int = 0) -> dict:
        output, new_cursor = self.read(cursor)
        return {
            "id": self.id,
            "command": self.command,
            "label": self.label,
            "argv": self.argv,
            "interactive": self.interactive,
            "running": self.running,
            "returncode": self.returncode,
            "elapsed": round(self.elapsed, 1),
            "output": output,
            "cursor": new_cursor,
        }


class Runner:
    """Starts CLI subprocesses, one at a time, and keeps their output.

    One at a time on purpose: two `ingest` runs racing each other write the
    same rows, and a `score` running under a `tailor` spends model usage on
    work the other is about to redo. The pipeline is a queue, not a pool.
    """

    def __init__(
        self,
        config_path: str = "",
        cwd: str | os.PathLike | None = None,
        cli: list[str] | None = None,
    ):
        self.config_path = config_path
        self.cwd = str(cwd or os.getcwd())
        # How to invoke the CLI. Overridable so the tests can drive the runner
        # with a stand-in child, and so an odd install can point at its own
        # interpreter.
        self.cli = list(cli) if cli else [sys.executable, "-m", "jobpipe.cli"]
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._order: list[str] = []

    # -- inspection ------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def current(self) -> Task | None:
        with self._lock:
            return self._current_locked()

    def _current_locked(self) -> Task | None:
        for task_id in reversed(self._order):
            task = self._tasks[task_id]
            if task.running:
                return task
        return None

    def latest(self) -> Task | None:
        with self._lock:
            return self._tasks[self._order[-1]] if self._order else None

    def recent(self, limit: int = 10) -> list[Task]:
        with self._lock:
            return [self._tasks[i] for i in reversed(self._order[-limit:])]

    # -- control ---------------------------------------------------------

    def start(self, command: str, options: dict | None = None) -> Task:
        argv = build_argv(command, options)
        spec = COMMANDS[command]

        with self._lock:
            busy = self._current_locked()
            if busy is not None:
                raise TaskError(
                    f"{busy.label} is still running. Wait for it, or stop it first."
                )
            task = Task(uuid.uuid4().hex[:12], spec, argv)
            self._tasks[task.id] = task
            self._order.append(task.id)
            self._prune_locked()

        cli = list(self.cli)
        if self.config_path:
            cli += ["-c", self.config_path]

        env = dict(os.environ)
        # Without these the child's output arrives in one lump at exit, which
        # makes a nine-minute scoring run look like a hang; and on Windows the
        # console encoding can't render the CLI's own output (— · →).
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            task.proc = subprocess.Popen(
                cli + argv,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            task.append(f"could not start: {exc}\n")
            task.finish(-1)
            raise TaskError(f"Could not start {command}: {exc}") from exc

        task.append("$ jobpipe " + " ".join(argv) + "\n\n")
        threading.Thread(target=self._pump, args=(task,), daemon=True).start()
        return task

    def send(self, task_id: str, text: str = "\n") -> None:
        """Answer a command that is waiting on stdin.

        `apply` fills the form and then blocks on input() so the browser stays
        open while you work through it. From a terminal you press Enter; from
        the dashboard, this is that Enter.
        """
        task = self.get(task_id)
        if task is None:
            raise TaskError("No such task")
        if not task.running or task.proc is None or task.proc.stdin is None:
            raise TaskError("That command has already finished")
        try:
            task.proc.stdin.write(text)
            task.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise TaskError(f"Could not reach the command: {exc}") from exc

    def stop(self, task_id: str) -> None:
        task = self.get(task_id)
        if task is None:
            raise TaskError("No such task")
        if task.running and task.proc is not None:
            task.append("\n^ stopped\n")
            task.proc.terminate()

    # -- internals -------------------------------------------------------

    def _pump(self, task: Task) -> None:
        """Drain the child's output a character at a time.

        A character rather than a line because input() writes its prompt with
        no trailing newline — read by lines and the one message that matters
        ("press Enter when you're done") is the one you never see.
        """
        stream = task.proc.stdout
        try:
            while True:
                chunk = stream.read(1)
                if not chunk:
                    break
                task.append(chunk)
        except (OSError, ValueError):
            pass
        finally:
            returncode = task.proc.wait()
            for pipe in (task.proc.stdin, task.proc.stdout):
                try:
                    if pipe is not None:
                        pipe.close()
                except OSError:
                    pass
            task.finish(returncode)

    def _prune_locked(self) -> None:
        while len(self._order) > HISTORY:
            oldest = self._order[0]
            if self._tasks[oldest].running:
                break
            self._order.pop(0)
            self._tasks.pop(oldest, None)
