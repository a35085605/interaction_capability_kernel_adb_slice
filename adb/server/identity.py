from __future__ import annotations

from dataclasses import dataclass

from adb.server.epoch import AdbServerEpoch


@dataclass(frozen=True, slots=True)
class AdbServerIdentity:
    """Runtime-scoped identity of one committed ADB server lifetime."""

    epoch: AdbServerEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.epoch, AdbServerEpoch):
            raise TypeError("epoch must be AdbServerEpoch")


__all__ = ["AdbServerIdentity"]
