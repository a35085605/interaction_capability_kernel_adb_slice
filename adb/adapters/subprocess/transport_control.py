from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import subprocess

from adb._subprocess import normalize_executable, normalize_timeout
from adb.aosp.io.cli import AospAdbCliClient
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
from networking import TcpEndpoint


def _exception_diagnostic(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


def _completed_diagnostic(completed: subprocess.CompletedProcess[str]) -> str | None:
    return "\n".join(
        part
        for part in (completed.stdout.strip(), completed.stderr.strip())
        if part
    ) or None


def _command_failure(
    operation: Callable[[], subprocess.CompletedProcess[str]],
) -> AdbTcpTransportControlFailure | None:
    try:
        completed = operation()
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
    """Adapt AOSP CLI transport commands into domain lifecycle-control results."""

    server_endpoint: TcpEndpoint
    executable: str = "adb"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))

    def _client(self) -> AospAdbCliClient:
        return AospAdbCliClient(
            self.executable,
            self.timeout_seconds,
            _runner=subprocess.run,
        )

    def connect(self, address: AdbConnectAddress) -> AdbTcpTransportConnectResult:
        if not isinstance(address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")
        client = self._client()
        failure = _command_failure(
            lambda: client.connect(self.server_endpoint, address.value)
        )
        if failure is not None:
            return failure
        return AdbTcpTransportConnectCommandSucceeded()

    def disconnect(self, address: AdbConnectAddress) -> AdbTcpTransportDisconnectResult:
        if not isinstance(address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")
        client = self._client()
        failure = _command_failure(
            lambda: client.disconnect(self.server_endpoint, address.value)
        )
        if failure is not None:
            return failure
        return AdbTcpTransportDisconnectCommandSucceeded()


__all__ = ["SubprocessAdbTransportController"]
