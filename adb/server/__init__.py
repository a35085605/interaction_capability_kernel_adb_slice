"""ADB server endpoint, identity, lifecycle, failure, and status contracts."""

from adb.server.availability import AdbServerUnavailableError
from adb.server.candidate import AdbServerCandidate, AdbServerCandidateFactory

from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.backend import AdbServerBackend
from adb.server.lifecycle.coordinator import AdbServerLifecycleCoordinator
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
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationStateConflict,
    AdbServerActivationResult,
    AdbServerDeactivated,
    AdbServerDeactivationStateConflict,
    AdbServerDeactivationResult,
    AdbServerState,
    AdbServerStateStatus,
    AdbServerStateStore,
    AdbServerStateView,
    AdbServerStateWriter,
)
from adb.server.endpoint import AdbServerEndpoint

__all__ = [
    "AdbServerActivated",
    "AdbServerActivationStateConflict",
    "AdbServerActivationResult",
    "AdbServerBackend",
    "AdbServerDeactivated",
    "AdbServerDeactivationStateConflict",
    "AdbServerDeactivationResult",
    "AdbServerConnectionFailure",
    "AdbServerBootstrapError",
    "AdbServerCandidate",
    "AdbServerCandidateFactory",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleCoordinator",
    "AdbServerLifecycleError",
    "AdbServerEndpoint",
    "AdbServerIdentity",
    "AdbServerIdentityIssuer",
    "AdbServerFailure",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerState",
    "AdbServerStateStatus",
    "AdbServerStateStore",
    "AdbServerStateView",
    "AdbServerStateWriter",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
]
