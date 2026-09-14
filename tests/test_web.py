"""The dashboard and review pages.

The runner is stubbed here: what's under test is the HTTP surface — that it
refuses what it should, and that the pages render what the database holds.
Actually running commands is covered in test_tasks.py.
"""

from __future__ import annotations

import pytest

from jobpipe import db, web
from jobpipe.config import Config
from jobpipe.models import STATUS_APPROVED, STATUS_SCORED, Job
from jobpipe.tasks import COMMANDS, TaskError

TOKEN = "test-token"


class FakeTask:
    def __init__(self, command="ingest", running=True):
        self.id = "task123"
        self.command = command
        self.label = COMMANDS[command].label
        self.running = running

    def state(self, cursor=0):
        return {
            "id": self.id, "command": self.command, "label": self.label,
            "argv": [self.command], "interactive": False, "running": self.running,
            "returncode": None if self.running else 0, "elapsed": 0.4,
            "output": "hello\n"[cursor:], "cursor": 6,
        }


class FakeRunner:
    """Records what the HTTP layer asked for, without spawning anything."""

    def __init__(self):
        self.calls = []
        self.sent = []
        self.sent_text = []
        self.stopped = []
        self.task = None
        self.fail = None

    def start(self, command, options=None):
        if self.fail:
            raise TaskError(self.fail)
        self.calls.append((command, options))
        self.task = FakeTask(command)
        return self.task

    def get(self, task_id):
        return self.task if self.task and self.task.id == task_id else None

    def current(self):
        return self.task if self.task and self.task.running else None

    def send(self, task_id, text="\n"):
        if self.get(task_id) is None:
            raise TaskError("No such task")
        self.sent.append(task_id)
        self.sent_text.append(text)

    def stop(self, task_id):
        if self.get(task_id) is None:
            raise TaskError("No such task")
        self.stopped.append(task_id)


@pytest.fixture
def config(tmp_path):
    return Config(
        db_path=str(tmp_path / "jobs.db"),
        path=str(tmp_path / "config.yaml"),
        backend="claude-cli",
        sources={"greenhouse": ["acme"]},
    )


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def client(config, runner):
    app = web.create_app(config, runner=runner, token=TOKEN)
    app.config["TESTING"] = True
    return app.test_client()


def seed(config, status=STATUS_APPROVED, **kw):
    conn = db.connect(config.db_path)
    job = Job(source="greenhouse", source_id="1", company="Acme",
              title="Radar Engineer", url="https://example.com/j/1", **kw)
    db.upsert_job(conn, job)
    db.save_score(conn, job.fingerprint(), 82, "strong overlap", ["on-site"])
    if status != STATUS_SCORED:
        db.set_status(conn, job.fingerprint(), status)
    conn.commit()
    conn.close()
    return job.fingerprint()


# -- pages ---------------------------------------------------------------


def test_dashboard_renders(client, config):
    seed(config)
    page = client.get("/").get_data(as_text=True)
    assert "Dashboard" in page
    assert "Ready to apply" in page
    assert "claude-cli" in page          # the backend it will actually use
    assert "Radar Engineer" in page      # approved jobs are in the initial state


def test_dashboard_states_that_it_cannot_submit(client):
    assert "cannot submit" in client.get("/").get_data(as_text=True)


def test_review_still_works(client, config):
    seed(config, status=STATUS_SCORED)
    page = client.get("/review").get_data(as_text=True)
    assert "Radar Engineer" in page
    assert "strong overlap" in page


def test_review_ignores_an_unknown_status(client):
    assert client.get("/review?status=../secrets").status_code == 200


def test_decide_updates_and_redirects(client, config):
    fp = seed(config, status=STATUS_SCORED)
    resp = client.post(f"/decide/{fp}", data={"action": "approve", "from": "scored"})
    assert resp.status_code == 302
    assert "/review" in resp.headers["Location"]
    conn = db.connect(config.db_path)
    assert db.get(conn, fp)["status"] == STATUS_APPROVED


def test_decide_from_the_dashboard_returns_there(client, config):
    fp = seed(config)
    resp = client.post(f"/decide/{fp}", data={"action": "applied", "from": "dashboard"})
    assert resp.headers["Location"].endswith("/")


# -- api -----------------------------------------------------------------


def test_state_reports_stats_and_approved_jobs(client, config):
    seed(config)
    body = client.get("/api/state").get_json()
    assert body["stats"]["approved"] == 1
    assert body["approved"][0]["title"] == "Radar Engineer"
    assert body["current"] is None


def test_run_starts_the_command(client, runner):
    body = client.post("/api/run", json={"command": "ingest"}).get_json()
    assert runner.calls == [("ingest", None)]
    assert body["task"]["id"] == "task123"


def test_run_passes_options_through(client, runner):
    client.post("/api/run", json={"command": "score", "options": {"limit": 5}})
    assert runner.calls == [("score", {"limit": 5})]


def test_run_reports_a_refusal_without_a_traceback(client, runner):
    runner.fail = "Ingest is still running."
    resp = client.post("/api/run", json={"command": "ingest"})
    assert resp.status_code == 409
    assert "still running" in resp.get_json()["error"]


def test_api_needs_a_json_body(client):
    """A plain form post is what a cross-origin page can send; JSON is not."""
    assert client.post("/api/run", data={"command": "ingest"}).status_code == 415


def test_api_refuses_another_origin(client, runner):
    resp = client.post(
        "/api/run", json={"command": "ingest"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert runner.calls == []


def test_api_accepts_its_own_origin(client, runner):
    resp = client.post(
        "/api/run", json={"command": "ingest"},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200


def test_task_output_is_polled_by_cursor(client, runner):
    client.post("/api/run", json={"command": "ingest"})
    body = client.get("/api/task/task123?cursor=6").get_json()
    assert body["task"]["output"] == ""
    assert body["task"]["cursor"] == 6


def test_unknown_task_is_a_404(client):
    assert client.get("/api/task/nope").status_code == 404


def test_send_reaches_the_runner(client, runner):
    client.post("/api/run", json={"command": "apply"})
    assert client.post("/api/task/task123/send", json={}).status_code == 200
    assert runner.sent == ["task123"]


def test_fill_is_an_instruction_the_page_can_send(client, runner):
    client.post("/api/run", json={"command": "apply"})
    resp = client.post("/api/task/task123/send", json={"text": "fill"})
    assert resp.status_code == 200
    assert runner.sent_text == ["fill\n"]


def test_arbitrary_text_never_reaches_a_process(client, runner):
    client.post("/api/run", json={"command": "apply"})
    resp = client.post("/api/task/task123/send", json={"text": "rm -rf /\n"})
    assert resp.status_code == 400
    assert runner.sent == []


def test_stop_reaches_the_runner(client, runner):
    client.post("/api/run", json={"command": "ingest"})
    assert client.post("/api/task/task123/stop", json={}).status_code == 200
    assert runner.stopped == ["task123"]


# -- the count and the page it links to must agree ------------------------


def scored(config, n, score):
    conn = db.connect(config.db_path)
    for i in range(n):
        job = Job(source="adzuna", source_id=f"x{i}-{score}", company=f"Co{i}",
                  title=f"Engineer {i} {score}", url="https://e.example")
        db.upsert_job(conn, job)
        db.save_score(conn, job.fingerprint(), score, "reason")
    conn.commit()
    conn.close()


def test_to_review_counts_what_the_queue_will_show(client, config):
    scored(config, 3, 80)
    scored(config, 7, 20)
    stats = client.get("/api/state").get_json()["stats"]
    assert stats["scored"] == 10      # every scored job
    assert stats["to_review"] == 3    # what /review actually lists
    assert stats["below"] == 7


def test_the_review_queue_matches_the_count_it_advertises(client, config):
    scored(config, 2, 80)
    scored(config, 5, 20)
    page = client.get("/review?status=scored").get_data(as_text=True)
    assert page.count('class="card"') == 2


def test_below_threshold_jobs_are_reachable(client, config):
    scored(config, 5, 20)
    page = client.get("/review?status=below").get_data(as_text=True)
    assert page.count('class="card"') == 5


def test_an_empty_queue_says_where_the_hidden_jobs_went(client, config):
    scored(config, 4, 20)
    page = client.get("/review?status=scored").get_data(as_text=True)
    assert "4 scored job(s) came in under 60" in page
    assert "min_score" in page


def test_an_empty_queue_with_nothing_hidden_says_so_instead(client):
    page = client.get("/review?status=scored").get_data(as_text=True)
    assert "came in under" not in page
    assert "Ingest" in page


# -- finding the files by hand -------------------------------------------


def tailored(config, tmp_path):
    """An approved job with a folder of documents on disk."""
    fp = seed(config)
    folder = tmp_path / "applications" / "acme-radar"
    folder.mkdir(parents=True)
    (folder / "resume.pdf").write_bytes(b"%PDF-1.4")
    (folder / "resume.de.pdf").write_bytes(b"%PDF-1.4")
    conn = db.connect(config.db_path)
    db.mark_tailored(conn, fp, str(folder))
    conn.commit()
    conn.close()
    return fp, folder


def test_state_gives_an_absolute_folder_path(client, config, tmp_path):
    """Relative paths are useless in a file-upload dialog."""
    fp, folder = tailored(config, tmp_path)
    job = next(j for j in client.get("/api/state").get_json()["approved"]
               if j["fingerprint"] == fp)
    assert job["folder"] == str(folder.resolve())


def test_both_language_resumes_are_listed(client, config, tmp_path):
    fp, _ = tailored(config, tmp_path)
    job = next(j for j in client.get("/api/state").get_json()["approved"]
               if j["fingerprint"] == fp)
    assert {"resume.pdf", "resume.de.pdf"} <= set(job["documents"])


def test_german_documents_are_servable(client, config, tmp_path):
    fp, _ = tailored(config, tmp_path)
    assert client.get(f"/doc/{fp}/resume.de.pdf").status_code == 200


def test_reveal_opens_the_folder(client, config, tmp_path, monkeypatch):
    fp, folder = tailored(config, tmp_path)
    opened = []
    monkeypatch.setattr(web, "open_folder", lambda p: opened.append(str(p)))
    resp = client.post(f"/api/reveal/{fp}", json={})
    assert resp.status_code == 200
    assert opened == [str(folder)]


def test_reveal_refuses_a_job_with_nothing_tailored(client, config):
    fp = seed(config)
    assert client.post(f"/api/reveal/{fp}", json={}).status_code == 404


def test_reveal_is_covered_by_the_origin_guard(client, config, tmp_path, monkeypatch):
    """It opens a window on the user's desktop; a stray tab must not."""
    fp, _ = tailored(config, tmp_path)
    opened = []
    monkeypatch.setattr(web, "open_folder", lambda p: opened.append(str(p)))
    resp = client.post(
        f"/api/reveal/{fp}", json={}, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
    assert opened == []


def test_tailor_accepts_a_language_from_the_page(client, runner):
    client.post("/api/run", json={
        "command": "tailor", "options": {"language": "de"},
    })
    assert runner.calls == [("tailor", {"language": "de"})]


# -- deciding without losing the page -------------------------------------


def test_decide_api_records_the_decision(client, config):
    fp = seed(config)
    body = client.post(f"/api/decide/{fp}", json={"action": "applied"}).get_json()
    assert body["ok"] is True
    conn = db.connect(config.db_path)
    assert db.get(conn, fp)["status"] == "applied"


def test_decide_api_returns_the_new_state_so_no_reload_is_needed(client, config):
    """A reload would reset the command panel — and the tailoring language."""
    fp = seed(config)
    body = client.post(f"/api/decide/{fp}", json={"action": "applied"}).get_json()
    assert body["stats"]["applied"] == 1
    assert body["approved"] == []


def test_decide_api_rejects_an_unknown_action(client, config):
    fp = seed(config)
    resp = client.post(f"/api/decide/{fp}", json={"action": "delete"})
    assert resp.status_code == 400


def test_decide_api_is_covered_by_the_origin_guard(client, config):
    fp = seed(config)
    resp = client.post(
        f"/api/decide/{fp}", json={"action": "applied"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    conn = db.connect(config.db_path)
    assert db.get(conn, fp)["status"] == STATUS_APPROVED


def test_the_review_page_still_uses_the_redirecting_form(client, config):
    """Only the dashboard needs the async path; the review page can reload."""
    fp = seed(config, status=STATUS_SCORED)
    resp = client.post(f"/decide/{fp}", data={"action": "approve", "from": "scored"})
    assert resp.status_code == 302


# -- the settings page ----------------------------------------------------


@pytest.fixture
def editable(tmp_path, runner):
    """A client whose config is a real file the page can write to."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "min_score: 60\n"
        "sources:\n"
        "  arbeitsagentur:\n"
        "    where: Chemnitz\n"
        "    queries: [Softwareentwickler]\n"
        "filters:\n"
        "  title_exclude: [senior]\n"
        f"db_path: {(tmp_path / 'jobs.db').as_posix()}\n"
        f"profile_path: {(tmp_path / 'profile.md').as_posix()}\n",
        encoding="utf-8",
    )
    (tmp_path / "profile.md").write_text("Background.", encoding="utf-8")
    from jobpipe.config import load_config

    app = web.create_app(load_config(path), runner=runner, token=TOKEN)
    app.config["TESTING"] = True
    return app.test_client(), path


def test_settings_page_renders_every_group(editable):
    client, _ = editable
    page = client.get("/settings").get_data(as_text=True)
    assert "Bundesagentur" in page
    assert "Minimum score" in page
    assert "Reject titles containing" in page


def test_settings_page_does_not_render_credentials(editable):
    """Rendering an API key into the page publishes it."""
    client, _ = editable
    page = client.get("/settings").get_data(as_text=True)
    assert "app_key" not in page
    assert "app_id" not in page


def test_saving_writes_the_file(editable):
    client, path = editable
    resp = client.post("/api/settings", json={"values": {"min_score": 42}})
    body = resp.get_json()
    assert body["changed"] == ["min_score"]
    assert body["restart_required"] is True
    assert "min_score: 42" in path.read_text(encoding="utf-8")


def test_saving_a_bad_value_is_a_400_and_changes_nothing(editable):
    client, path = editable
    before = path.read_text(encoding="utf-8")
    resp = client.post("/api/settings", json={"values": {"min_score": 500}})
    assert resp.status_code == 400
    assert "at most" in resp.get_json()["error"]
    assert path.read_text(encoding="utf-8") == before


def test_saving_is_covered_by_the_origin_guard(editable):
    client, path = editable
    before = path.read_text(encoding="utf-8")
    resp = client.post(
        "/api/settings", json={"values": {"min_score": 10}},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert path.read_text(encoding="utf-8") == before


def test_probe_reports_a_live_count(client, monkeypatch):
    from jobpipe.sources import arbeitsagentur

    monkeypatch.setattr(
        arbeitsagentur, "search", lambda *a, **k: ([], 253)
    )
    body = client.post("/api/probe", json={"query": "Softwareentwickler"}).get_json()
    assert body == {"query": "Softwareentwickler", "count": 253}


def test_probe_needs_a_term(client):
    assert client.post("/api/probe", json={"query": "  "}).status_code == 400


def test_probe_reports_a_source_failure_without_a_traceback(client, monkeypatch):
    from jobpipe.sources import arbeitsagentur
    from jobpipe.sources.base import SourceError

    def explode(*a, **k):
        raise SourceError("HTTP 429")

    monkeypatch.setattr(arbeitsagentur, "search", explode)
    resp = client.post("/api/probe", json={"query": "x"})
    assert resp.status_code == 502
    assert "429" in resp.get_json()["error"]
