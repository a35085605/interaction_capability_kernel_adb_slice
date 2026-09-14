from __future__ import annotations

from collections.abc import Callable
import subprocess

from adb._subprocess import normalize_executable, normalize_timeout
from networking import TcpEndpoint


_Runner = Callable[..., subprocess.CompletedProcess[str]]


def _normalize_address(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("ADB connect address must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("ADB connect address cannot be empty")
    return normalized


class AospAdbCliClient:
    """Execute AOSP ``adb`` host commands against one explicit server endpoint."""

    def __init__(
        self,
        executable: str = "adb",
        timeout_seconds: float = 10.0,
        *,
        _runner: _Runner = subprocess.run,
    ) -> None:
        if not callable(_runner):
            raise TypeError("_runner must be callable")
        self.executable = normalize_executable(executable)
        self.timeout_seconds = normalize_timeout(timeout_seconds)
        self._runner = _runner

    def connect(
        self,
        server_endpoint: TcpEndpoint,
        address: str,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(server_endpoint, "connect", address)

    def disconnect(
        self,
        server_endpoint: TcpEndpoint,
        address: str,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(server_endpoint, "disconnect", address)

    def _run(
        self,
        server_endpoint: TcpEndpoint,
        command: str,
        address: str,
    ) -> subprocess.CompletedProcess[str]:
        if not isinstance(server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")
        normalized_address = _normalize_address(address)
        return self._runner(
            [
                self.executable,
                "-H",
                server_endpoint.host,
                "-P",
                str(server_endpoint.port),
                command,
                normalized_address,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_seconds,
        )


__all__ = ["AospAdbCliClient"]
