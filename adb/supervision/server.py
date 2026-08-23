"""Compatibility exports for ADB server lifecycle supervision.

The canonical implementation lives in :mod:`adb.server.lifecycle.supervision`.
"""

from adb.server.lifecycle.supervision import (
    AdbServerEndpointPolicy,
    AdbServerFixedEndpoint,
    AdbServerPerGenerationEndpoint,
    AdbServerPinFirstResolvedEndpoint,
    AdbServerRecoveryCycleId,
    AdbServerSupervisionPolicy,
    AdbServerSupervisor,
)

__all__ = [
    "AdbServerEndpointPolicy",
    "AdbServerFixedEndpoint",
    "AdbServerPerGenerationEndpoint",
    "AdbServerPinFirstResolvedEndpoint",
    "AdbServerRecoveryCycleId",
    "AdbServerSupervisionPolicy",
    "AdbServerSupervisor",
]
