from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServer


@runtime_checkable
class AdbServerStateView(Protocol):
    """Read-only current ADB server lifetime projection for one runtime."""

    @property
    def current(self) -> AdbServer | None: ...

    @property
    def latest_epoch(self) -> int | None: ...


@runtime_checkable
class AdbServerStateWriter(Protocol):
    """Commit exact ADB server lifetime transitions for one runtime."""

    def activate(self, server: AdbServer) -> bool: ...

    def retire(self, server: AdbServer) -> bool: ...


class AdbServerState(AdbServerStateView, AdbServerStateWriter):
    """Thread-safe authoritative current-server state for one runtime.

    A server lifetime must retire before a newer one can become current.  Epochs never move
    backwards, and an already-retired lifetime cannot be resurrected.  The state deliberately
    stores only runtime reality; desired-running intent, retry policy, and recovery episodes
    belong to supervision.
    """

    def __init__(self, initial: AdbServer | None = None) -> None:
        if initial is not None and not isinstance(initial, AdbServer):
            raise TypeError("initial must be AdbServer or None")
        self._lock = Lock()
        self._current = initial
        self._latest_epoch = None if initial is None else initial.epoch

    @property
    def current(self) -> AdbServer | None:
        with self._lock:
            return self._current

    @property
    def latest_epoch(self) -> int | None:
        with self._lock:
            return self._latest_epoch

    def activate(self, server: AdbServer) -> bool:
        """Make a fresh server lifetime current when no lifetime is currently active."""

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        with self._lock:
            if self._current == server:
                return True
            if self._current is not None:
                return False
            latest_epoch = self._latest_epoch
            if latest_epoch is not None and server.epoch <= latest_epoch:
                return False
            self._current = server
            self._latest_epoch = server.epoch
            return True

    def retire(self, server: AdbServer) -> bool:
        """Clear the exact current lifetime without allowing stale retirement to win."""

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        with self._lock:
            if self._current != server:
                return False
            self._current = None
            return True


__all__ = [
    "AdbServerState",
    "AdbServerStateView",
    "AdbServerStateWriter",
]
