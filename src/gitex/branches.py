"""Pure GitFlow branch policy."""

from dataclasses import dataclass
from enum import StrEnum

from gitex.config import BranchConfig, valid_ref_name


class BranchType(StrEnum):
    MAIN = "main"
    DEVELOP = "develop"
    FEATURE = "feature"
    BUGFIX = "bugfix"
    RELEASE = "release"
    HOTFIX = "hotfix"
    UNKNOWN = "unknown"


class Strategy(StrEnum):
    REBASE = "rebase"
    MERGE = "merge"


@dataclass(frozen=True)
class BranchPolicy:
    kind: BranchType
    creation_source: str | None = None
    integration_source: str | None = None
    delivery_target: str | None = None
    strategy: Strategy | None = None


def branch_policy(name: str, config: BranchConfig | None = None) -> BranchPolicy:
    """Unknown/invalid branches have no implicit mutation policy."""
    config = config or BranchConfig()
    if not valid_ref_name(name):
        return BranchPolicy(BranchType.UNKNOWN)
    if name == config.main:
        return BranchPolicy(BranchType.MAIN)
    if name == config.develop:
        return BranchPolicy(BranchType.DEVELOP, config.main, config.main, None, Strategy.MERGE)
    for kind in (BranchType.FEATURE, BranchType.BUGFIX, BranchType.RELEASE, BranchType.HOTFIX):
        prefix = getattr(config, f"{kind.value}_prefix")
        if name.startswith(prefix) and len(name) > len(prefix):
            ordinary = kind in (BranchType.FEATURE, BranchType.BUGFIX)
            return BranchPolicy(
                kind,
                config.main if kind == BranchType.HOTFIX else config.develop,
                config.develop if ordinary else config.main,
                config.develop if ordinary else config.main,
                Strategy.REBASE if ordinary else Strategy.MERGE,
            )
    return BranchPolicy(BranchType.UNKNOWN)
