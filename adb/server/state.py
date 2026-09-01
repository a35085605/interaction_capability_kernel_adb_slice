from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.epoch import ServerEpoch
from adb.server.lifetime import AdbServerLifetime


class AdbServerStateStatus(str, Enum):
    """Lifecycle status of the runtime-authoritative ADB server state."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True, init=False)
class AdbServerState:
    """Immutable authoritative ADB server state for one runtime observation.

    ``endpoint`` and ``epoch`` identify the last committed server lifetime.  Inactive states may
    preserve both values so lifecycle status does not depend on clearing endpoint metadata.
    ``epoch`` remains the committed-lifetime watermark, preventing a stale inactive observation
    from committing after an intervening server lifetime.
    """

    endpoint: AdbServerEndpoint | None = None
    epoch: ServerEpoch | None = None
    status: AdbServerStateStatus = AdbServerStateStatus.INACTIVE

    def __init__(
        self,
        endpoint: AdbServerEndpoint | None = None,
        epoch: ServerEpoch | None = None,
        status: AdbServerStateStatus | None = None,
    ) -> None:
        # Preserve the historical two-argument construction semantics: a state with an endpoint
        # was active before status became explicit, while an endpoint-less state was inactive.
        if status is None:
            status = (
                AdbServerStateStatus.ACTIVE
                if endpoint is not None
                else AdbServerStateStatus.INACTIVE
            )
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "epoch", epoch)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.endpoint is not None and not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        if self.epoch is not None and not isinstance(self.epoch, ServerEpoch):
            raise TypeError("epoch must be ServerEpoch or None")
        if not isinstance(self.status, AdbServerStateStatus):
            raise TypeError("status must be AdbServerStateStatus")
        if self.endpoint is not None and self.epoch is None:
            raise ValueError("server state with an endpoint must have an epoch")
        if self.status is AdbServerStateStatus.ACTIVE and (
            self.endpoint is None or self.epoch is None
        ):
            raise ValueError("active server state must have an endpoint and epoch")

    @property
    def active(self) -> bool:
        """Whether an authoritative server endpoint is currently active."""

        return self.status is AdbServerStateStatus.ACTIVE

    @property
    def server(self) -> AdbServerLifetime | None:
        """Project the active authoritative server lifetime, if present."""

        if self.status is AdbServerStateStatus.INACTIVE:
            return None
        endpoint = self.endpoint
        if endpoint is None:
            raise RuntimeError("active ADB server state has no endpoint")
        epoch = self.epoch
        if epoch is None:
            raise RuntimeError("active ADB server state has no epoch")
        return AdbServerLifetime(endpoint, epoch)


@runtime_checkable
class AdbServerStateView(Protocol):
    """Authoritative endpoint and lifetime-epoch view for one runtime."""

    @property
    def endpoint(self) -> AdbServerEndpoint | None: ...

    @property
    def epoch(self) -> ServerEpoch | None: ...

    @property
    def status(self) -> AdbServerStateStatus: ...

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
        expected: AdbServerState,
    ) -> AdbServerLifetime | None: ...

    def deactivate(self, expected: AdbServerLifetime) -> bool: ...


class AdbServerStateStore(AdbServerStateView, AdbServerStateWriter):
    """Thread-safe authority for one runtime's immutable :class:`AdbServerState` value."""

    def __init__(
        self,
        initial: AdbServerState | AdbServerLifetime | None = None,
    ) -> None:
        if initial is None:
            state = AdbServerState()
        elif isinstance(initial, AdbServerState):
            state = initial
        elif isinstance(initial, AdbServerLifetime):
            state = AdbServerState(
                initial.endpoint,
                initial.epoch,
                AdbServerStateStatus.ACTIVE,
            )
        else:
            raise TypeError("initial must be AdbServerState, AdbServerLifetime, or None")
        self._lock = Lock()
        self._state = state

    @property
    def state(self) -> AdbServerState:
        """Atomically return the current immutable authoritative state value."""

        with self._lock:
            return self._state

    @property
    def endpoint(self) -> AdbServerEndpoint | None:
        return self.state.endpoint

    @property
    def epoch(self) -> ServerEpoch | None:
        return self.state.epoch

    @property
    def status(self) -> AdbServerStateStatus:
        return self.state.status

    @property
    def active(self) -> bool:
        return self.state.active

    @property
    def current(self) -> AdbServerLifetime | None:
        return self.state.server

    def snapshot(self) -> AdbServerState:
        """Atomically capture the current immutable authoritative state value."""

        return self.state

    def commit(
        self,
        endpoint: AdbServerEndpoint,
        expected: AdbServerState,
    ) -> AdbServerLifetime | None:
        """Activate ``endpoint`` iff ``expected`` is the current inactive state.

        A successful compare-and-set allocates the next runtime-scoped server epoch at the same
        linearization point that makes the endpoint authoritative.
        """

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(expected, AdbServerState):
            raise TypeError("expected must be AdbServerState")

        with self._lock:
            if self._state != expected or expected.active:
                return None

            next_epoch = ServerEpoch(1 if expected.epoch is None else expected.epoch.value + 1)
            next_state = AdbServerState(
                endpoint,
                next_epoch,
                AdbServerStateStatus.ACTIVE,
            )
            self._state = next_state
            server = next_state.server
            if server is None:
                raise RuntimeError("committed ADB server state has no active server")
            return server

    def deactivate(self, expected: AdbServerLifetime) -> bool:
        """Make ``expected`` inactive while preserving its endpoint and epoch metadata."""

        if not isinstance(expected, AdbServerLifetime):
            raise TypeError("expected must be AdbServerLifetime")

        with self._lock:
            state = self._state
            if state.server != expected:
                return False
            self._state = AdbServerState(
                endpoint=state.endpoint,
                epoch=state.epoch,
                status=AdbServerStateStatus.INACTIVE,
            )
            return True


__all__ = [
    "AdbServerState",
    "AdbServerStateStatus",
    "AdbServerStateStore",
    "AdbServerStateView",
    "AdbServerStateWriter",
]
