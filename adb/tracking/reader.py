from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from adb.server.identity import AdbServer
from adb.tracking.model import AdbDevicesSnapshot

if TYPE_CHECKING:
    from adb._internal.client import AdbServiceClient


class AdbDevicesSnapshotReader(Protocol):
    """Read the current complete ADB track-devices snapshot."""

    def read(self, server: AdbServer) -> AdbDevicesSnapshot:
        ...


_ClientFactory = Callable[[AdbServer], "AdbServiceClient"]


def _default_client_factory(server: AdbServer) -> AdbServiceClient:
    from adb._internal.client import AdbServiceClient

    return AdbServiceClient(server.endpoint)


class SmartSocketAdbDevicesSnapshotReader:
    """One-shot track-devices snapshot reader from the first protobuf tracker frame."""

    _SERVICE = "host:track-devices-proto-binary"

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, server: AdbServer) -> AdbDevicesSnapshot:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        from adb._internal.proto import parse_devices_snapshot

        payload = self._client_factory(server).first_stream_frame(self._SERVICE)
        return parse_devices_snapshot(payload)


__all__ = ["AdbDevicesSnapshotReader", "SmartSocketAdbDevicesSnapshotReader"]
