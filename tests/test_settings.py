"""Editing config.yaml from the settings page.

The file is hand-maintained and every command depends on it, so the tests
that matter here are the ones about not wrecking it: comments survive, a bad
edit is refused before the file is touched, and the previous version is kept.
"""

from __future__ import annotations

import pytest

from jobpipe import settings
from jobpipe.settings import SettingsError

CONFIG = """\
# Personal config. Gitignored.
backend: claude-cli
min_score: 60

sources:
  # SmartRecruiters has the best European coverage.
  smartrecruiters:
    - ContinentalAG

  arbeitsagentur:
    where: "Chemnitz"
    umkreis: 100            # km — this is the big one
    exclude_staffing: true  # drops Zeitarbeit
    queries:
      - "Softwareentwickler"
      - "Python"

filters:
  title_exclude:
    - senior
  max_age_days: 45

db_path: jobs.db
profile_path: profile.md
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    (tmp_path / "profile.md").write_text("Background.", encoding="utf-8")
    return path


# -- reading ---------------------------------------------------------------


def test_read_returns_every_editable_value(config):
    values = settings.read(config)
    assert values["min_score"] == 60
    assert values["sources.arbeitsagentur.queries"] == ["Softwareentwickler", "Python"]
    assert values["sources.arbeitsagentur.where"] == "Chemnitz"
    assert values["sources.arbeitsagentur.exclude_staffing"] is True
    assert values["filters.title_exclude"] == ["senior"]


def test_absent_settings_read_as_empty_not_missing(config):
    values = settings.read(config)
    assert values["sources.adzuna.queries"] == []
    assert values["filters.min_salary"] is None
    assert values["filters.remote_only"] is False


def test_credentials_are_not_editable_and_never_read_out():
    """A page that renders an API key has published it."""
    keys = set(settings.BY_KEY)
    assert not any("app_id" in k or "app_key" in k for k in keys)


def test_setup_is_not_editable_from_the_page():
    """Backends and paths stay a deliberate edit to the file."""
    keys = set(settings.BY_KEY)
    for locked in ["backend", "db_path", "profile_path", "resume_path", "cli_model"]:
        assert locked not in keys


# -- writing ---------------------------------------------------------------


def test_a_changed_value_is_written(config):
    changed = settings.write(config, {"min_score": 45})
    assert changed == ["min_score"]
    assert settings.read(config)["min_score"] == 45


def test_comments_survive_an_edit(config):
    settings.write(config, {"sources.arbeitsagentur.queries": ["HiL", "ECU"]})
    text = config.read_text(encoding="utf-8")
    assert "# Personal config. Gitignored." in text
    assert "# km — this is the big one" in text
    assert "# drops Zeitarbeit" in text


def test_the_new_terms_are_in_the_file(config):
    settings.write(config, {"sources.arbeitsagentur.queries": ["HiL", "ECU"]})
    text = config.read_text(encoding="utf-8")
    assert "HiL" in text and "ECU" in text
    assert "Softwareentwickler" not in text


def test_writing_the_same_values_changes_nothing(config):
    before = config.read_text(encoding="utf-8")
    assert settings.write(config, {"min_score": 60}) == []
    assert config.read_text(encoding="utf-8") == before


def test_the_previous_version_is_kept(config):
    settings.write(config, {"min_score": 30})
    backup = config.with_suffix(".yaml.bak")
    assert backup.is_file()
    assert "min_score: 60" in backup.read_text(encoding="utf-8")


def test_untouched_settings_are_left_alone(config):
    settings.write(config, {"min_score": 30})
    assert settings.read(config)["sources.smartrecruiters"] == ["ContinentalAG"]
    assert "backend: claude-cli" in config.read_text(encoding="utf-8")


def test_blank_entries_in_a_list_are_dropped(config):
    settings.write(config, {"filters.title_exclude": ["senior", "  ", "", "lead"]})
    assert settings.read(config)["filters.title_exclude"] == ["senior", "lead"]


def test_clearing_an_optional_number_removes_it(config):
    settings.write(config, {"filters.max_age_days": None})
    assert settings.read(config)["filters.max_age_days"] is None
    assert "max_age_days" not in config.read_text(encoding="utf-8")


# -- refusing a bad edit ---------------------------------------------------


def test_an_unknown_setting_is_refused(config):
    with pytest.raises(SettingsError, match="Not a setting"):
        settings.write(config, {"db_path": "/somewhere/else.db"})


def test_a_non_numeric_number_is_refused(config):
    with pytest.raises(SettingsError, match="whole number"):
        settings.write(config, {"min_score": "sixty"})


def test_a_number_out_of_range_is_refused(config):
    with pytest.raises(SettingsError, match="at most 100"):
        settings.write(config, {"min_score": 900})


def test_a_refused_edit_leaves_the_file_untouched(config):
    before = config.read_text(encoding="utf-8")
    for bad in [{"min_score": "x"}, {"nope": 1}, {"filters.title_exclude": 5}]:
        with pytest.raises(SettingsError):
            settings.write(config, bad)
    assert config.read_text(encoding="utf-8") == before


def test_an_edit_that_would_not_load_is_refused_before_the_swap(config, monkeypatch):
    """The result is loaded before it replaces the file — writing a config
    that cannot be read would break the command that could fix it."""
    from jobpipe.config import ConfigError

    def explode(path):
        raise ConfigError("unknown filter key")

    monkeypatch.setattr(settings, "load_config", explode)
    before = config.read_text(encoding="utf-8")
    with pytest.raises(SettingsError, match="would not load"):
        settings.write(config, {"min_score": 42})
    assert config.read_text(encoding="utf-8") == before
    assert not config.with_suffix(".yaml.new").exists()


def test_the_result_still_loads_as_a_config(config):
    from jobpipe.config import load_config

    settings.write(config, {
        "min_score": 55,
        "sources.arbeitsagentur.queries": ["HiL", "AUTOSAR"],
        "filters.title_exclude": ["senior", "lead"],
    })
    loaded = load_config(config)
    assert loaded.min_score == 55
    assert loaded.arbeitsagentur["queries"] == ["HiL", "AUTOSAR"]
    assert loaded.filters.title_exclude == ["senior", "lead"]


def test_a_setting_can_be_added_to_a_source_that_had_none(config):
    settings.write(config, {"sources.germantechjobs.keywords": ["embedded"]})
    from jobpipe.config import load_config
    assert load_config(config).germantechjobs["keywords"] == ["embedded"]


# -- what the page renders -------------------------------------------------


def test_every_setting_belongs_to_a_rendered_group():
    rendered = {s.key for _, group in settings.grouped() for s in group}
    assert rendered == set(settings.BY_KEY)


def test_groups_come_back_in_display_order():
    assert [name for name, _ in settings.grouped()][0] == "Review"
