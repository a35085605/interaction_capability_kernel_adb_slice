from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from adb.aosp.tracking.model import ConnectionType, Devices
from adb.epoch import Epoch, EpochSequence

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
    """Domain identity for one complete AOSP devices payload observed within one runtime.

    ``payload`` remains raw AOSP protocol evidence. Domain interpretation is explicit and occurs
    through snapshot operations such as ``resolve_configured_transport``.
    """

    payload: Devices
    epoch: AdbDevicesSnapshotEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Devices):
            raise TypeError("payload must be AOSP Devices")
        if not isinstance(self.epoch, AdbDevicesSnapshotEpoch):
            raise TypeError("epoch must be AdbDevicesSnapshotEpoch")

    def resolve_configured_transport(
        self,
        configuration: AdbConfiguredTransport,
    ) -> AdbConfiguredTransportResolution:
        """Interpret this AOSP observation against one configured transport.

        Exact USB/SOCKET evidence wins. AOSP ``UNKNOWN`` connection types are compatibility
        fallback evidence when no exact typed row is present.
        """

        from adb.transport.configuration import AdbConfiguredTransport
        from adb.transport.resolution import AdbConfiguredTransportResolution

        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")

        serial_matches = tuple(
            row for row in self.payload.devices if row.serial == configuration.serial.value
        )
        exact_matches = tuple(
            row
            for row in serial_matches
            if row.connection_type is configuration.expected_connection_type
        )
        unknown_matches = tuple(
            row
            for row in serial_matches
            if row.connection_type is ConnectionType.UNKNOWN
        )
        matches = exact_matches if exact_matches else unknown_matches
        type_mismatches = tuple(
            row
            for row in serial_matches
            if row.connection_type
            not in (configuration.expected_connection_type, ConnectionType.UNKNOWN)
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
