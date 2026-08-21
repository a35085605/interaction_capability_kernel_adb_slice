from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from adb.server.model import AdbServerEndpoint
from adb.transport.inventory.model import AdbDevicesSnapshot

if TYPE_CHECKING:
    from adb._internal.client import AdbServiceClient


class AdbDevicesSnapshotReader(Protocol):
    """Read the current complete ADB transport-inventory snapshot."""

    def read(self, endpoint: AdbServerEndpoint) -> AdbDevicesSnapshot:
        ...


_ClientFactory = Callable[[AdbServerEndpoint], "AdbServiceClient"]


def _default_client_factory(endpoint: AdbServerEndpoint) -> AdbServiceClient:
    from adb._internal.client import AdbServiceClient

    return AdbServiceClient(endpoint)


class SmartSocketAdbDevicesSnapshotReader:
    """One-shot inventory snapshot reader from the first protobuf tracker frame."""

    _SERVICE = "host:track-devices-proto-binary"

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, endpoint: AdbServerEndpoint) -> AdbDevicesSnapshot:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        from adb._internal.proto import parse_devices_snapshot

        payload = self._client_factory(endpoint).first_stream_frame(self._SERVICE)
        return parse_devices_snapshot(payload)


__all__ = ["AdbDevicesSnapshotReader", "SmartSocketAdbDevicesSnapshotReader"]
