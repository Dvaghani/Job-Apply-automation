"""Which jobs a scoring run picks up, and what it leaves alone."""

from __future__ import annotations

import threading
import time

import pytest

from jobpipe import db, score
from jobpipe.models import STATUS_APPROVED, STATUS_SCORED, Job
from jobpipe.score import FitScore


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.db")


class Cfg:
    db_path = ""
    concurrency = 1
    backend = "api"
    model = "m"
    cli_model = "sonnet"
    cli_timeout = 300
    agy_model = "gemini-3.8-flash-high"
    agy_timeout = 600

    def load_profile(self):
        return "a profile"


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.seen = []

    def complete(self, system, prompt, model_cls, max_tokens=8000):
        self.seen.append(prompt)
        return FitScore(score=71, reason="fine", flags=[])


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(score.llm, "build", lambda config: fake)
    return fake


def seed(conn, n=3):
    fingerprints = []
    for i in range(n):
        job = Job(source="greenhouse", source_id=str(i), company="Acme",
                  title=f"Engineer {i}", url=f"u{i}")
        db.upsert_job(conn, job)
        fingerprints.append(job.fingerprint())
    return fingerprints


def test_a_plain_run_only_takes_unscored_jobs(conn, backend):
    fps = seed(conn)
    db.save_score(conn, fps[0], 50, "why", [])
    result = score.run(Cfg(), conn, limit=None)
    assert result["scored"] == 2


def test_rescore_takes_the_jobs_already_scored(conn, backend):
    fps = seed(conn)
    for fp in fps:
        db.save_score(conn, fp, 50, "why", [])
    db.set_status(conn, fps[0], STATUS_APPROVED)

    result = score.run(Cfg(), conn, rescore=True)
    assert result["scored"] == 3
    # an approval has to survive a rescore
    assert db.get(conn, fps[0])["status"] == STATUS_APPROVED
    assert db.get(conn, fps[1])["status"] == STATUS_SCORED
    assert db.get(conn, fps[0])["score"] == 71


def test_naming_a_job_beats_rescore(conn, backend):
    """The dashboard's per-job Score button sends both if the box is ticked."""
    fps = seed(conn)
    for fp in fps:
        db.save_score(conn, fp, 50, "why", [])

    result = score.run(Cfg(), conn, fingerprints=[fps[1]], rescore=True)
    assert result["scored"] == 1
    assert db.get(conn, fps[1])["score"] == 71
    assert db.get(conn, fps[0])["score"] == 50


def test_rescore_respects_a_limit(conn, backend):
    fps = seed(conn, n=4)
    for fp in fps:
        db.save_score(conn, fp, 50, "why", [])
    assert score.run(Cfg(), conn, limit=2, rescore=True)["scored"] == 2


# -- running several at once -----------------------------------------------


class SlowBackend:
    """Counts how many calls are in flight at the same time."""

    name = "slow"

    def __init__(self):
        self.lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.calls = 0

    def complete(self, system, prompt, model_cls, max_tokens=8000):
        with self.lock:
            self.live += 1
            self.calls += 1
            self.peak = max(self.peak, self.live)
        time.sleep(0.05)
        with self.lock:
            self.live -= 1
        return FitScore(score=60, reason="ok", flags=[])


def test_workers_run_at_the_same_time(conn, monkeypatch):
    fake = SlowBackend()
    monkeypatch.setattr(score.llm, "build", lambda config: fake)
    seed(conn, n=8)

    cfg = Cfg()
    cfg.concurrency = 4
    result = score.run(cfg, conn)

    assert result["scored"] == 8
    assert fake.calls == 8
    assert fake.peak > 1, "calls were serialised"


def test_one_worker_is_still_one_at_a_time(conn, monkeypatch):
    fake = SlowBackend()
    monkeypatch.setattr(score.llm, "build", lambda config: fake)
    seed(conn, n=4)

    cfg = Cfg()
    cfg.concurrency = 1
    assert score.run(cfg, conn)["scored"] == 4
    assert fake.peak == 1


def test_a_failure_in_one_worker_does_not_lose_the_others(conn, monkeypatch):
    from jobpipe.llm import LLMError

    class Flaky:
        name = "flaky"

        def __init__(self):
            self.lock = threading.Lock()
            self.n = 0

        def complete(self, system, prompt, model_cls, max_tokens=8000):
            with self.lock:
                self.n += 1
                mine = self.n
            if mine % 2 == 0:
                raise LLMError("backend said no")
            return FitScore(score=55, reason="ok", flags=[])

    monkeypatch.setattr(score.llm, "build", lambda config: Flaky())
    fps = seed(conn, n=6)

    cfg = Cfg()
    cfg.concurrency = 3
    result = score.run(cfg, conn)

    assert result["scored"] + result["failed"] == 6
    assert result["failed"] == 3
    # the ones that failed stay unscored, so the next run retries them
    unscored = {r["fingerprint"] for r in db.unscored(conn)}
    assert len(unscored) == 3
    assert unscored <= set(fps)
