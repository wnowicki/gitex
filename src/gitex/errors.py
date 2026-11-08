"""Actionable adapter errors."""


class GitError(RuntimeError):
    """Git inspection failed; no workflow mutation was attempted."""
