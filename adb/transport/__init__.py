"""ADB transport specifications, lifecycle, selection, and capabilities."""

from adb.transport.address import AdbConnectAddress
from adb.transport.features import (
    AdbTransportFeatures,
    AdbTransportFeaturesReader,
)
from adb.transport.identity import AdbDeviceSerial, AdbTransportId
from adb.transport.model import (
    AdbObservedTransportKind,
    AdbObservedTransportState,
    AdbTransport,
    AdbTransportKind,
    AdbTransportState,
)
from adb.transport.control import (
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
from adb.transport.spec import (
    AdbTcpTransportSpec,
    AdbTransportSpec,
    AdbUsbTransportSpec,
)

__all__ = [
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
    "AdbTcpTransportSpec",
    "AdbTransport",
    "AdbTransportById",
    "AdbTransportBySerial",
    "AdbTransportFeatures",
    "AdbTransportFeaturesReader",
    "AdbTransportId",
    "AdbTransportKind",
    "AdbTransportSelector",
    "AdbTransportSpec",
    "AdbTransportState",
    "AdbUsbTransportSpec",
]
