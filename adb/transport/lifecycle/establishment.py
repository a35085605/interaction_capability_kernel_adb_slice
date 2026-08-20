from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
)
from adb.transport.lifecycle.command import (
    AdbTcpConnect,
    AdbTcpConnector,
    AdbTransportCommandOperation,
)
from native_attempt import NativeAttemptResult


@dataclass(frozen=True, slots=True)
class AdbTransportEstablishmentAttempt:
    """One active command attempt made to establish an absent transport's presence."""

    operation: AdbTransportCommandOperation
    result: NativeAttemptResult

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbTransportCommandOperation):
            raise TypeError("operation must be an ADB transport command operation")
        if not isinstance(self.result, NativeAttemptResult):
            raise TypeError("result must be NativeAttemptResult")


@runtime_checkable
class AdbTransportEstablisher(Protocol):
    """Transport-kind-specific active establishment of configured transport presence."""

    def supports(self, configuration: AdbConfiguredTransport) -> bool: ...

    def establish(
        self,
        configuration: AdbConfiguredTransport,
    ) -> AdbTransportEstablishmentAttempt: ...


class AdbTcpTransportEstablisher:
    """Establish an absent TCP transport with exactly one ``adb connect`` attempt."""

    def __init__(self, connector: AdbTcpConnector) -> None:
        if not callable(getattr(connector, "connect", None)):
            raise TypeError("connector must provide connect()")
        self._connector = connector

    def supports(self, configuration: AdbConfiguredTransport) -> bool:
        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        return isinstance(configuration.transport, AdbTcpTransportConfiguration)

    def establish(
        self,
        configuration: AdbConfiguredTransport,
    ) -> AdbTransportEstablishmentAttempt:
        if not self.supports(configuration):
            raise ValueError(
                "TCP establishment requires a TCP transport configuration"
            )
        transport = configuration.transport
        assert isinstance(transport, AdbTcpTransportConfiguration)
        operation = AdbTcpConnect(transport.address)
        return AdbTransportEstablishmentAttempt(
            operation,
            self._connector.connect(operation),
        )


__all__ = [
    "AdbTcpTransportEstablisher",
    "AdbTransportEstablisher",
    "AdbTransportEstablishmentAttempt",
]
