import pytest

from jobpipe import doctor
from jobpipe.config import Config


def statuses(checks):
    return {label: status for status, label, _ in checks}


def details(checks):
    return {label: detail for _, label, detail in checks}


# --- the silent-failure shapes -------------------------------------------

def test_adzuna_with_keys_but_no_queries_is_a_failure():
    cfg = Config(sources={"adzuna": {"app_id": "a", "app_key": "b", "queries": []}})
    checks = doctor.check_sources(cfg)
    assert statuses(checks)["adzuna"] == doctor.FAIL
    assert "queries" in details(checks)["adzuna"]


def test_adzuna_missing_credentials_is_a_failure():
    cfg = Config(sources={"adzuna": {"queries": ["x"]}})
    assert statuses(doctor.check_sources(cfg))["adzuna"] == doctor.FAIL


def test_adzuna_fully_configured_passes():
    cfg = Config(sources={"adzuna": {
        "app_id": "a", "app_key": "b", "country": "de",
        "where": "Chemnitz", "queries": ["engineer", "developer"],
    }})
    checks = doctor.check_sources(cfg)
    assert statuses(checks)["adzuna"] == doctor.OK
    assert "2 quer" in details(checks)["adzuna"] and "de" in details(checks)["adzuna"]


def test_source_block_at_top_level_is_caught():
    """The commonest YAML slip: one indent level too few, parses fine, does nothing."""
    cfg = Config(sources={}, raw={"adzuna": {"app_id": "a", "queries": ["x"]}})
    checks = doctor.check_sources(cfg)
    labels = [label for _, label, _ in checks]
    assert "`adzuna:` at top level" in labels
    assert any(s == doctor.FAIL for s, label, _ in checks if "top level" in label)


def test_no_sources_at_all_is_a_failure():
    checks = doctor.check_sources(Config())
    assert statuses(checks)["sources"] == doctor.FAIL


def test_configured_ats_source_passes():
    cfg = Config(sources={"smartrecruiters": ["ContinentalAG"]})
    checks = doctor.check_sources(cfg)
    assert statuses(checks)["smartrecruiters"] == doctor.OK
    assert "sources" not in statuses(checks)


# --- backend --------------------------------------------------------------

def test_api_backend_without_a_key_fails(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert doctor.check_backend(Config(backend="api"))[0][0] == doctor.FAIL


def test_api_backend_with_a_key_passes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert doctor.check_backend(Config(backend="api"))[0][0] == doctor.OK


def test_cli_backend_without_the_cli_fails(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    assert doctor.check_backend(Config(backend="claude-cli"))[0][0] == doctor.FAIL


def test_cli_backend_with_the_cli_passes(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/claude")
    status, label, detail = doctor.check_backend(Config(backend="claude-cli"))[0]
    assert status == doctor.OK and "sonnet" in detail


# --- files ----------------------------------------------------------------

def test_missing_files_name_the_command_they_break(tmp_path):
    cfg = Config(
        profile_path=str(tmp_path / "nope.md"),
        resume_path=str(tmp_path / "nope.json"),
        applicant_path=str(tmp_path / "nope.yaml"),
    )
    checks = doctor.check_files(cfg)
    assert all(s == doctor.WARN for s, _, _ in checks)
    assert "score" in details(checks)["profile.md"]
    assert "tailor" in details(checks)["resume.json"]
    assert "apply" in details(checks)["applicant.yaml"]


def test_run_returns_the_failure_count(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    failures = doctor.run(Config(backend="api"))
    assert failures >= 2          # no key, no sources
    assert "problem(s) to fix" in capsys.readouterr().out


def test_run_is_clean_when_everything_is_set(monkeypatch, tmp_path, capsys):
    for name in ("profile.md", "resume.json", "applicant.yaml"):
        (tmp_path / name).write_text("x")
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/claude")
    cfg = Config(
        backend="claude-cli",
        sources={"smartrecruiters": ["ContinentalAG"]},
        profile_path=str(tmp_path / "profile.md"),
        resume_path=str(tmp_path / "resume.json"),
        applicant_path=str(tmp_path / "applicant.yaml"),
    )
    assert doctor.run(cfg) == 0
    assert "No problems found" in capsys.readouterr().out


# --- commented-out blocks -------------------------------------------------

def test_commented_out_source_block_is_caught(tmp_path):
    """Filling in credentials but leaving the `#` gives a config that looks
    done and parses to nothing."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "sources:\n"
        "  smartrecruiters: [ContinentalAG]\n"
        "\n"
        "  # adzuna:\n"
        "  #   app_id: abc123\n"
        "  #   app_key: def456\n"
        "  #   queries: ['engineer']\n"
    )
    cfg = Config(sources={"smartrecruiters": ["ContinentalAG"]},
                 raw={"sources": {"smartrecruiters": ["ContinentalAG"]}},
                 path=str(cfg_file))
    checks = doctor.check_commented_out(cfg)
    assert len(checks) == 1
    status, label, detail = checks[0]
    assert status == doctor.FAIL
    assert "commented out" in label
    assert "remove the leading `#`" in detail


def test_active_block_is_not_reported_as_commented(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("sources:\n  adzuna:\n    app_id: a\n    queries: ['x']\n")
    cfg = Config(sources={"adzuna": {"app_id": "a", "queries": ["x"]}},
                 raw={"sources": {"adzuna": {"app_id": "a"}}}, path=str(cfg_file))
    assert doctor.check_commented_out(cfg) == []


def test_commented_check_survives_a_missing_file():
    assert doctor.check_commented_out(Config(path="/nonexistent/config.yaml")) == []
    assert doctor.check_commented_out(Config()) == []
