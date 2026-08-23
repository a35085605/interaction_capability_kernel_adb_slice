"""Compatibility façade for long-lived ADB supervision APIs."""

from adb.transport.lifecycle.supervision.supervisor import AdbConfiguredTransportSupervisor
from adb.transport.inventory.tracking.supervision.supervisor import AdbDevicesTrackingSupervisor
from adb.transport.lifecycle.supervision.policy import (
    AdbConfiguredTransportSupervisionPolicy,
)
from adb.transport.inventory.tracking.supervision.policy import (
    AdbDevicesTrackingSupervisionPolicy,
)
from adb.server.lifecycle.provisioning import (
    AdbServerControllerProvisioner,
    AdbServerEndpointPolicy,
    AdbServerFixedEndpoint,
    AdbServerPerGenerationEndpoint,
    AdbServerPinFirstResolvedEndpoint,
    AdbServerProvisioner,
    resolve_server_provisioning_endpoint,
)
from adb.server.lifecycle.supervision import (
    AdbServerRecoveryCycleId,
    AdbServerSupervisionPolicy,
    AdbServerSupervisor,
)
from adb.transport.lifecycle.supervision.signal import (
    AdbConfiguredTransportRecoveryExhausted,
    AdbConfiguredTransportResolutionChanged,
    AdbConfiguredTransportSupervisionSignal,
)

AdbSupervisionSignal = AdbConfiguredTransportSupervisionSignal

__all__ = [
    "AdbConfiguredTransportRecoveryExhausted",
    "AdbConfiguredTransportResolutionChanged",
    "AdbConfiguredTransportSupervisionPolicy",
    "AdbConfiguredTransportSupervisor",
    "AdbDevicesTrackingSupervisionPolicy",
    "AdbDevicesTrackingSupervisor",
    "AdbServerControllerProvisioner",
    "AdbServerEndpointPolicy",
    "AdbServerFixedEndpoint",
    "AdbServerPerGenerationEndpoint",
    "AdbServerPinFirstResolvedEndpoint",
    "AdbServerProvisioner",
    "AdbServerRecoveryCycleId",
    "AdbServerSupervisionPolicy",
    "AdbServerSupervisor",
    "AdbSupervisionSignal",
    "resolve_server_provisioning_endpoint",
]
