"""ADB transport configuration, lifecycle, selection, resolution, and capabilities."""

from adb.aosp.transport.address import AdbConnectAddress
from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
    AdbTransportConfiguration,
    AdbUsbTransportConfiguration,
)
from adb.transport.features import (
    AdbTransportFeatures,
    AdbTransportFeaturesReader,
)
from adb.transport.lifecycle import (
    AdbDeviceSideReconnect,
    AdbDeviceSideReconnector,
    AdbOfflineTransportsReconnect,
    AdbOfflineTransportsReconnector,
    AdbTcpConnect,
    AdbTcpConnector,
    AdbTcpDisconnect,
    AdbTcpDisconnector,
    AdbTcpTransportEnsureOrchestrator,
    AdbTcpTransportEnsurePolicy,
    AdbTcpTransportEnsureReadiness,
    AdbTcpTransportEnsureResult,
    AdbTcpTransportEnsureStatus,
    AdbTcpTransportEnsurer,
    AdbTcpTransportPresenceSatisfaction,
    AdbTcpTransportReadinessSatisfaction,
    AdbTransportReconnect,
    AdbTransportReconnector,
)
from adb.transport.resolution import (
    AdbConfiguredTransportProjection,
    AdbConfiguredTransportResolution,
    AdbConfiguredTransportResolutionStatus,
)
from adb.transport.identity import AdbDeviceSerial, AdbTransportId
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)

__all__ = [
    "AdbConfiguredTransport",
    "AdbConfiguredTransportProjection",
    "AdbConfiguredTransportResolution",
    "AdbConfiguredTransportResolutionStatus",
    "AdbDeviceSerial",
    "AdbDeviceSideReconnect",
    "AdbDeviceSideReconnector",
    "AdbOfflineTransportsReconnect",
    "AdbOfflineTransportsReconnector",
    "AdbConnectAddress",
    "AdbTcpConnect",
    "AdbTcpConnector",
    "AdbTcpDisconnect",
    "AdbTcpDisconnector",
    "AdbTcpTransportConfiguration",
    "AdbTcpTransportEnsureOrchestrator",
    "AdbTcpTransportEnsurePolicy",
    "AdbTcpTransportEnsureReadiness",
    "AdbTcpTransportEnsureResult",
    "AdbTcpTransportEnsureStatus",
    "AdbTcpTransportEnsurer",
    "AdbTcpTransportPresenceSatisfaction",
    "AdbTcpTransportReadinessSatisfaction",
    "AdbTransportById",
    "AdbTransportBySerial",
    "AdbTransportConfiguration",
    "AdbTransportFeatures",
    "AdbTransportFeaturesReader",
    "AdbTransportId",
    "AdbTransportReconnect",
    "AdbTransportReconnector",
    "AdbTransportSelector",
    "AdbUsbTransportConfiguration",
]
