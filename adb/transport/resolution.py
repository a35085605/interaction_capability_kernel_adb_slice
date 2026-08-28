from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adb.server.identity import AdbServer
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.identity import AdbTransportId
from adb.tracking.snapshot.identity import AdbDevicesSnapshotEpoch
from adb.tracking.snapshot.model import (
    AdbConnectionType,
    AdbDevicesRecord,
    AdbTrackedDevice,
)


class AdbConfiguredTransportResolutionStatus(str, Enum):
    """How one configured transport identity appears in one complete track-devices snapshot."""

    ABSENT = "absent"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    TYPE_MISMATCH = "type_mismatch"


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportResolution:
    """Resolution of one configured transport against track-devices evidence."""

    configuration: AdbConfiguredTransport
    matches: tuple[AdbTrackedDevice, ...]
    type_mismatches: tuple[AdbTrackedDevice, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.matches, tuple) or not all(
            isinstance(row, AdbTrackedDevice) for row in self.matches
        ):
            raise TypeError("matches must be a tuple of AdbTrackedDevice values")
        if not isinstance(self.type_mismatches, tuple) or not all(
            isinstance(row, AdbTrackedDevice) for row in self.type_mismatches
        ):
            raise TypeError("type_mismatches must be a tuple of AdbTrackedDevice values")
        if any(
            row.serial != self.configuration.serial.value
            for row in (*self.matches, *self.type_mismatches)
        ):
            raise ValueError("resolution rows must match configured serial")
        if any(
            row.connection_type
            not in (
                self.configuration.expected_connection_type,
                AdbConnectionType.UNKNOWN,
            )
            for row in self.matches
        ):
            raise ValueError("matches must have the configured connection type")
        if any(
            row.connection_type
            in (
                self.configuration.expected_connection_type,
                AdbConnectionType.UNKNOWN,
            )
            for row in self.type_mismatches
        ):
            raise ValueError("type_mismatches must have a different connection type")

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
    def row(self) -> AdbTrackedDevice | None:
        return (
            self.matches[0]
            if self.status is AdbConfiguredTransportResolutionStatus.RESOLVED
            else None
        )

    @property
    def transport_id(self) -> AdbTransportId | None:
        """Convert a resolved raw AOSP transport ID into the domain identity.

        Zero means the observation did not provide a usable transport identity. Any other value
        crosses the domain boundary here and is validated by ``AdbTransportId``.
        """

        row = self.row
        if row is None or row.transport_id == 0:
            return None
        return AdbTransportId(row.transport_id)


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportProjection:
    """One configured-transport resolution bound to its source server and snapshot identity."""

    server: AdbServer
    snapshot_epoch: AdbDevicesSnapshotEpoch
    resolution: AdbConfiguredTransportResolution

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not isinstance(self.snapshot_epoch, AdbDevicesSnapshotEpoch):
            raise TypeError("snapshot_epoch must be AdbDevicesSnapshotEpoch")
        if not isinstance(self.resolution, AdbConfiguredTransportResolution):
            raise TypeError("resolution must be AdbConfiguredTransportResolution")

    @property
    def configuration(self) -> AdbConfiguredTransport:
        return self.resolution.configuration

    @property
    def status(self) -> AdbConfiguredTransportResolutionStatus:
        return self.resolution.status

    @property
    def row(self) -> AdbTrackedDevice | None:
        return self.resolution.row


def resolve_configured_transport(
    configuration: AdbConfiguredTransport,
    record: AdbDevicesRecord,
) -> AdbConfiguredTransportResolution:
    """Resolve a configured transport against one complete track-devices record.

    Exact USB/SOCKET matches win; UNKNOWN connection types are fallback compatibility evidence.
    """

    if not isinstance(configuration, AdbConfiguredTransport):
        raise TypeError("configuration must be AdbConfiguredTransport")
    if not isinstance(record, AdbDevicesRecord):
        raise TypeError("record must be AdbDevicesRecord")

    serial_matches = tuple(
        row for row in record.devices if row.serial == configuration.serial.value
    )
    exact_matches = tuple(
        row
        for row in serial_matches
        if row.connection_type is configuration.expected_connection_type
    )
    unknown_matches = tuple(
        row
        for row in serial_matches
        if row.connection_type is AdbConnectionType.UNKNOWN
    )
    # Older ADB servers may not report connection type. Prefer exact evidence whenever it is
    # available, and use UNKNOWN rows only as a compatibility fallback.
    matches = exact_matches if exact_matches else unknown_matches
    type_mismatches = tuple(
        row
        for row in serial_matches
        if row.connection_type
        not in (configuration.expected_connection_type, AdbConnectionType.UNKNOWN)
    )
    return AdbConfiguredTransportResolution(
        configuration=configuration,
        matches=matches,
        type_mismatches=type_mismatches,
    )


__all__ = [
    "AdbConfiguredTransportProjection",
    "AdbConfiguredTransportResolution",
    "AdbConfiguredTransportResolutionStatus",
    "resolve_configured_transport",
]
