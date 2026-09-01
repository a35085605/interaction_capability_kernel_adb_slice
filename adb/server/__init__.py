"""ADB server endpoint, lifetime, lifecycle, failure, and status contracts."""

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
from adb.server.epoch import ServerEpoch, ServerEpochSequence
from adb.server.lifetime import AdbServerLifetime
from adb.server.state import (
    AdbServerState,
    AdbServerStateSnapshot,
    AdbServerStateTransition,
    AdbServerStateView,
    AdbServerStateWriter,
)
from adb.server.address import AdbServerTcpAddress
from adb.aosp.server.status import AdbMdnsBackend, AdbServerStatus, AdbServerStatusReader, AdbUsbBackend
from adb.server.lifecycle.control.subprocess import SubprocessAdbServerBackend

__all__ = [
    "AdbServerBackend",
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
    "AdbServerTcpAddress",
    "ServerEpoch",
    "ServerEpochSequence",
    "AdbServerFailure",
    "AdbServerLifetime",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerState",
    "AdbServerStateSnapshot",
    "AdbServerStateTransition",
    "AdbServerStateView",
    "AdbServerStateWriter",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
    "AdbUsbBackend",
    "SubprocessAdbServerBackend",
]
