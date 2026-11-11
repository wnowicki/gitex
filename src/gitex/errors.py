"""Actionable adapter errors."""


class GitError(RuntimeError):
    """Git inspection failed; no workflow mutation was attempted."""


class ConfigError(ValueError):
    """Repository configuration is invalid and must be corrected explicitly."""
