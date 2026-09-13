import pytest

from jobpipe import db
from jobpipe.models import STATUS_APPROVED, STATUS_NEW, STATUS_REJECTED, Job


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def make(**kw):
    base = dict(source="greenhouse", source_id="1", company="Acme", title="Engineer", url="u")
    base.update(kw)
    return Job(**base)


def test_insert_then_reinsert_is_idempotent(conn):
    job = make()
    assert db.upsert_job(conn, job) == "inserted"
    assert db.upsert_job(conn, job) == "seen"
    assert db.stats(conn)["new"] == 1


def test_reingest_does_not_resurrect_a_rejected_job(conn):
    job = make()
    db.upsert_job(conn, job)
    db.set_status(conn, job.fingerprint(), STATUS_REJECTED)
    db.upsert_job(conn, job)
    assert db.get(conn, job.fingerprint())["status"] == STATUS_REJECTED


def test_reingest_preserves_an_existing_score(conn):
    job = make()
    db.upsert_job(conn, job)
    db.save_score(conn, job.fingerprint(), 88, "good fit", ["k8s"])
    db.upsert_job(conn, job)
    row = db.get(conn, job.fingerprint())
    assert row["score"] == 88
    assert row["score_reason"] == "good fit"


def test_review_queue_respects_min_score_and_orders_by_score(conn):
    for i, score in enumerate([40, 95, 70]):
        job = make(source_id=str(i), title=f"Engineer {i}")
        db.upsert_job(conn, job)
        db.save_score(conn, job.fingerprint(), score, "r")
    queue = db.review_queue(conn, min_score=60)
    assert [r["score"] for r in queue] == [95, 70]


def test_filtered_jobs_leave_the_queue(conn):
    job = make()
    db.upsert_job(conn, job)
    db.reject_by_filter(conn, job.fingerprint(), "not remote")
    row = db.get(conn, job.fingerprint())
    assert row["status"] == STATUS_REJECTED
    assert row["filter_reason"] == "not remote"


def test_set_status_rejects_unknown_status(conn):
    job = make()
    db.upsert_job(conn, job)
    with pytest.raises(ValueError):
        db.set_status(conn, job.fingerprint(), "maybe")


def test_stats_reports_every_status(conn):
    assert set(db.stats(conn)) == {"new", "scored", "approved", "rejected", "applied"}


def test_unscored_only_returns_new(conn):
    a, b = make(source_id="1", title="A"), make(source_id="2", title="B")
    db.upsert_job(conn, a)
    db.upsert_job(conn, b)
    db.set_status(conn, b.fingerprint(), STATUS_APPROVED)
    assert [r["title"] for r in db.unscored(conn)] == ["A"]


def test_below_threshold_finds_what_the_queue_hides(conn):
    """Scored-but-under-cut jobs are undecided, not rejected."""
    for i, score in enumerate([90, 61, 60, 59, 5]):
        job = make(source_id=str(i), title=f"Engineer {i}")
        db.upsert_job(conn, job)
        db.save_score(conn, job.fingerprint(), score, "r")

    ready = db.review_queue(conn, min_score=60)
    below = db.below_threshold(conn, min_score=60)
    assert [r["score"] for r in ready] == [90, 61, 60]
    assert [r["score"] for r in below] == [59, 5]


def test_the_two_views_partition_the_scored_jobs(conn):
    for i, score in enumerate([80, 10, 44, 99]):
        job = make(source_id=str(i), title=f"Engineer {i}")
        db.upsert_job(conn, job)
        db.save_score(conn, job.fingerprint(), score, "r")
    total = db.stats(conn)["scored"]
    assert len(db.review_queue(conn, 60)) + len(db.below_threshold(conn, 60)) == total


def test_below_threshold_counts_an_unscored_row_as_below(conn):
    """COALESCE(score, 0): a null score must land somewhere, not vanish."""
    job = make()
    db.upsert_job(conn, job)
    db.save_score(conn, job.fingerprint(), 0, "r")
    conn.execute("UPDATE jobs SET score = NULL WHERE fingerprint = ?", (job.fingerprint(),))
    assert len(db.below_threshold(conn, 60)) == 1


def test_rescoring_can_leave_an_approval_alone(conn):
    """A job approved before it was ever scored is the reason this exists."""
    job = make()
    db.upsert_job(conn, job)
    db.set_status(conn, job.fingerprint(), STATUS_APPROVED)
    db.save_score(conn, job.fingerprint(), 77, "good", set_status=False)
    row = db.get(conn, job.fingerprint())
    assert row["status"] == STATUS_APPROVED
    assert row["score"] == 77


def test_scoring_normally_still_moves_a_job_into_the_queue(conn):
    job = make()
    db.upsert_job(conn, job)
    db.save_score(conn, job.fingerprint(), 77, "good")
    assert db.get(conn, job.fingerprint())["status"] == "scored"


def test_rescorable_covers_the_live_pipeline_only(conn):
    """Rejected jobs are not revisited: a filter rejection was never an LLM
    judgement, and a rejection you made yourself is not for this to undo."""
    wanted = []
    for i, status in enumerate(["scored", "approved", "rejected", "applied", "new"]):
        job = make(source_id=str(i), title=f"Engineer {i}")
        db.upsert_job(conn, job)
        if status != "new":
            db.set_status(conn, job.fingerprint(), status)
        if status in ("scored", "approved"):
            wanted.append(job.fingerprint())

    got = {r["fingerprint"] for r in db.rescorable(conn)}
    assert got == set(wanted)
