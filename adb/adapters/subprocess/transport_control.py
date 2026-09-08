from __future__ import annotations

from dataclasses import dataclass

from adb.adapters.subprocess.command import (
    normalize_executable,
    normalize_timeout,
    run_adb,
    server_args,
)
from networking import TcpAddress
from adb.transport.address import AdbConnectAddress
from adb.transport.lifecycle.control.result import (
    AdbTcpTransportConnectCommandSucceeded,
    AdbTcpTransportConnectResult,
    AdbTcpTransportControlFailed,
    AdbTcpTransportControlFailure,
    AdbTcpTransportControlTimedOut,
    AdbTcpTransportDisconnectCommandSucceeded,
    AdbTcpTransportDisconnectResult,
)
from native_attempt import NativeAttemptResult, NativeAttemptStatus


def _attempt_diagnostic(attempt: NativeAttemptResult) -> str | None:
    if attempt.diagnostic is not None:
        return attempt.diagnostic
    if attempt.native_code is not None:
        return f"native attempt code: {attempt.native_code}"
    return None


def _control_failure_from_attempt(
    attempt: NativeAttemptResult,
) -> AdbTcpTransportControlFailure | None:
    if not isinstance(attempt, NativeAttemptResult):
        raise TypeError("attempt must be NativeAttemptResult")
    if attempt.status is NativeAttemptStatus.SUCCEEDED:
        return None
    diagnostic = _attempt_diagnostic(attempt)
    if attempt.status is NativeAttemptStatus.TIMED_OUT:
        return AdbTcpTransportControlTimedOut(diagnostic=diagnostic)
    if attempt.status is NativeAttemptStatus.FAILED:
        return AdbTcpTransportControlFailed(diagnostic=diagnostic)
    raise TypeError("unsupported native attempt status")


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

    def connect(self, address: AdbConnectAddress) -> AdbTcpTransportConnectResult:
        if not isinstance(address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")
        attempt = run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.endpoint), "connect", address.value],
        )
        failure = _control_failure_from_attempt(attempt)
        if failure is not None:
            return failure
        return AdbTcpTransportConnectCommandSucceeded()

    def disconnect(self, address: AdbConnectAddress) -> AdbTcpTransportDisconnectResult:
        if not isinstance(address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")
        attempt = run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.endpoint), "disconnect", address.value],
        )
        failure = _control_failure_from_attempt(attempt)
        if failure is not None:
            return failure
        return AdbTcpTransportDisconnectCommandSucceeded()


__all__ = ["SubprocessAdbTransportController"]
