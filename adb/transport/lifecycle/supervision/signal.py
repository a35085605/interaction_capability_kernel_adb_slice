from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

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
    """Signal that automatic recovery after an observed disappearance ended unsatisfied."""

    configuration: AdbConfiguredTransport
    result: AdbTransportEnsureResult

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.result, AdbTransportEnsureResult):
            raise TypeError("result must be AdbTransportEnsureResult")
        if self.result.operation.configuration != self.configuration:
            raise ValueError("recovery result must match configured transport")
        if self.result.status is AdbTransportEnsureStatus.SATISFIED:
            raise ValueError(
                "recovery exhausted signal requires an unsatisfied result"
            )


AdbConfiguredTransportSupervisionSignal: TypeAlias = (
    AdbConfiguredTransportResolutionChanged | AdbConfiguredTransportRecoveryExhausted
)


__all__ = [
    "AdbConfiguredTransportRecoveryExhausted",
    "AdbConfiguredTransportResolutionChanged",
    "AdbConfiguredTransportSupervisionSignal",
]
