"""ADB server generation, request, capability, lifecycle, snapshot, and failure contracts."""

from adb.server.capability import AdbServerCapability
from adb.server.coordinator import AdbServerLifecycleCoordinator
from adb.server.error import AdbServerAcquireError
from adb.server.failure import (
    AdbServerConnectionFailure,
    AdbServerFailure,
    AdbServerLaunchFailure,
    AdbServerLifecycleFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
    AdbServerProtocolFailure,
    AdbServerRequestFailure,
    AdbServerServiceFailure,
    AdbServerTimeoutFailure,
)
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.lifecycle import (
    AdbServerAcquireResult,
    AdbServerLifecycle,
    AdbServerLifecycleFactory,
    AdbServerLifecycleResult,
    AdbServerReleaseResult,
)
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerPhase, AdbServerSnapshot, AdbServerSnapshotReader


__all__ = [
    "AdbServerAcquireError",
    "AdbServerAcquireResult",
    "AdbServerCapability",
    "AdbServerConnectionFailure",
    "AdbServerFailure",
    "AdbServerGeneration",
    "AdbServerGenerationIssuer",
    "AdbServerLaunchFailure",
    "AdbServerLifecycle",
    "AdbServerLifecycleCoordinator",
    "AdbServerLifecycleFactory",
    "AdbServerLifecycleFailure",
    "AdbServerLifecycleResult",
    "AdbServerLivenessFailure",
    "AdbServerPhase",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerReleaseResult",
    "AdbServerRequest",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerSnapshot",
    "AdbServerSnapshotReader",
    "AdbServerTimeoutFailure",
]
