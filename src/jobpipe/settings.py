"""Reading and writing the tunable parts of config.yaml.

Only the parts worth tuning from a page: what to search for, what to throw
away, and the score cut-off. Setup — backends, file paths, API credentials —
stays in the file, where changing it is a deliberate act.

Credentials are never read out to the browser. A page that renders an API
key into its own HTML has published it to anything that can see the page.

`config.yaml` is hand-maintained and its comments are half its
documentation ("Adzuna requires every word in a query to match, so keep them
short"). Rewriting it with a plain YAML dump would throw all of that away, so
edits round-trip through ruamel and the previous version is kept alongside.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML, YAMLError

from .config import ConfigError, load_config


@dataclass(frozen=True)
class Setting:
    """One editable value, and enough about it for a page to render it."""

    path: tuple[str, ...]
    label: str
    kind: str          # "list" | "text" | "int" | "bool"
    group: str
    help: str = ""
    minimum: int | None = None
    maximum: int | None = None
    placeholder: str = ""

    @property
    def key(self) -> str:
        return ".".join(self.path)


SETTINGS: list[Setting] = [
    # -- what reaches the review queue ---------------------------------
    Setting(
        ("min_score",), "Minimum score", "int", "Review",
        "Jobs below this are scored but kept out of the review queue. "
        "They are not rejected — they wait in the Below tab.",
        minimum=0, maximum=100,
    ),

    # -- the primary search --------------------------------------------
    Setting(
        ("sources", "arbeitsagentur", "queries"), "Search terms", "list",
        "Bundesagentur für Arbeit",
        "One search per term. Probe them: a term returning thousands is too "
        "broad and will fill the queue with roles you do not want.",
    ),
    Setting(
        ("sources", "arbeitsagentur", "where"), "Near", "text",
        "Bundesagentur für Arbeit", placeholder="Chemnitz",
    ),
    Setting(
        ("sources", "arbeitsagentur", "umkreis"), "Radius (km)", "int",
        "Bundesagentur für Arbeit", minimum=0, maximum=200,
    ),
    Setting(
        ("sources", "arbeitsagentur", "max_days_old"), "Posted within (days)", "int",
        "Bundesagentur für Arbeit", minimum=0, maximum=100,
    ),
    Setting(
        ("sources", "arbeitsagentur", "exclude_staffing"),
        "Exclude staffing agencies", "bool", "Bundesagentur für Arbeit",
        "Drops Zeitarbeit and private recruiters at the source — the same "
        "role relisted by six agencies, none of whom are hiring.",
    ),

    # -- the backstop ---------------------------------------------------
    Setting(
        ("sources", "adzuna", "queries"), "Search terms", "list", "Adzuna",
        "Every word in a term must match, so keep them short. Broad single "
        "words are what filled the queue with unrelated roles.",
    ),
    Setting(("sources", "adzuna", "where"), "Near", "text", "Adzuna"),
    Setting(
        ("sources", "adzuna", "distance"), "Radius (km)", "int", "Adzuna",
        minimum=0, maximum=200,
    ),

    # -- boards fetched whole, then filtered ----------------------------
    Setting(
        ("sources", "arbeitnow", "keywords"), "Keep titles matching", "list",
        "Arbeitnow",
        "This board is fetched whole and filtered here, so a loose list "
        "lets in everything it carries.",
    ),
    Setting(
        ("sources", "arbeitnow", "location_contains"), "Keep locations matching",
        "list", "Arbeitnow",
    ),
    Setting(
        ("sources", "germantechjobs", "keywords"), "Keep titles matching", "list",
        "GermanTechJobs",
        "All IT already, so filter on what you actually do rather than on "
        "'software' or 'engineer'.",
    ),

    # -- per-company ATS feeds -------------------------------------------
    Setting(("sources", "greenhouse"), "Greenhouse board tokens", "list", "Watchlist"),
    Setting(("sources", "lever"), "Lever slugs", "list", "Watchlist"),
    Setting(("sources", "ashby"), "Ashby slugs", "list", "Watchlist"),
    Setting(
        ("sources", "smartrecruiters"), "SmartRecruiters slugs", "list", "Watchlist",
        "Best European coverage of the free ATS feeds.",
    ),

    # -- hard filters, applied before any model call ---------------------
    Setting(
        ("filters", "title_exclude"), "Reject titles containing", "list", "Filters",
        "Free and applied before anything is scored.",
    ),
    Setting(
        ("filters", "title_include"), "Require titles to contain", "list", "Filters",
        "Leave empty to allow any title.",
    ),
    Setting(
        ("filters", "locations"), "Allowed locations", "list", "Filters",
        "Leave empty to allow anywhere. GermanTechJobs publishes no location, "
        "so setting this rejects all of it.",
    ),
    Setting(
        ("filters", "max_age_days"), "Reject older than (days)", "int", "Filters",
        minimum=0, maximum=365,
    ),
    Setting(
        ("filters", "min_salary"), "Salary floor", "int", "Filters",
        "Only judged when a posting states a salary — most do not.",
        minimum=0, maximum=1_000_000,
    ),
    Setting(("filters", "remote_only"), "Remote only", "bool", "Filters"),
]

BY_KEY = {s.key: s for s in SETTINGS}

GROUP_ORDER = [
    "Review", "Bundesagentur für Arbeit", "Adzuna", "Arbeitnow",
    "GermanTechJobs", "Watchlist", "Filters",
]


class SettingsError(RuntimeError):
    """The edit was refused. The file on disk is unchanged."""


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # do not re-wrap long lines that were fine as they were
    return y


def _dig(data, path: tuple[str, ...]):
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def read(config_path: str | Path) -> dict:
    """Current values for everything editable, keyed by dotted path."""
    path = Path(config_path)
    if not path.exists():
        raise SettingsError(f"No config at {path}")
    with path.open(encoding="utf-8") as handle:
        data = _yaml().load(handle) or {}

    out = {}
    for setting in SETTINGS:
        value = _dig(data, setting.path)
        if setting.kind == "list":
            out[setting.key] = [str(v) for v in (value or [])]
        elif setting.kind == "bool":
            out[setting.key] = bool(value)
        elif setting.kind == "int":
            out[setting.key] = value if isinstance(value, int) else None
        else:
            out[setting.key] = "" if value is None else str(value)
    return out


def _clean(setting: Setting, raw):
    """One submitted value, coerced and checked, or a SettingsError."""
    if setting.kind == "list":
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise SettingsError(f"{setting.label} must be a list")
        items = [str(v).strip() for v in raw]
        return [v for v in items if v]

    if setting.kind == "bool":
        return bool(raw)

    if setting.kind == "int":
        if raw in (None, ""):
            return None
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise SettingsError(f"{setting.label} must be a whole number") from None
        if setting.minimum is not None and number < setting.minimum:
            raise SettingsError(f"{setting.label} must be at least {setting.minimum}")
        if setting.maximum is not None and number > setting.maximum:
            raise SettingsError(f"{setting.label} must be at most {setting.maximum}")
        return number

    return str(raw or "").strip()


def _place(data, path: tuple[str, ...], value) -> None:
    """Set a value, creating the parents it needs."""
    node = data
    for part in path[:-1]:
        existing = node.get(part)
        if not isinstance(existing, dict):
            node[part] = {}
        node = node[part]
    leaf = path[-1]

    # An empty optional becomes an absent key rather than a null, so the
    # file keeps reading the way a hand-written one would.
    if value is None or (isinstance(value, list) and not value and leaf not in node):
        node.pop(leaf, None)
        return
    if isinstance(node.get(leaf), list) and isinstance(value, list):
        # Mutate in place: replacing the node drops comments attached to it.
        node[leaf].clear()
        node[leaf].extend(value)
        return
    node[leaf] = value


def write(config_path: str | Path, values: dict) -> list[str]:
    """Apply `values` to the config file. Returns the keys actually changed.

    Validated, backed up and written atomically, in that order: an edit that
    would not load is refused before the file is touched, and a crash
    mid-write cannot leave a half-written config behind.
    """
    path = Path(config_path)
    current = read(path)

    unknown = set(values) - set(BY_KEY)
    if unknown:
        raise SettingsError(f"Not a setting: {', '.join(sorted(unknown))}")

    with path.open(encoding="utf-8") as handle:
        data = _yaml().load(handle) or {}

    changed = []
    for key, raw in values.items():
        setting = BY_KEY[key]
        cleaned = _clean(setting, raw)
        if cleaned != current.get(key):
            changed.append(key)
        _place(data, setting.path, cleaned)

    if not changed:
        return []

    # Render first, then check it still loads, then swap it in. Writing an
    # unloadable config would break every command including the one that
    # could fix it.
    temp = path.with_suffix(path.suffix + ".new")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            _yaml().dump(data, handle)
        load_config(temp)
    except (YAMLError, ConfigError, OSError) as exc:
        temp.unlink(missing_ok=True)
        raise SettingsError(f"Refused — the result would not load: {exc}") from exc

    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    os.replace(temp, path)
    return changed


def grouped() -> list[tuple[str, list[Setting]]]:
    """Settings in display order, for a page to render."""
    return [
        (group, [s for s in SETTINGS if s.group == group])
        for group in GROUP_ORDER
        if any(s.group == group for s in SETTINGS)
    ]
