from __future__ import annotations

from dataclasses import dataclass

from adb.adapters.subprocess.command import (
    normalize_executable,
    normalize_timeout,
    run_adb,
    server_args,
)
from networking import TcpAddress
from adb.transport.lifecycle.control.port import AdbTcpConnect, AdbTcpDisconnect
from native_attempt import NativeAttemptResult


@dataclass(frozen=True, slots=True)
class SubprocessAdbTransportController:
    """Execute transport lifecycle commands through the configured server endpoint."""

    endpoint: TcpAddress
    executable: str = "adb"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))

    def connect(self, operation: AdbTcpConnect) -> NativeAttemptResult:
        if not isinstance(operation, AdbTcpConnect):
            raise TypeError("operation must be AdbTcpConnect")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.endpoint), "connect", operation.address.value],
        )

    def disconnect(self, operation: AdbTcpDisconnect) -> NativeAttemptResult:
        if not isinstance(operation, AdbTcpDisconnect):
            raise TypeError("operation must be AdbTcpDisconnect")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.endpoint), "disconnect", operation.address.value],
        )


__all__ = ["SubprocessAdbTransportController"]
