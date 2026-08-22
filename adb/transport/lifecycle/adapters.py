from __future__ import annotations

from dataclasses import dataclass

from adb._internal.subprocess import (
    normalize_executable,
    normalize_timeout,
    run_adb,
    selector_args,
    server_args,
)
from adb.server.identity import AdbServer
from adb.transport.lifecycle.command import (
    AdbDeviceSideReconnect,
    AdbOfflineTransportsReconnect,
    AdbTcpConnect,
    AdbTcpDisconnect,
    AdbTransportReconnect,
)
from native_attempt import NativeAttemptResult


@dataclass(frozen=True, slots=True)
class SubprocessAdbTransport:
    """Execute one server-bound transport lifecycle command per bounded CLI attempt."""

    server: AdbServer
    executable: str = "adb"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))

    def connect(self, operation: AdbTcpConnect) -> NativeAttemptResult:
        if not isinstance(operation, AdbTcpConnect):
            raise TypeError("operation must be AdbTcpConnect")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.server.endpoint), "connect", operation.address.value],
        )

    def disconnect(self, operation: AdbTcpDisconnect) -> NativeAttemptResult:
        if not isinstance(operation, AdbTcpDisconnect):
            raise TypeError("operation must be AdbTcpDisconnect")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.server.endpoint), "disconnect", operation.address.value],
        )

    def reconnect(self, operation: AdbTransportReconnect) -> NativeAttemptResult:
        if not isinstance(operation, AdbTransportReconnect):
            raise TypeError("operation must be AdbTransportReconnect")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.server.endpoint), *selector_args(operation.selector), "reconnect"],
        )

    def reconnect_device(self, operation: AdbDeviceSideReconnect) -> NativeAttemptResult:
        if not isinstance(operation, AdbDeviceSideReconnect):
            raise TypeError("operation must be AdbDeviceSideReconnect")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [
                *server_args(self.server.endpoint),
                *selector_args(operation.selector),
                "reconnect",
                "device",
            ],
        )

    def reconnect_offline(self, operation: AdbOfflineTransportsReconnect) -> NativeAttemptResult:
        if not isinstance(operation, AdbOfflineTransportsReconnect):
            raise TypeError("operation must be AdbOfflineTransportsReconnect")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.server.endpoint), "reconnect", "offline"],
        )


__all__ = ["SubprocessAdbTransport"]
