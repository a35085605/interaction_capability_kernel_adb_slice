from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.epoch import ServerEpoch
from adb.server.lifetime import AdbServerLifetime


@dataclass(frozen=True, slots=True)
class AdbServerStateSnapshot:
    """Immutable atomic observation of one runtime's authoritative server state."""

    endpoint: AdbServerEndpoint | None
    epoch: ServerEpoch | None

    def __post_init__(self) -> None:
        if self.endpoint is not None and not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        if self.epoch is not None and not isinstance(self.epoch, ServerEpoch):
            raise TypeError("epoch must be ServerEpoch or None")
        if self.endpoint is not None and self.epoch is None:
            raise ValueError("active server state must have an epoch")

    @property
    def active(self) -> bool:
        """Whether an authoritative server endpoint is currently active."""

        return self.endpoint is not None

    @property
    def current(self) -> AdbServerLifetime | None:
        """Project the current authoritative server lifetime, if active."""

        endpoint = self.endpoint
        if endpoint is None:
            return None
        epoch = self.epoch
        if epoch is None:
            raise RuntimeError("active ADB server snapshot has no epoch")
        return AdbServerLifetime(endpoint, epoch)


@runtime_checkable
class AdbServerStateView(Protocol):
    """Authoritative endpoint and lifetime-epoch view for one runtime."""

    @property
    def endpoint(self) -> AdbServerEndpoint | None: ...

    @property
    def epoch(self) -> ServerEpoch | None: ...

    @property
    def active(self) -> bool: ...

    @property
    def current(self) -> AdbServerLifetime | None: ...


@runtime_checkable
class AdbServerStateWriter(Protocol):
    """Commit authoritative server activation and deactivation transitions."""

    def commit(
        self,
        endpoint: AdbServerEndpoint,
        expected_epoch: ServerEpoch | None,
    ) -> AdbServerLifetime | None: ...

    def deactivate(self, expected: AdbServerLifetime) -> bool: ...


class AdbServerState(AdbServerStateView, AdbServerStateWriter):
    """Thread-safe authoritative server endpoint with a committed-lifetime epoch watermark.

    The epoch advances only when an inactive state successfully commits a new endpoint.  Clearing
    the endpoint makes the state inactive while preserving the last committed epoch, preventing
    stale inactive observations from committing after an intervening server lifetime.
    """

    def __init__(self, initial: AdbServerLifetime | None = None) -> None:
        if initial is not None and not isinstance(initial, AdbServerLifetime):
            raise TypeError("initial must be AdbServerLifetime or None")
        self._lock = Lock()
        self._endpoint = None if initial is None else initial.endpoint
        self._epoch = None if initial is None else initial.epoch

    @property
    def endpoint(self) -> AdbServerEndpoint | None:
        with self._lock:
            return self._endpoint

    @property
    def epoch(self) -> ServerEpoch | None:
        with self._lock:
            return self._epoch

    @property
    def active(self) -> bool:
        with self._lock:
            return self._endpoint is not None

    @property
    def current(self) -> AdbServerLifetime | None:
        with self._lock:
            return self._current_locked()

    def snapshot(self) -> AdbServerStateSnapshot:
        """Atomically capture endpoint presence and the committed-lifetime epoch watermark."""

        with self._lock:
            return AdbServerStateSnapshot(endpoint=self._endpoint, epoch=self._epoch)

    def commit(
        self,
        endpoint: AdbServerEndpoint,
        expected_epoch: ServerEpoch | None,
    ) -> AdbServerLifetime | None:
        """Activate ``endpoint`` iff state is inactive and ``expected_epoch`` is still current.

        A successful commit allocates the next runtime-scoped server epoch at the same
        linearization point that makes the endpoint authoritative.
        """

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if expected_epoch is not None and not isinstance(expected_epoch, ServerEpoch):
            raise TypeError("expected_epoch must be ServerEpoch or None")

        with self._lock:
            if self._endpoint is not None:
                return None
            if expected_epoch != self._epoch:
                return None

            next_epoch = ServerEpoch(1 if self._epoch is None else self._epoch.value + 1)
            server = AdbServerLifetime(endpoint, next_epoch)
            self._endpoint = endpoint
            self._epoch = next_epoch
            return server

    def deactivate(self, expected: AdbServerLifetime) -> bool:
        """Make ``expected`` inactive while preserving its epoch as the lifetime watermark."""

        if not isinstance(expected, AdbServerLifetime):
            raise TypeError("expected must be AdbServerLifetime")

        with self._lock:
            current = self._current_locked()
            if current != expected:
                return False
            self._endpoint = None
            return True

    def _current_locked(self) -> AdbServerLifetime | None:
        endpoint = self._endpoint
        if endpoint is None:
            return None
        epoch = self._epoch
        if epoch is None:
            raise RuntimeError("active ADB server state has no epoch")
        return AdbServerLifetime(endpoint, epoch)


__all__ = [
    "AdbServerState",
    "AdbServerStateSnapshot",
    "AdbServerStateView",
    "AdbServerStateWriter",
]
