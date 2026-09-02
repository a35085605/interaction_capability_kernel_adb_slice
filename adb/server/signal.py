from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias
from uuid import uuid4

from adb.server.failure import (
    AdbServerConnectionFailure,
    AdbServerLaunchFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
)
from adb.server.identity import AdbServerIdentity


@dataclass(frozen=True, slots=True, order=True)
class AdbServerRecoveryId:
    """Opaque identity for one runtime-supervised server recovery."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(
                "ADB server recovery id must be a string, "
                f"got {type(self.value).__name__}"
            )
        normalized = self.value.strip()
        if not normalized:
            raise ValueError("ADB server recovery id cannot be empty")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def new(cls) -> "AdbServerRecoveryId":
        return cls(uuid4().hex)


_LIVENESS_FAILURE_TYPES = (
    AdbServerConnectionFailure,
    AdbServerProcessExitedFailure,
)


def _require_server(value: object) -> AdbServerIdentity:
    if not isinstance(value, AdbServerIdentity):
        raise TypeError("server must be AdbServerIdentity")
    return value


class _ServerSignalProjection:
    server: AdbServerIdentity

    @property
    def identity(self) -> AdbServerIdentity:
        return self.server


@dataclass(frozen=True, slots=True)
class AdbServerReconciliationRequested(_ServerSignalProjection):
    """Request reconciliation after terminal server liveness failure."""

    server: AdbServerIdentity
    failure: AdbServerLivenessFailure

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, _LIVENESS_FAILURE_TYPES):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetryDue:
    """Runtime-supervision signal that one scheduled recovery acquisition attempt became due."""

    recovery_id: AdbServerRecoveryId
    attempt_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_id, AdbServerRecoveryId):
            raise TypeError("recovery_id must be AdbServerRecoveryId")
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be greater than zero")


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryExhausted:
    """Signal that genuine ADB server launch failures exhausted the retry budget."""

    recovery_id: AdbServerRecoveryId
    attempts: int
    failure: AdbServerLaunchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_id, AdbServerRecoveryId):
            raise TypeError("recovery_id must be AdbServerRecoveryId")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be greater than zero")
        if not isinstance(self.failure, AdbServerLaunchFailure):
            raise TypeError("failure must be AdbServerLaunchFailure")


AdbServerSignal: TypeAlias = (
    AdbServerReconciliationRequested
    | AdbServerRecoveryRetryDue
    | AdbServerRecoveryExhausted
)


__all__ = [
    "AdbServerReconciliationRequested",
    "AdbServerRecoveryId",
    "AdbServerRecoveryExhausted",
    "AdbServerRecoveryRetryDue",
    "AdbServerSignal",
]
