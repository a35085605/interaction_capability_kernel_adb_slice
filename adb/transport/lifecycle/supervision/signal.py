from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
)
from adb.transport.resolution import AdbConfiguredTransportProjection
from adb.transport.lifecycle.ensure import (
    AdbTcpTransportEnsureResult,
    AdbTcpTransportEnsureStatus,
)


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportResolutionChanged:
    """Signal carrying a provenance-preserving configured-transport projection change."""

    previous: AdbConfiguredTransportProjection | None
    current: AdbConfiguredTransportProjection

    def __post_init__(self) -> None:
        if self.previous is not None and not isinstance(
            self.previous, AdbConfiguredTransportProjection
        ):
            raise TypeError("previous must be AdbConfiguredTransportProjection or None")
        if not isinstance(self.current, AdbConfiguredTransportProjection):
            raise TypeError("current must be AdbConfiguredTransportProjection")
        if self.previous is not None and (
            self.previous.configuration != self.current.configuration
        ):
            raise ValueError(
                "configured transport projection change must keep one configuration"
            )


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportRecoveryExhausted:
    """Signal that TCP recovery for an observed absence ended unsatisfied."""

    configuration: AdbConfiguredTransport
    result: AdbTcpTransportEnsureResult

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.configuration.transport, AdbTcpTransportConfiguration):
            raise ValueError("recovery exhausted signals require a TCP configuration")
        if not isinstance(self.result, AdbTcpTransportEnsureResult):
            raise TypeError("result must be AdbTcpTransportEnsureResult")
        if self.result.operation.configuration != self.configuration:
            raise ValueError("recovery result must match configured transport")
        if self.result.status is AdbTcpTransportEnsureStatus.SATISFIED:
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
