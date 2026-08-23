from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint


@dataclass(frozen=True, slots=True)
class AdbServer:
    """Identity for one ADB server lifetime.

    The epoch distinguishes successive server lifetimes.
    """

    endpoint: AdbServerEndpoint
    epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if isinstance(self.epoch, bool) or not isinstance(self.epoch, int):
            raise TypeError("epoch must be an integer")
        if self.epoch <= 0:
            raise ValueError("epoch must be greater than zero")


@runtime_checkable
class AdbServerEpochIssuer(Protocol):
    """Issue epochs for successive ADB server lifetimes within one runtime."""

    def issue(self) -> int:
        ...


class AdbServerEpochSequence:
    """Runtime-scoped monotonically increasing ADB server epoch issuer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._current = 0

    def issue(self) -> int:
        with self._lock:
            self._current += 1
            return self._current


__all__ = ["AdbServer", "AdbServerEpochIssuer", "AdbServerEpochSequence"]
