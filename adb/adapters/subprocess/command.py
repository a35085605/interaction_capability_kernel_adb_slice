"""Compatibility exports for subprocess argument validation."""

from adb._subprocess import normalize_executable, normalize_timeout


__all__ = ["normalize_executable", "normalize_timeout"]
