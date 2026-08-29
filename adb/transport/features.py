from __future__ import annotations

from typing import Protocol

from adb.aosp.transport.features import AdbTransportFeatures
from adb.server.address import AdbServerTcpAddress
from adb.transport.selection import AdbTransportSelector


class AdbTransportFeaturesReader(Protocol):
    """Read features for one selected ADB transport."""

    def read(
        self,
        endpoint: AdbServerTcpAddress,
        selector: AdbTransportSelector,
    ) -> AdbTransportFeatures:
        ...


__all__ = ["AdbTransportFeatures", "AdbTransportFeaturesReader"]
