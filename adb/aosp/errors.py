from __future__ import annotations


class AdbError(RuntimeError):
    """Base error for low-level ADB protocol and service failures."""


class AdbServerConnectionError(AdbError):
    """The configured ADB server smart-socket session could not be established or used."""


class AdbTimeoutError(AdbServerConnectionError):
    """An ADB server smart-socket operation exceeded its configured timeout."""


class AdbProtocolError(AdbError):
    """ADB framing or payload data violated the expected protocol."""


class AdbServiceError(AdbError):
    """An ADB server or device service rejected a request."""

    def __init__(self, service: str, detail: str) -> None:
        self.service = service
        self.detail = detail
        super().__init__(f"ADB service {service!r} failed: {detail}")


__all__ = [
    "AdbError",
    "AdbProtocolError",
    "AdbServerConnectionError",
    "AdbServiceError",
    "AdbTimeoutError",
]
