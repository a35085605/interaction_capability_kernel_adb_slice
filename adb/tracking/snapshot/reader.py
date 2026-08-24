from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from adb.epoch import EpochIssuer
from adb.server.identity import AdbServer
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshot,
    AdbDevicesSnapshotEpoch,
)

if TYPE_CHECKING:
    from adb._internal.client import AdbServiceClient


class AdbDevicesSnapshotReader(Protocol):
    """Read one freshly identified complete ADB track-devices snapshot."""

    def read(self, server: AdbServer) -> AdbDevicesSnapshot:
        ...


_ClientFactory = Callable[[AdbServer], "AdbServiceClient"]


def _default_client_factory(server: AdbServer) -> AdbServiceClient:
    from adb._internal.client import AdbServiceClient

    return AdbServiceClient(server.endpoint)


class SmartSocketAdbDevicesSnapshotReader:
    """One-shot track-devices snapshot reader from the first protobuf tracker frame."""

    _SERVICE = "host:track-devices-proto-binary"

    def __init__(
        self,
        *,
        devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch],
        _client_factory: _ClientFactory = _default_client_factory,
    ) -> None:
        if not isinstance(devices_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("devices_snapshot_epoch_issuer must satisfy EpochIssuer")
        self._devices_snapshot_epoch_issuer = devices_snapshot_epoch_issuer
        self._client_factory = _client_factory

    def read(self, server: AdbServer) -> AdbDevicesSnapshot:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        from adb._internal.proto import parse_devices_record

        payload = self._client_factory(server).first_stream_frame(self._SERVICE)
        return AdbDevicesSnapshot(
            parse_devices_record(payload),
            self._devices_snapshot_epoch_issuer.issue(),
        )


__all__ = ["AdbDevicesSnapshotReader", "SmartSocketAdbDevicesSnapshotReader"]
