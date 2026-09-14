from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessExit:
    code: int


@dataclass(frozen=True, slots=True)
class WindowsProcessSpec:
    argv: tuple[str, ...]
    executable: str | None = None
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    inherited_handles: tuple[int, ...] = ()
    creation_flags: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple):
            raise TypeError("argv must be tuple[str, ...]")
        if not self.argv:
            raise ValueError("argv must not be empty")
        if any(not isinstance(value, str) for value in self.argv):
            raise TypeError("argv must contain only str")
        if any("\0" in value for value in self.argv):
            raise ValueError("argv must not contain NUL")

        if self.executable is not None:
            if not isinstance(self.executable, str):
                raise TypeError("executable must be str or None")
            if not self.executable:
                raise ValueError("executable must not be empty")
            if "\0" in self.executable:
                raise ValueError("executable must not contain NUL")

        if self.cwd is not None:
            if not isinstance(self.cwd, str):
                raise TypeError("cwd must be str or None")
            if "\0" in self.cwd:
                raise ValueError("cwd must not contain NUL")

        if not isinstance(self.inherited_handles, tuple):
            raise TypeError("inherited_handles must be tuple[int, ...]")
        for handle in self.inherited_handles:
            if isinstance(handle, bool) or not isinstance(handle, int):
                raise TypeError("inherited_handles must contain only int handles")

        if isinstance(self.creation_flags, bool) or not isinstance(
            self.creation_flags, int
        ):
            raise TypeError("creation_flags must be int")
        if not 0 <= self.creation_flags <= 0xFFFFFFFF:
            raise ValueError("creation_flags must fit in DWORD")

__all__ = ["ProcessExit", "WindowsProcessSpec"]
