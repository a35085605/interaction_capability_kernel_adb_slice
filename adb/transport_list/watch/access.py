from __future__ import annotations

from dataclasses import dataclass

from adb._lifecycle.endpoint import EndpointAccess


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAccess(EndpointAccess):
    """Usable transport-list watch endpoint metadata, without lifecycle authority."""


__all__ = ["AdbTransportListWatchAccess"]
