from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adb.server.identity import AdbServerIdentity
from adb.transport.model import AdbTransport
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.interpretation import (
    AdbObservedTransportCompatibility,
    classify_observed_transport,
)
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.identity import AdbTransportId


class AdbConfiguredTransportResolutionStatus(str, Enum):
    """How one configured transport identity appears in one complete transport-list snapshot."""

    ABSENT = "absent"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    TYPE_MISMATCH = "type_mismatch"


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportResolution:
    """Resolution of one configured transport against domain transport-list evidence."""

    configuration: AdbConfiguredTransport
    matches: tuple[AdbTransport, ...]
    type_mismatches: tuple[AdbTransport, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.matches, tuple) or not all(
            isinstance(transport, AdbTransport) for transport in self.matches
        ):
            raise TypeError("matches must be a tuple of AdbTransport values")
        if not isinstance(self.type_mismatches, tuple) or not all(
            isinstance(transport, AdbTransport)
            for transport in self.type_mismatches
        ):
            raise TypeError("type_mismatches must be a tuple of AdbTransport values")
        if any(
            not transport.matches_serial(self.configuration.serial)
            for transport in (*self.matches, *self.type_mismatches)
        ):
            raise ValueError("resolution transports must match configured serial")
        if any(
            classify_observed_transport(self.configuration, transport)
            is AdbObservedTransportCompatibility.MISMATCH
            for transport in self.matches
        ):
            raise ValueError("matches must be compatible with the configured transport type")
        if any(
            classify_observed_transport(self.configuration, transport)
            is not AdbObservedTransportCompatibility.MISMATCH
            for transport in self.type_mismatches
        ):
            raise ValueError("type_mismatches must have a different transport type")

    @property
    def status(self) -> AdbConfiguredTransportResolutionStatus:
        """Classify the immutable resolution evidence."""

        if not self.matches:
            return (
                AdbConfiguredTransportResolutionStatus.TYPE_MISMATCH
                if self.type_mismatches
                else AdbConfiguredTransportResolutionStatus.ABSENT
            )
        if len(self.matches) == 1:
            return AdbConfiguredTransportResolutionStatus.RESOLVED
        return AdbConfiguredTransportResolutionStatus.AMBIGUOUS

    @property
    def transport(self) -> AdbTransport | None:
        return (
            self.matches[0]
            if self.status is AdbConfiguredTransportResolutionStatus.RESOLVED
            else None
        )

    @property
    def transport_id(self) -> AdbTransportId | None:
        """Return the already-validated server-local identity of a resolved transport."""

        transport = self.transport
        return transport.transport_id if transport is not None else None


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportProjection:
    """One configured-transport resolution bound to source server and list identities."""

    server: AdbServerIdentity
    transport_list: AdbTransportListIdentity
    resolution: AdbConfiguredTransportResolution

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(self.transport_list, AdbTransportListIdentity):
            raise TypeError("transport_list must be AdbTransportListIdentity")
        if not isinstance(self.resolution, AdbConfiguredTransportResolution):
            raise TypeError("resolution must be AdbConfiguredTransportResolution")

    @property
    def configuration(self) -> AdbConfiguredTransport:
        return self.resolution.configuration

    @property
    def status(self) -> AdbConfiguredTransportResolutionStatus:
        return self.resolution.status

    @property
    def transport(self) -> AdbTransport | None:
        return self.resolution.transport


__all__ = [
    "AdbConfiguredTransportProjection",
    "AdbConfiguredTransportResolution",
    "AdbConfiguredTransportResolutionStatus",
]
