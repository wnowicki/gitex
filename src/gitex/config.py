"""Strict repository configuration; no writes or Git subprocesses."""

import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Mapping

from gitex.errors import ConfigError


@dataclass(frozen=True)
class BranchConfig:
    main: str = "main"
    develop: str = "develop"
    feature_prefix: str = "feature/"
    bugfix_prefix: str = "bugfix/"
    release_prefix: str = "release/"
    hotfix_prefix: str = "hotfix/"


@dataclass(frozen=True)
class WorkflowConfig:
    push_on_sync: bool = True
    finish_method: str = "pr"


@dataclass(frozen=True)
class ReleaseConfig:
    changelog: str = "CHANGELOG.md"
    tag_prefix: str = "v"


@dataclass(frozen=True)
class Config:
    branches: BranchConfig = BranchConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    release: ReleaseConfig = ReleaseConfig()


def valid_ref_name(name: str) -> bool:
    """Check Git ref syntax for a relative branch/tag name, without invoking Git."""
    return bool(name) and not (
        name.startswith(("-", "/"))
        or name.endswith(("/", "."))
        or ".." in name
        or "@{" in name
        or name == "@"
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", name)
        or any(
            not part or part.startswith(".") or part.endswith(".lock") for part in name.split("/")
        )
    )


def load_config(root: Path, overrides: Mapping[str, Mapping[str, object]] | None = None) -> Config:
    """Load .gitex.toml; explicit non-None overrides win over file and defaults.

    Overrides use the same section/key structure as TOML. Invalid file values
    are rejected even when overridden; typos must never be silently repaired.
    """
    path = root / ".gitex.toml"
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read {path}; fix the configuration: {exc}") from exc

    return _parse(overrides or {}, _parse(data, Config()))


def _parse(raw: Mapping[str, object], base: Config) -> Config:
    types = {"branches": BranchConfig, "workflow": WorkflowConfig, "release": ReleaseConfig}
    if raw.keys() - types.keys():
        raise ConfigError("Unknown configuration section; use branches, workflow, or release.")
    sections = {}
    for section, cls in types.items():
        values = raw.get(section, {})
        if not isinstance(values, Mapping):
            raise ConfigError(f"{section} must be a table.")
        defaults = getattr(base, section)
        allowed = {field.name for field in fields(cls)}
        if values.keys() - allowed:
            raise ConfigError(f"Unknown key in {section}; check the documented configuration.")
        merged = {key: getattr(defaults, key) for key in allowed}
        for key, value in values.items():
            if value is None:
                continue
            if type(value) is not type(merged[key]):
                raise ConfigError(f"Invalid type for {section}.{key}.")
            merged[key] = value
        sections[section] = cls(**merged)
    config = Config(**sections)
    _validate(config)
    return config


def _validate(config: Config) -> None:
    branches = config.branches
    bases = (branches.main, branches.develop)
    prefixes = tuple(
        getattr(branches, f"{kind}_prefix") for kind in ("feature", "bugfix", "release", "hotfix")
    )
    if any(not valid_ref_name(name) or name == "HEAD" for name in bases):
        raise ConfigError("Invalid base branch name.")
    if bases[0] == bases[1]:
        raise ConfigError("Main and develop must be distinct.")
    if any(not p.endswith("/") or not valid_ref_name(p + "topic") for p in prefixes):
        raise ConfigError("Branch prefixes must be valid names ending in '/'.")
    if any(
        a.startswith(b) for i, a in enumerate(prefixes) for j, b in enumerate(prefixes) if i != j
    ):
        raise ConfigError("Branch prefixes must not overlap.")
    if any(base.startswith(p) or base == p[:-1] for base in bases for p in prefixes):
        raise ConfigError("Base branches must not overlap topic prefixes.")
    if config.workflow.finish_method not in ("pr", "local-merge"):
        raise ConfigError("workflow.finish_method must be pr or local-merge.")
    if config.release.changelog != "CHANGELOG.md":
        raise ConfigError("Only CHANGELOG.md is supported for automatic release edits in v1.")
    if not valid_ref_name(config.release.tag_prefix + "1.2.3"):
        raise ConfigError("Invalid release tag prefix.")
