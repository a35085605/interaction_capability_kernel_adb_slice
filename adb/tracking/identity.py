from __future__ import annotations

from dataclasses import dataclass

from adb.epoch import Epoch, EpochSequence
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, ServerEpoch


class DevicesTrackingEpoch(Epoch):
    """Ordinal identity for successive track-devices observation sessions."""

    __slots__ = ()


class DevicesTrackingEpochSequence(EpochSequence[DevicesTrackingEpoch]):
    """Runtime-scoped monotonically increasing tracking-session epoch issuer."""

    def __init__(self) -> None:
        super().__init__(DevicesTrackingEpoch)


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingScope:
    """One track-devices observation session.

    ``epoch`` distinguishes successive tracker sessions for correlation and stale-signal
    fencing. Replacement trackers bound to the same ``AdbServer`` lifetime observe the same
    server-epoch data world but have distinct tracking epochs.
    """

    server: AdbServer
    epoch: DevicesTrackingEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not isinstance(self.epoch, DevicesTrackingEpoch):
            raise TypeError("epoch must be DevicesTrackingEpoch")

    @property
    def server_endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def server_epoch(self) -> ServerEpoch:
        return self.server.epoch


__all__ = [
    "AdbDevicesTrackingScope",
    "DevicesTrackingEpoch",
    "DevicesTrackingEpochSequence",
]
