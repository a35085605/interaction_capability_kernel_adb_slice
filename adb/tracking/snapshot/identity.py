from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from adb.epoch import Epoch, EpochSequence
from adb.tracking.observation import AdbTrackedTransportObservation
from adb.tracking.snapshot.interpretation import (
    AdbObservedTransportCompatibility,
    classify_observed_transport,
)

if TYPE_CHECKING:
    from adb.transport.configuration import AdbConfiguredTransport
    from adb.transport.resolution import AdbConfiguredTransportResolution


class AdbDevicesSnapshotEpoch(Epoch):
    """Runtime-scoped ordinal identity for observed device snapshots."""

    __slots__ = ()


class AdbDevicesSnapshotEpochSequence(EpochSequence[AdbDevicesSnapshotEpoch]):
    """Runtime-scoped monotonically increasing device-snapshot epoch issuer."""

    def __init__(self) -> None:
        super().__init__(AdbDevicesSnapshotEpoch)


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshot:
    """Domain-identified complete set of tracked transport observations for one runtime."""

    observations: tuple[AdbTrackedTransportObservation, ...]
    epoch: AdbDevicesSnapshotEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or not all(
            isinstance(row, AdbTrackedTransportObservation) for row in self.observations
        ):
            raise TypeError(
                "observations must be a tuple of AdbTrackedTransportObservation values"
            )
        if not isinstance(self.epoch, AdbDevicesSnapshotEpoch):
            raise TypeError("epoch must be AdbDevicesSnapshotEpoch")

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
            row for row in self.observations if row.matches_serial(configuration.serial)
        )
        classified = tuple(
            (row, classify_observed_transport(configuration, row))
            for row in serial_matches
        )
        exact_matches = tuple(
            row
            for row, compatibility in classified
            if compatibility is AdbObservedTransportCompatibility.MATCH
        )
        unspecified_matches = tuple(
            row
            for row, compatibility in classified
            if compatibility is AdbObservedTransportCompatibility.UNSPECIFIED
        )
        matches = exact_matches if exact_matches else unspecified_matches
        type_mismatches = tuple(
            row
            for row, compatibility in classified
            if compatibility is AdbObservedTransportCompatibility.MISMATCH
        )
        return AdbConfiguredTransportResolution(
            configuration=configuration,
            matches=matches,
            type_mismatches=type_mismatches,
        )


__all__ = [
    "AdbDevicesSnapshot",
    "AdbDevicesSnapshotEpoch",
    "AdbDevicesSnapshotEpochSequence",
]
