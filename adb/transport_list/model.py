from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, overload

from adb.transport.model import AdbTransport
from adb.transport_list.interpretation import (
    AdbObservedTransportCompatibility,
    classify_observed_transport,
)

if TYPE_CHECKING:
    from adb.transport.configuration import AdbConfiguredTransport
    from adb.transport.resolution import AdbConfiguredTransportResolution


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportList:
    """Immutable complete transport list observed from one ADB server."""

    transports: tuple[AdbTransport, ...]

    def __init__(self, transports: Iterable[AdbTransport] = ()) -> None:
        if isinstance(transports, AdbTransportList):
            normalized = transports.transports
        else:
            try:
                normalized = tuple(transports)
            except TypeError as exc:
                raise TypeError("transports must be an iterable of AdbTransport values") from exc
        if not all(isinstance(transport, AdbTransport) for transport in normalized):
            raise TypeError("transports must contain only AdbTransport values")
        object.__setattr__(self, "transports", normalized)

    def __iter__(self) -> Iterator[AdbTransport]:
        return iter(self.transports)

    def __len__(self) -> int:
        return len(self.transports)

    @overload
    def __getitem__(self, index: int) -> AdbTransport: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[AdbTransport, ...]: ...

    def __getitem__(self, index: int | slice) -> AdbTransport | tuple[AdbTransport, ...]:
        return self.transports[index]

    def resolve_configured_transport(
        self,
        configuration: AdbConfiguredTransport,
    ) -> AdbConfiguredTransportResolution:
        """Resolve a configured transport from exact evidence with unspecified-kind fallback."""

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


__all__ = ["AdbTransportList"]
