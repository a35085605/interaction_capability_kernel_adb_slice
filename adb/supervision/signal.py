from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint
from adb.supervision.model import AdbServerRecoveryCycleId
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.inventory.resolution import AdbConfiguredTransportResolution
from adb.transport.lifecycle.ensure import (
    AdbTransportEnsureResult,
    AdbTransportEnsureStatus,
)


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportResolutionChanged:
    """Signal carrying one configured-transport projection in the current tracker scope."""

    previous: AdbConfiguredTransportResolution | None
    current: AdbConfiguredTransportResolution

    def __post_init__(self) -> None:
        if self.previous is not None and not isinstance(
            self.previous, AdbConfiguredTransportResolution
        ):
            raise TypeError("previous must be AdbConfiguredTransportResolution or None")
        if not isinstance(self.current, AdbConfiguredTransportResolution):
            raise TypeError("current must be AdbConfiguredTransportResolution")
        if self.previous is not None and (
            self.previous.configuration != self.current.configuration
        ):
            raise ValueError(
                "configured transport resolution change must keep one configuration"
            )


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportRecoveryExhausted:
    """Signal that automatic disappearance recovery ended unsatisfied."""

    configuration: AdbConfiguredTransport
    result: AdbTransportEnsureResult

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.result, AdbTransportEnsureResult):
            raise TypeError("result must be AdbTransportEnsureResult")
        if self.result.operation.configuration != self.configuration:
            raise ValueError("disappearance recovery result must match configured transport")
        if self.result.status is AdbTransportEnsureStatus.SATISFIED:
            raise ValueError(
                "disappearance recovery exhausted signal requires an unsatisfied result"
            )


@dataclass(frozen=True, slots=True)
class AdbServerReconciliationRequested:
    """Signal that current server liveness evidence requires ownership reconciliation."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipLost:
    """Signal that the current owned server lifetime has been invalidated."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerOwnershipRecovered:
    """Signal that fresh session-created ownership is available at the endpoint."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetryDue:
    """Signal delivered when one owned-server recovery retry becomes due."""

    endpoint: AdbServerEndpoint
    cycle_id: AdbServerRecoveryCycleId
    attempt_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
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

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(self.cycle_id, AdbServerRecoveryCycleId):
            raise TypeError("cycle_id must be AdbServerRecoveryCycleId")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be greater than zero")


AdbSupervisionSignal: TypeAlias = (
    AdbConfiguredTransportResolutionChanged
    | AdbConfiguredTransportRecoveryExhausted
    | AdbServerReconciliationRequested
    | AdbServerOwnershipLost
    | AdbServerOwnershipRecovered
    | AdbServerRecoveryRetryDue
    | AdbServerRecoveryExhausted
)


__all__ = [
    "AdbConfiguredTransportRecoveryExhausted",
    "AdbConfiguredTransportResolutionChanged",
    "AdbServerOwnershipLost",
    "AdbServerOwnershipRecovered",
    "AdbServerReconciliationRequested",
    "AdbServerRecoveryExhausted",
    "AdbServerRecoveryRetryDue",
    "AdbSupervisionSignal",
]
