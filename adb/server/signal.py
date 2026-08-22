from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias
from uuid import uuid4

from adb.server.failure import (
    AdbServerCloseUnprovenFailure,
    AdbServerConnectionFailure,
    AdbServerLaunchFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
)
from adb.server.identity import AdbServer
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


def _require_server(value: object) -> AdbServer:
    if not isinstance(value, AdbServer):
        raise TypeError("server must be AdbServer")
    return value


def _require_endpoint(value: object) -> AdbServerEndpoint:
    if not isinstance(value, AdbServerEndpoint):
        raise TypeError("endpoint must be AdbServerEndpoint")
    return value


class _ServerSignalProjection:
    server: AdbServer

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def epoch(self) -> int:
        return self.server.epoch


@dataclass(frozen=True, slots=True)
class AdbServerReconciliationRequested(_ServerSignalProjection):
    """Signal that terminal liveness evidence requires server-fenced reconciliation."""

    server: AdbServer
    failure: AdbServerLivenessFailure

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, _LIVENESS_FAILURE_TYPES):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipRetired(_ServerSignalProjection):
    """Public fact that one server lifetime is irreversibly no longer usable."""

    server: AdbServer

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipLost(_ServerSignalProjection):
    """Failure evidence explaining why one already-retired server lifetime was lost."""

    server: AdbServer
    failure: AdbServerLivenessFailure

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, _LIVENESS_FAILURE_TYPES):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerNativeCloseCompleted(_ServerSignalProjection):
    """Private-lifecycle fact that termination of a retired server was proven."""

    server: AdbServer

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbServerNativeCloseUnproven(_ServerSignalProjection):
    """Private-lifecycle fact that termination of a retired server remains unproven."""

    server: AdbServer
    failure: AdbServerCloseUnprovenFailure

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, AdbServerCloseUnprovenFailure):
            raise TypeError("failure must be AdbServerCloseUnprovenFailure")


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipRecovered(_ServerSignalProjection):
    """Signal carrying the fresh usable ADB-owned server."""

    server: AdbServer

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetryDue:
    """Signal delivered when one ADB-owned server recovery retry becomes due."""

    endpoint: AdbServerEndpoint
    cycle_id: AdbServerRecoveryCycleId
    attempt_number: int

    def __post_init__(self) -> None:
        _require_endpoint(self.endpoint)
        if not isinstance(self.cycle_id, AdbServerRecoveryCycleId):
            raise TypeError("cycle_id must be AdbServerRecoveryCycleId")
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be greater than zero")


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryExhausted:
    """Signal that fresh ADB-owned server creation exhausted its retry budget."""

    endpoint: AdbServerEndpoint
    cycle_id: AdbServerRecoveryCycleId
    attempts: int
    failure: AdbServerLaunchFailure

    def __post_init__(self) -> None:
        _require_endpoint(self.endpoint)
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
    | AdbServerOwnershipRetired
    | AdbServerOwnershipLost
    | AdbServerNativeCloseCompleted
    | AdbServerNativeCloseUnproven
    | AdbServerOwnershipRecovered
    | AdbServerRecoveryRetryDue
    | AdbServerRecoveryExhausted
)


__all__ = [
    "AdbServerNativeCloseCompleted",
    "AdbServerNativeCloseUnproven",
    "AdbServerOwnershipLost",
    "AdbServerOwnershipRecovered",
    "AdbServerOwnershipRetired",
    "AdbServerReconciliationRequested",
    "AdbServerRecoveryCycleId",
    "AdbServerRecoveryExhausted",
    "AdbServerRecoveryRetryDue",
    "AdbServerSignal",
]
