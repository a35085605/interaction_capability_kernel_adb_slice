from __future__ import annotations

from dataclasses import dataclass
import subprocess

from adb.adapters.subprocess.command import normalize_executable, normalize_timeout
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
from networking import TcpAddress


def _exception_diagnostic(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


def _completed_diagnostic(completed: subprocess.CompletedProcess[str]) -> str | None:
    return "\n".join(
        part
        for part in (completed.stdout.strip(), completed.stderr.strip())
        if part
    ) or None


def _run_control_command(
    executable: str,
    timeout_seconds: float,
    args: list[str],
) -> AdbTcpTransportControlFailure | None:
    try:
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return AdbTcpTransportControlTimedOut(diagnostic=_exception_diagnostic(exc))
    except OSError as exc:
        return AdbTcpTransportControlFailed(diagnostic=_exception_diagnostic(exc))

    if completed.returncode == 0:
        return None

    diagnostic = _completed_diagnostic(completed)
    if diagnostic is None:
        diagnostic = f"ADB subprocess exited with code {completed.returncode}"
    return AdbTcpTransportControlFailed(diagnostic=diagnostic)


@dataclass(frozen=True, slots=True)
class SubprocessAdbTransportController:
    """Execute transport lifecycle commands through the configured server address."""

    server_address: TcpAddress
    executable: str = "adb"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.server_address, TcpAddress):
            raise TypeError("server_address must be TcpAddress")
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))

    def _args(self, command: str, address: AdbConnectAddress) -> list[str]:
        return [
            "-H",
            self.server_address.host,
            "-P",
            str(self.server_address.port),
            command,
            address.value,
        ]

    def connect(self, address: AdbConnectAddress) -> AdbTcpTransportConnectResult:
        if not isinstance(address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")
        failure = _run_control_command(
            self.executable,
            self.timeout_seconds,
            self._args("connect", address),
        )
        if failure is not None:
            return failure
        return AdbTcpTransportConnectCommandSucceeded()

    def disconnect(self, address: AdbConnectAddress) -> AdbTcpTransportDisconnectResult:
        if not isinstance(address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")
        failure = _run_control_command(
            self.executable,
            self.timeout_seconds,
            self._args("disconnect", address),
        )
        if failure is not None:
            return failure
        return AdbTcpTransportDisconnectCommandSucceeded()


__all__ = ["SubprocessAdbTransportController"]
