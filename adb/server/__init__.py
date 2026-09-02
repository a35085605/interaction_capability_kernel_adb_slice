"""ADB server endpoint, identity, lifecycle, failure, and status contracts."""

from adb.server.availability import AdbServerUnavailableError

from adb.server.lifecycle.control.provisioner import AdbServerProvisioner
from adb.server.lifecycle.control.retirer import AdbServerRetirer
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
    AdbServerProvisionResult,
    AdbServerProvisioned,
)
from adb.server.lifecycle.control.errors import AdbServerControlError
from adb.server.lifecycle.control.backend import AdbServerBackend
from adb.server.lifecycle.transaction import (
    AdbServerProvisionCommitted,
    AdbServerProvisionTransactionResult,
)
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
    AdbServerActivationRejected,
    AdbServerActivationResult,
    AdbServerDeactivated,
    AdbServerDeactivationRejected,
    AdbServerDeactivationResult,
    AdbServerState,
    AdbServerStateStatus,
    AdbServerStateStore,
    AdbServerStateView,
    AdbServerStateWriter,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.aosp.model.server_status import AdbMdnsBackend, AdbServerStatus, AdbUsbBackend
from adb.adapters.aosp.server_status import AdbServerStatusReader
from adb.server.lifecycle.control.subprocess import SubprocessAdbServerBackend

__all__ = [
    "AdbServerActivated",
    "AdbServerActivationRejected",
    "AdbServerActivationResult",
    "AdbServerBackend",
    "AdbServerDeactivated",
    "AdbServerDeactivationRejected",
    "AdbServerDeactivationResult",
    "AdbMdnsBackend",
    "AdbServerConnectionFailure",
    "AdbServerControlError",
    "AdbServerProvisioner",
    "AdbServerRetirer",
    "AdbServerProvisionDeferred",
    "AdbServerProvisionFailed",
    "AdbServerProvisionResult",
    "AdbServerProvisioned",
    "AdbServerProvisionCommitted",
    "AdbServerProvisionTransactionResult",
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
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
    "AdbUsbBackend",
    "SubprocessAdbServerBackend",
]
