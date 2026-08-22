from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.failure import (
    AdbServerCloseUnprovenFailure,
    AdbServerConnectionFailure,
    AdbServerLaunchFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
)
from adb.server.identity import AdbServerIncarnation
from adb.server.model import AdbServerEndpoint, AdbServerRecoveryCycleId
from adb.server.ownership import AdbOwnedServer


_LIVENESS_FAILURE_TYPES = (
    AdbServerConnectionFailure,
    AdbServerProcessExitedFailure,
)


def _require_incarnation(value: object) -> AdbServerIncarnation:
    if not isinstance(value, AdbServerIncarnation):
        raise TypeError("incarnation must be AdbServerIncarnation")
    return value


def _require_endpoint(value: object) -> AdbServerEndpoint:
    if not isinstance(value, AdbServerEndpoint):
        raise TypeError("endpoint must be AdbServerEndpoint")
    return value


class _IncarnationSignalProjection:
    incarnation: AdbServerIncarnation

    @property
    def endpoint(self) -> AdbServerEndpoint:
        """Compatibility projection of :attr:`incarnation`."""

        return self.incarnation.endpoint

    @property
    def generation(self) -> int:
        """Compatibility projection of :attr:`incarnation`."""

        return self.incarnation.generation


@dataclass(frozen=True, slots=True)
class AdbServerReconciliationRequested(_IncarnationSignalProjection):
    """Signal that terminal liveness evidence requires incarnation-fenced reconciliation."""

    incarnation: AdbServerIncarnation
    failure: AdbServerLivenessFailure

    def __post_init__(self) -> None:
        _require_incarnation(self.incarnation)
        if not isinstance(self.failure, _LIVENESS_FAILURE_TYPES):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipRetired(_IncarnationSignalProjection):
    """Public fact that one owned incarnation is irreversibly no longer usable."""

    incarnation: AdbServerIncarnation

    def __post_init__(self) -> None:
        _require_incarnation(self.incarnation)


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipLost(_IncarnationSignalProjection):
    """Failure evidence explaining why one already-retired owned incarnation was lost."""

    incarnation: AdbServerIncarnation
    failure: AdbServerLivenessFailure

    def __post_init__(self) -> None:
        _require_incarnation(self.incarnation)
        if not isinstance(self.failure, _LIVENESS_FAILURE_TYPES):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerNativeCloseCompleted(_IncarnationSignalProjection):
    """Private-lifecycle fact that termination of a retired incarnation was proven."""

    incarnation: AdbServerIncarnation

    def __post_init__(self) -> None:
        _require_incarnation(self.incarnation)


@dataclass(frozen=True, slots=True)
class AdbServerNativeCloseUnproven(_IncarnationSignalProjection):
    """Private-lifecycle fact that termination of a retired incarnation remains unproven."""

    incarnation: AdbServerIncarnation
    failure: AdbServerCloseUnprovenFailure

    def __post_init__(self) -> None:
        _require_incarnation(self.incarnation)
        if not isinstance(self.failure, AdbServerCloseUnprovenFailure):
            raise TypeError("failure must be AdbServerCloseUnprovenFailure")


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipRecovered:
    """Signal carrying the fresh usable process-owned ADB server incarnation."""

    server: AdbOwnedServer

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbOwnedServer):
            raise TypeError("server must be AdbOwnedServer")

    @property
    def incarnation(self) -> AdbServerIncarnation:
        return self.server.incarnation

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.incarnation.endpoint

    @property
    def generation(self) -> int:
        return self.incarnation.generation


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetryDue:
    """Signal delivered when one owned-server recovery retry becomes due."""

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
    """Signal that fresh owned-server creation exhausted its retry budget."""

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
    "AdbServerRecoveryExhausted",
    "AdbServerRecoveryRetryDue",
    "AdbServerSignal",
]
