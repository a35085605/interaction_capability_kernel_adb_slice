from __future__ import annotations

import subprocess
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.model import AdbServerEndpoint


class AdbServerNativeError(RuntimeError):
    """Base error for process-owned native ADB server lifecycle failures."""


class AdbServerCloseError(AdbServerNativeError):
    """An owned native ADB server process could not be proven terminated."""


@runtime_checkable
class AdbServerNativeHandle(Protocol):
    """Exact native lifetime handle returned by one successful launch.

    The handle is the authority for both ownership identity and teardown. Implementations
    must never represent a pre-existing ADB listener that was merely discovered by endpoint.
    """

    @property
    def endpoint(self) -> AdbServerEndpoint: ...

    @property
    def active(self) -> bool: ...

    def close(self) -> None:
        """Terminate this exact native lifetime, or raise when termination is not proven."""
        ...


class _SubprocessAdbServerHandle:
    """Exact foreground ADB server child process owned by this Python process."""

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        process: subprocess.Popen[bytes],
        shutdown_timeout_seconds: float,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        self._endpoint = endpoint
        self._process = process
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._lock = Lock()
        self._closed = False

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._closed and self._process.poll() is None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._process.poll() is not None:
                self._closed = True
                return

            try:
                self._process.terminate()
            except OSError as exc:
                if self._process.poll() is None:
                    raise AdbServerCloseError(
                        f"failed to terminate owned ADB server at {self.endpoint.host}:"
                        f"{self.endpoint.port}: {exc}"
                    ) from exc

            try:
                self._process.wait(timeout=self._shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError as exc:
                    if self._process.poll() is None:
                        raise AdbServerCloseError(
                            f"failed to kill owned ADB server at {self.endpoint.host}:"
                            f"{self.endpoint.port}: {exc}"
                        ) from exc
                try:
                    self._process.wait(timeout=self._shutdown_timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    raise AdbServerCloseError(
                        "owned ADB server did not terminate after kill"
                    ) from exc

            if self._process.poll() is None:
                raise AdbServerCloseError("owned ADB server termination was not confirmed")
            self._closed = True


__all__ = [
    "AdbServerCloseError",
    "AdbServerNativeError",
    "AdbServerNativeHandle",
]
