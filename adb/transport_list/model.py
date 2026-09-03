from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from adb.transport.model import AdbTransport
from adb.transport_list.interpretation import (
    AdbObservedTransportCompatibility,
    classify_observed_transport,
)

if TYPE_CHECKING:
    from adb.transport.configuration import AdbConfiguredTransport
    from adb.transport.resolution import AdbConfiguredTransportResolution


AdbTransportList: TypeAlias = tuple[AdbTransport, ...]


@dataclass(frozen=True, slots=True)
class AdbTransportListSnapshot:
    """Complete domain transport-list value observed from one ADB server."""

    transports: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.transports, tuple) or not all(
            isinstance(transport, AdbTransport) for transport in self.transports
        ):
            raise TypeError(
                "transports must be a tuple of AdbTransport values"
            )

    def resolve_configured_transport(
        self,
        configuration: AdbConfiguredTransport,
    ) -> AdbConfiguredTransportResolution:
        """Resolve one configured transport using exact typed evidence first and unspecified
        transport kinds as fallback evidence.
        """

        from adb.transport.configuration import AdbConfiguredTransport
        from adb.transport.resolution import AdbConfiguredTransportResolution

        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")

        serial_matches = tuple(
            transport
            for transport in self.transports
            if transport.matches_serial(configuration.serial)
        )
        classified = tuple(
            (transport, classify_observed_transport(configuration, transport))
            for transport in serial_matches
        )
        exact_matches = tuple(
            transport
            for transport, compatibility in classified
            if compatibility is AdbObservedTransportCompatibility.MATCH
        )
        unspecified_matches = tuple(
            transport
            for transport, compatibility in classified
            if compatibility is AdbObservedTransportCompatibility.UNSPECIFIED
        )
        matches = exact_matches if exact_matches else unspecified_matches
        type_mismatches = tuple(
            transport
            for transport, compatibility in classified
            if compatibility is AdbObservedTransportCompatibility.MISMATCH
        )
        return AdbConfiguredTransportResolution(
            configuration=configuration,
            matches=matches,
            type_mismatches=type_mismatches,
        )


__all__ = ["AdbTransportList", "AdbTransportListSnapshot"]
