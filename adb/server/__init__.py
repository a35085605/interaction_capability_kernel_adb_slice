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
    AdbServerAcquireAlreadyActive,
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerAcquireRequestMismatch,
    AdbServerAcquireResult,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
    AdbServerLifecycle,
    AdbServerLifecycleBusy,
    AdbServerLifecycleFactory,
    AdbServerReleaseAlreadyIdle,
    AdbServerReleaseFailed,
    AdbServerReleaseRequestMismatch,
    AdbServerReleaseResult,
    AdbServerReleaseSucceeded,
)
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerPhase, AdbServerSnapshot, AdbServerSnapshotReader


__all__ = [
    "AdbServerAcquireAlreadyActive",
    "AdbServerAcquireError",
    "AdbServerAcquireFailed",
    "AdbServerAcquireReleaseRequired",
    "AdbServerAcquireRequestMismatch",
    "AdbServerAcquireResult",
    "AdbServerAcquireSucceeded",
    "AdbServerCapability",
    "AdbServerConnectionFailure",
    "AdbServerFailure",
    "AdbServerGeneration",
    "AdbServerGenerationIssuer",
    "AdbServerGenerationMismatch",
    "AdbServerLaunchFailure",
    "AdbServerLifecycle",
    "AdbServerLifecycleBusy",
    "AdbServerLifecycleCoordinator",
    "AdbServerLifecycleFactory",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerPhase",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerReleaseAlreadyIdle",
    "AdbServerReleaseFailed",
    "AdbServerReleaseRequestMismatch",
    "AdbServerReleaseResult",
    "AdbServerReleaseSucceeded",
    "AdbServerRequest",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerSnapshot",
    "AdbServerSnapshotReader",
    "AdbServerTimeoutFailure",
]
