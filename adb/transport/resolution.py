from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adb.server.lifetime import AdbServerLifetime
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.identity import AdbTransportId
from adb.tracking.snapshot.identity import AdbDevicesSnapshotEpoch
from adb.aosp.tracking.model import Device
from adb.tracking.snapshot.interpretation import (
    AdbObservedTransportCompatibility,
    classify_observed_transport,
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
    matches: tuple[Device, ...]
    type_mismatches: tuple[Device, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.matches, tuple) or not all(
            isinstance(row, Device) for row in self.matches
        ):
            raise TypeError("matches must be a tuple of Device values")
        if not isinstance(self.type_mismatches, tuple) or not all(
            isinstance(row, Device) for row in self.type_mismatches
        ):
            raise TypeError("type_mismatches must be a tuple of Device values")
        if any(
            row.serial != self.configuration.serial.value
            for row in (*self.matches, *self.type_mismatches)
        ):
            raise ValueError("resolution rows must match configured serial")
        if any(
            classify_observed_transport(self.configuration, row)
            is AdbObservedTransportCompatibility.MISMATCH
            for row in self.matches
        ):
            raise ValueError("matches must be compatible with the configured transport type")
        if any(
            classify_observed_transport(self.configuration, row)
            is not AdbObservedTransportCompatibility.MISMATCH
            for row in self.type_mismatches
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
    def row(self) -> Device | None:
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

    server: AdbServerLifetime
    snapshot_epoch: AdbDevicesSnapshotEpoch
    resolution: AdbConfiguredTransportResolution

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")
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
    def row(self) -> Device | None:
        return self.resolution.row



__all__ = [
    "AdbConfiguredTransportProjection",
    "AdbConfiguredTransportResolution",
    "AdbConfiguredTransportResolutionStatus",
]
