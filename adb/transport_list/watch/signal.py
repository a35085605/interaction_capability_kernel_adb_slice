from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.transport_list.session_identity import AdbTransportListSessionIdentity
from adb.transport_list.watch.failure import AdbTransportListWatchFailure


def _require_session_identity(value: object) -> AdbTransportListSessionIdentity:
    if not isinstance(value, AdbTransportListSessionIdentity):
        raise TypeError("session_identity must be AdbTransportListSessionIdentity")
    return value


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStarted:
    """Signal that one observation session committed its initial transport list."""

    session_identity: AdbTransportListSessionIdentity

    def __post_init__(self) -> None:
        _require_session_identity(self.session_identity)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStopped:
    """Signal that one observation session ended and its authority was revoked."""

    session_identity: AdbTransportListSessionIdentity

    def __post_init__(self) -> None:
        _require_session_identity(self.session_identity)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchFailed:
    """Signal a transport-list watch failure for one observation session."""

    session_identity: AdbTransportListSessionIdentity
    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        _require_session_identity(self.session_identity)
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


AdbTransportListWatchSignal: TypeAlias = (
    AdbTransportListWatchStarted
    | AdbTransportListWatchStopped
    | AdbTransportListWatchFailed
)


__all__ = [
    "AdbTransportListWatchFailed",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchSignal",
    "AdbTransportListWatchStarted",
    "AdbTransportListWatchStopped",
]
