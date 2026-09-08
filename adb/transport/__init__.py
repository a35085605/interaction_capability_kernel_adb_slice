"""ADB transport configuration, lifecycle, selection, and capabilities."""

from adb.transport.address import AdbConnectAddress
from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
    AdbTransportConfiguration,
    AdbTransportType,
    AdbUsbTransportConfiguration,
)
from adb.transport.features import (
    AdbTransportFeatures,
    AdbTransportFeaturesReader,
)
from adb.transport.identity import AdbDeviceSerial, AdbTransportId
from adb.transport.model import (
    AdbObservedTransportKind,
    AdbObservedTransportState,
    AdbTransport,
    AdbTransportState,
)
from adb.transport.lifecycle import (
    AdbTcpTransportConnectCommandSucceeded,
    AdbTcpTransportConnectResult,
    AdbTcpTransportControlFailed,
    AdbTcpTransportControlFailure,
    AdbTcpTransportController,
    AdbTcpTransportControlTimedOut,
    AdbTcpTransportDisconnectCommandSucceeded,
    AdbTcpTransportDisconnectResult,
)
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)

__all__ = [
    "AdbConfiguredTransport",
    "AdbObservedTransportKind",
    "AdbObservedTransportState",
    "AdbDeviceSerial",
    "AdbConnectAddress",
    "AdbTcpTransportConnectCommandSucceeded",
    "AdbTcpTransportConnectResult",
    "AdbTcpTransportControlFailed",
    "AdbTcpTransportControlFailure",
    "AdbTcpTransportController",
    "AdbTcpTransportControlTimedOut",
    "AdbTcpTransportDisconnectCommandSucceeded",
    "AdbTcpTransportDisconnectResult",
    "AdbTcpTransportConfiguration",
    "AdbTransport",
    "AdbTransportById",
    "AdbTransportBySerial",
    "AdbTransportConfiguration",
    "AdbTransportFeatures",
    "AdbTransportFeaturesReader",
    "AdbTransportId",
    "AdbTransportSelector",
    "AdbTransportState",
    "AdbTransportType",
    "AdbUsbTransportConfiguration",
]
