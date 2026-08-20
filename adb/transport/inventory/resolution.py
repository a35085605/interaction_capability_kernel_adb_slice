from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.inventory.model import (
    AdbConnectionType,
    AdbDevicesSnapshot,
    AdbTrackedDevice,
)


class AdbConfiguredTransportResolutionStatus(str, Enum):
    """How one configured transport identity appears in one complete inventory snapshot."""

    ABSENT = "absent"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    TYPE_MISMATCH = "type_mismatch"


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportResolution:
    """Pure projection of one configured transport into inventory evidence.

    The result separates rows matching both serial and configured connection type from rows that
    reuse the serial with a different known connection type. It does not construct an
    ``AdbTransportById`` selector or otherwise change how commands select the transport.
    """

    configuration: AdbConfiguredTransport
    status: AdbConfiguredTransportResolutionStatus
    matches: tuple[AdbTrackedDevice, ...]
    type_mismatches: tuple[AdbTrackedDevice, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.status, AdbConfiguredTransportResolutionStatus):
            raise TypeError("status must be AdbConfiguredTransportResolutionStatus")
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
        expected = (
            AdbConfiguredTransportResolutionStatus.ABSENT
            if not self.matches and not self.type_mismatches
            else AdbConfiguredTransportResolutionStatus.TYPE_MISMATCH
            if not self.matches
            else AdbConfiguredTransportResolutionStatus.RESOLVED
            if len(self.matches) == 1
            else AdbConfiguredTransportResolutionStatus.AMBIGUOUS
        )
        if self.status is not expected:
            raise ValueError("resolution status does not match resolution evidence")

    @property
    def row(self) -> AdbTrackedDevice | None:
        return (
            self.matches[0]
            if self.status is AdbConfiguredTransportResolutionStatus.RESOLVED
            else None
        )


def resolve_configured_transport(
    configuration: AdbConfiguredTransport,
    snapshot: AdbDevicesSnapshot,
) -> AdbConfiguredTransportResolution:
    """Locate the configured serial and transport kind in fresh inventory evidence.

    This lookup supports readiness presence/state evaluation only. It does not translate the
    serial into a transport-id selector and does not participate in native serial selection.
    Exact USB/SOCKET evidence is preferred; UNKNOWN connection types are accepted only when no
    exact row is available for compatibility with older ADB servers.
    """

    if not isinstance(configuration, AdbConfiguredTransport):
        raise TypeError("configuration must be AdbConfiguredTransport")
    if not isinstance(snapshot, AdbDevicesSnapshot):
        raise TypeError("snapshot must be AdbDevicesSnapshot")

    serial_matches = tuple(
        row for row in snapshot.devices if row.serial == configuration.serial.value
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
    status = (
        AdbConfiguredTransportResolutionStatus.ABSENT
        if not matches and not type_mismatches
        else AdbConfiguredTransportResolutionStatus.TYPE_MISMATCH
        if not matches
        else AdbConfiguredTransportResolutionStatus.RESOLVED
        if len(matches) == 1
        else AdbConfiguredTransportResolutionStatus.AMBIGUOUS
    )
    return AdbConfiguredTransportResolution(
        configuration,
        status,
        matches,
        type_mismatches,
    )


__all__ = [
    "AdbConfiguredTransportResolution",
    "AdbConfiguredTransportResolutionStatus",
    "resolve_configured_transport",
]
