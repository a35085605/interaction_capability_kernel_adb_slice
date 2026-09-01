from __future__ import annotations

from adb.epoch import Epoch


class ServerEpoch(Epoch):
    """Runtime-scoped identity for one committed ADB server lifetime."""

    __slots__ = ()


__all__ = ["ServerEpoch"]
