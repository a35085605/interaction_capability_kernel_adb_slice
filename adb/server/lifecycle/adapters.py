from __future__ import annotations

from dataclasses import dataclass

from adb._internal.subprocess import (
    normalize_executable,
    normalize_timeout,
    run_adb,
    server_args,
    server_listen_socket,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.command import AdbServerStart, AdbServerStop
from adb.server.lifecycle.creation import (
    AdbServerCreationAttempt,
    AdbServerCreationEvidence,
)
from native_attempt import NativeAttemptResult, NativeAttemptStatus, NativeCompletionScope


@dataclass(frozen=True, slots=True)
class SubprocessAdbServer:
    """Execute one endpoint-bound ADB server lifecycle command per bounded CLI attempt."""

    endpoint: AdbServerEndpoint
    executable: str = "adb"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))

    def start(self, operation: AdbServerStart) -> NativeAttemptResult:
        if not isinstance(operation, AdbServerStart):
            raise TypeError("operation must be AdbServerStart")
        if operation.endpoint != self.endpoint:
            raise ValueError("operation endpoint does not match configured ADB server endpoint")
        return run_adb(self.executable, self.timeout_seconds, [*server_args(self.endpoint), "start-server"])

    def stop(self, operation: AdbServerStop) -> NativeAttemptResult:
        if not isinstance(operation, AdbServerStop):
            raise TypeError("operation must be AdbServerStop")
        if operation.endpoint != self.endpoint:
            raise ValueError("operation endpoint does not match configured ADB server endpoint")
        return run_adb(self.executable, self.timeout_seconds, [*server_args(self.endpoint), "kill-server"])


@dataclass(frozen=True, slots=True)
class SubprocessAdbServerCreator:
    """Launch a fresh ADB server and preserve the native launcher ACK as evidence.

    Unlike ``adb start-server``, ``adb -L SOCKET server`` enters AOSP's direct
    launcher path. A zero exit follows the child's ``OK\n`` startup handshake,
    which is emitted only after the smart-socket listener has been installed.
    """

    executable: str = "adb"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))

    def create(self, endpoint: AdbServerEndpoint) -> AdbServerCreationAttempt:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        native_attempt = run_adb(
            self.executable,
            self.timeout_seconds,
            ["-L", server_listen_socket(endpoint), "server"],
        )
        if (
            native_attempt.status is NativeAttemptStatus.SUCCEEDED
            and native_attempt.completion_scope is NativeCompletionScope.PROCESS_EXIT
        ):
            evidence = AdbServerCreationEvidence.CREATED_BY_ATTEMPT
        elif native_attempt.completion_scope is NativeCompletionScope.PROCESS_EXIT:
            evidence = AdbServerCreationEvidence.NOT_CREATED
        else:
            evidence = AdbServerCreationEvidence.INDETERMINATE
        return AdbServerCreationAttempt(endpoint, evidence, native_attempt)


__all__ = ["SubprocessAdbServer", "SubprocessAdbServerCreator"]
