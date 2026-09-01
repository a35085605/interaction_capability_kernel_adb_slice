from __future__ import annotations

from adb.epoch import Epoch


class AdbServerEpoch(Epoch):
    """Runtime-scoped monotonic ordinal for committed ADB server lifetimes."""

    __slots__ = ()


__all__ = ["AdbServerEpoch"]
