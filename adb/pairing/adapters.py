from __future__ import annotations

from dataclasses import dataclass

from adb._internal.subprocess import (
    normalize_executable,
    normalize_timeout,
    run_adb,
    server_args,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.pairing.command import AdbWirelessPair
from native_attempt import NativeAttemptResult


@dataclass(frozen=True, slots=True)
class SubprocessAdbPairing:
    """Execute one endpoint-bound ADB pairing command per bounded CLI attempt."""

    endpoint: AdbServerEndpoint
    executable: str = "adb"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))

    def pair(self, operation: AdbWirelessPair) -> NativeAttemptResult:
        if not isinstance(operation, AdbWirelessPair):
            raise TypeError("operation must be AdbWirelessPair")
        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.endpoint), "pair", operation.address],
            input_text=f"{operation.pairing_code}\n",
        )


__all__ = ["SubprocessAdbPairing"]
