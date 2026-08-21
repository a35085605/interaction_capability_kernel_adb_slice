from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from adb.server.ownership import AdbOwnedServer
from adb.transport.inventory.model import AdbDevicesSnapshot

if TYPE_CHECKING:
    from adb._internal.client import AdbServiceClient


class AdbDevicesSnapshotReader(Protocol):
    """Read the current complete ADB transport-inventory snapshot."""

    def read(self, server: AdbOwnedServer) -> AdbDevicesSnapshot:
        ...


_ClientFactory = Callable[[AdbOwnedServer], "AdbServiceClient"]


def _default_client_factory(server: AdbOwnedServer) -> AdbServiceClient:
    from adb._internal.client import AdbServiceClient

    return AdbServiceClient(server.endpoint)


class SmartSocketAdbDevicesSnapshotReader:
    """One-shot inventory snapshot reader from the first protobuf tracker frame."""

    _SERVICE = "host:track-devices-proto-binary"

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, server: AdbOwnedServer) -> AdbDevicesSnapshot:
        if not isinstance(server, AdbOwnedServer):
            raise TypeError("server must be AdbOwnedServer")
        from adb._internal.proto import parse_devices_snapshot

        payload = self._client_factory(server).first_stream_frame(self._SERVICE)
        return parse_devices_snapshot(payload)


__all__ = ["AdbDevicesSnapshotReader", "SmartSocketAdbDevicesSnapshotReader"]
