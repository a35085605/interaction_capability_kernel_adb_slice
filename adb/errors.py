from __future__ import annotations

from adb.aosp.errors import (
    AdbError,
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
)


class AdbTransportSelectionError(AdbServiceError):
    """Base error for deterministic transport selection failures."""


class AdbTransportNotFoundError(AdbTransportSelectionError):
    """Transport-selection failure for a requested transport absent from the selected ADB server."""


class AdbTransportAmbiguousError(AdbTransportSelectionError):
    """The requested transport selector matched more than one transport."""


class AdbTransportUnavailableError(AdbTransportSelectionError):
    """Transport-selection failure for an existing transport that is currently unavailable."""


class AdbRemoteCommandError(AdbError):
    """Failure from a typed read-only remote command with a non-zero exit code."""

    def __init__(
        self,
        *,
        command_name: str,
        exit_code: int,
        stderr: bytes = b"",
    ) -> None:
        self.command_name = command_name
        self.exit_code = exit_code
        self.stderr = stderr
        detail = stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        super().__init__(
            f"ADB remote command {command_name!r} exited with {exit_code}{suffix}"
        )


__all__ = [
    "AdbError",
    "AdbProtocolError",
    "AdbRemoteCommandError",
    "AdbServerConnectionError",
    "AdbServiceError",
    "AdbTimeoutError",
    "AdbTransportAmbiguousError",
    "AdbTransportNotFoundError",
    "AdbTransportSelectionError",
    "AdbTransportUnavailableError",
]
