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
from adb.server.epoch import ServerEpoch
from adb.server.lifetime import AdbServerLifetime
from adb.server.endpoint import AdbServerEndpoint


@dataclass(frozen=True, slots=True, order=True)
class AdbServerRecoveryCycleId:
    """Opaque identity for one scheduled/retry server recovery cycle."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(
                "ADB server recovery cycle id must be a string, "
                f"got {type(self.value).__name__}"
            )
        normalized = self.value.strip()
        if not normalized:
            raise ValueError("ADB server recovery cycle id cannot be empty")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def new(cls) -> "AdbServerRecoveryCycleId":
        return cls(uuid4().hex)


_LIVENESS_FAILURE_TYPES = (
    AdbServerConnectionFailure,
    AdbServerProcessExitedFailure,
)


def _require_server(value: object) -> AdbServerLifetime:
    if not isinstance(value, AdbServerLifetime):
        raise TypeError("server must be AdbServerLifetime")
    return value


class _ServerSignalProjection:
    server: AdbServerLifetime

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def epoch(self) -> ServerEpoch:
        return self.server.epoch


@dataclass(frozen=True, slots=True)
class AdbServerReconciliationRequested(_ServerSignalProjection):
    """Request reconciliation after terminal server liveness failure."""

    server: AdbServerLifetime
    failure: AdbServerLivenessFailure

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, _LIVENESS_FAILURE_TYPES):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerRetired(_ServerSignalProjection):
    """Signal one retired ADB server domain lifetime."""

    server: AdbServerLifetime

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbServerLost(_ServerSignalProjection):
    """Failure evidence explaining why one already-retired server lifetime was lost."""

    server: AdbServerLifetime
    failure: AdbServerLivenessFailure

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, _LIVENESS_FAILURE_TYPES):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerRecovered(_ServerSignalProjection):
    """Signal carrying the recovered ADB server."""

    server: AdbServerLifetime

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetryDue:
    """Signal delivered when one ADB server recovery retry is due."""

    cycle_id: AdbServerRecoveryCycleId
    attempt_number: int
    launch_attempts: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.cycle_id, AdbServerRecoveryCycleId):
            raise TypeError("cycle_id must be AdbServerRecoveryCycleId")
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be greater than zero")
        if isinstance(self.launch_attempts, bool) or not isinstance(self.launch_attempts, int):
            raise TypeError("launch_attempts must be an integer")
        if self.launch_attempts < 0:
            raise ValueError("launch_attempts must be greater than or equal to zero")


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryExhausted:
    """Signal that genuine ADB server launch failures exhausted the retry budget."""

    cycle_id: AdbServerRecoveryCycleId
    attempts: int
    failure: AdbServerLaunchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.cycle_id, AdbServerRecoveryCycleId):
            raise TypeError("cycle_id must be AdbServerRecoveryCycleId")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be greater than zero")
        if not isinstance(self.failure, AdbServerLaunchFailure):
            raise TypeError("failure must be AdbServerLaunchFailure")


AdbServerSignal: TypeAlias = (
    AdbServerReconciliationRequested
    | AdbServerRetired
    | AdbServerLost
    | AdbServerRecovered
    | AdbServerRecoveryRetryDue
    | AdbServerRecoveryExhausted
)


__all__ = [
    "AdbServerLost",
    "AdbServerRecovered",
    "AdbServerRetired",
    "AdbServerReconciliationRequested",
    "AdbServerRecoveryCycleId",
    "AdbServerRecoveryExhausted",
    "AdbServerRecoveryRetryDue",
    "AdbServerSignal",
]
