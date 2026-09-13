from jobpipe import db, ingest
from jobpipe.config import Config, Filters
from jobpipe.models import Job


def make(**kw):
    base = dict(source="greenhouse", source_id="1", company="Acme", title="Senior Engineer", url="u")
    base.update(kw)
    return Job(**base)


def run_with(monkeypatch, tmp_path, jobs, filters=None):
    config = Config(filters=filters or Filters(), db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(ingest, "collect", lambda cfg, rep: jobs)
    conn = db.connect(config.db_path)
    return config, conn, ingest.run(config, conn)


def test_counts_new_and_duplicate(monkeypatch, tmp_path):
    jobs = [make(source_id="1"), make(source_id="2", title="Data Engineer")]
    config, conn, report = run_with(monkeypatch, tmp_path, jobs)
    assert (report.fetched, report.inserted, report.duplicates) == (2, 2, 0)

    # A second run over the same postings must add nothing.
    again = ingest.run(config, conn)
    assert (again.inserted, again.duplicates) == (0, 2)


def test_same_role_from_two_sources_collapses(monkeypatch, tmp_path):
    jobs = [
        make(source="greenhouse", source_id="1", title="Senior Engineer (Remote)"),
        make(source="lever", source_id="2", title="Senior Engineer"),
    ]
    _, _, report = run_with(monkeypatch, tmp_path, jobs)
    assert report.inserted == 1
    assert report.duplicates == 1


def test_filtered_jobs_are_stored_but_rejected(monkeypatch, tmp_path):
    jobs = [make(title="Engineering Intern"), make(source_id="2", title="Senior Engineer")]
    filters = Filters(title_exclude=["intern"])
    _, conn, report = run_with(monkeypatch, tmp_path, jobs, filters)
    assert report.inserted == 2
    assert report.filtered == 1
    # Filtered rows stay in the DB so re-ingest doesn't re-surface them.
    assert db.stats(conn)["rejected"] == 1
    assert db.stats(conn)["new"] == 1


def test_source_errors_are_collected_not_raised(monkeypatch, tmp_path):
    def boom(cfg, rep):
        rep.errors.append("greenhouse/acme: 404")
        return [make()]

    config = Config(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(ingest, "collect", boom)
    report = ingest.run(config, db.connect(config.db_path))
    assert report.errors and report.inserted == 1
