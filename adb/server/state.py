from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer


class AdbServerStateStatus(str, Enum):
    """Lifecycle status of the runtime-authoritative ADB server state."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True, init=False)
class AdbServerState:
    """Immutable authoritative ADB server state for one runtime.

    Inactive state retains the last endpoint and identity for stale-work fencing.
    """

    endpoint: AdbServerEndpoint | None = None
    identity: AdbServerIdentity | None = None
    status: AdbServerStateStatus = AdbServerStateStatus.INACTIVE

    def __init__(
        self,
        endpoint: AdbServerEndpoint | None = None,
        identity: AdbServerIdentity | None = None,
        status: AdbServerStateStatus | None = None,
        *,
        last_identity: AdbServerIdentity | None = None,
    ) -> None:
        if last_identity is not None:
            if not isinstance(last_identity, AdbServerIdentity):
                raise TypeError("last_identity must be AdbServerIdentity or None")
            if identity is not None and identity != last_identity:
                raise ValueError("identity and last_identity must match when both are provided")
            identity = last_identity
        if status is None:
            status = (
                AdbServerStateStatus.ACTIVE
                if endpoint is not None
                else AdbServerStateStatus.INACTIVE
            )
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.endpoint is not None and not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        if self.identity is not None and not isinstance(
            self.identity, AdbServerIdentity
        ):
            raise TypeError("identity must be AdbServerIdentity or None")
        if not isinstance(self.status, AdbServerStateStatus):
            raise TypeError("status must be AdbServerStateStatus")
        if (self.endpoint is None) != (self.identity is None):
            raise ValueError("server endpoint and identity must be present together")
        if self.status is AdbServerStateStatus.ACTIVE and self.endpoint is None:
            raise ValueError("active server state must have an endpoint and identity")

    @property
    def active(self) -> bool:
        """Whether an authoritative server endpoint is currently active."""

        return self.status is AdbServerStateStatus.ACTIVE

    @property
    def current_identity(self) -> AdbServerIdentity | None:
        """Return the current authoritative server identity, if visible."""

        return self.identity if self.active else None

    @property
    def last_identity(self) -> AdbServerIdentity | None:
        """Compatibility alias for the retained identity watermark."""

        return self.identity

    @property
    def server(self) -> AdbServerIdentity | None:
        """Return the active authoritative server identity, if present."""

        return self.current_identity


@dataclass(frozen=True, slots=True)
class AdbServerActivated:
    """Evidence that an activation transition committed this authoritative state."""

    state: AdbServerState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbServerState):
            raise TypeError("state must be AdbServerState")
        if not self.state.active:
            raise ValueError("activated result requires active server state")

    @property
    def server(self) -> AdbServerIdentity:
        """Return the server identity made authoritative by this activation."""

        server = self.state.current_identity
        assert server is not None
        return server


@dataclass(frozen=True, slots=True)
class AdbServerActivationStateConflict:
    """Evidence that activation lost its expected authoritative-state fence."""

    state: AdbServerState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbServerState):
            raise TypeError("state must be AdbServerState")


AdbServerActivationResult: TypeAlias = (
    AdbServerActivated | AdbServerActivationStateConflict
)


@dataclass(frozen=True, slots=True)
class AdbServerDeactivated:
    """Evidence that a deactivation transition committed this authoritative state."""

    state: AdbServerState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbServerState):
            raise TypeError("state must be AdbServerState")
        if self.state.active:
            raise ValueError("deactivated result requires inactive server state")
        if self.state.endpoint is None or self.state.identity is None:
            raise ValueError(
                "deactivated result requires preserved endpoint and identity metadata"
            )

    @property
    def server(self) -> AdbServerIdentity:
        """Return the server identity retired by this deactivation."""

        server = self.state.identity
        assert server is not None
        return server


@dataclass(frozen=True, slots=True)
class AdbServerDeactivationStateConflict:
    """Evidence that deactivation lost its expected active-server fence."""

    state: AdbServerState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbServerState):
            raise TypeError("state must be AdbServerState")


AdbServerDeactivationResult: TypeAlias = (
    AdbServerDeactivated | AdbServerDeactivationStateConflict
)


@runtime_checkable
class AdbServerStateView(Protocol):
    """Authoritative endpoint and server-identity view for one runtime."""

    @property
    def endpoint(self) -> AdbServerEndpoint | None: ...

    @property
    def identity(self) -> AdbServerIdentity | None: ...

    @property
    def status(self) -> AdbServerStateStatus: ...

    @property
    def active(self) -> bool: ...

    @property
    def current_identity(self) -> AdbServerIdentity | None: ...

    def snapshot(self) -> AdbServerState: ...


@runtime_checkable
class AdbServerStateWriter(Protocol):
    """Apply authoritative server activation and deactivation transitions."""

    def activate(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult: ...

    def deactivate(self, expected: AdbServerIdentity) -> AdbServerDeactivationResult: ...


class AdbServerStateStore(AdbServerStateView, AdbServerStateWriter):
    """Thread-safe authority for server state transitions and activation identity issuance."""

    def __init__(self, initial: AdbServerState | None = None) -> None:
        if initial is None:
            state = AdbServerState()
        elif isinstance(initial, AdbServerState):
            state = initial
        else:
            raise TypeError("initial must be AdbServerState or None")
        self._lock = Lock()
        self._state = state
        self._identity_issuer = AdbServerIdentityIssuer(after=state.identity)

    @property
    def state(self) -> AdbServerState:
        """Atomically return the current immutable authoritative state value."""

        with self._lock:
            return self._state

    @property
    def endpoint(self) -> AdbServerEndpoint | None:
        return self.state.endpoint

    @property
    def identity(self) -> AdbServerIdentity | None:
        return self.state.identity

    @property
    def status(self) -> AdbServerStateStatus:
        return self.state.status

    @property
    def active(self) -> bool:
        return self.state.active

    @property
    def current_identity(self) -> AdbServerIdentity | None:
        return self.state.current_identity

    @property
    def last_identity(self) -> AdbServerIdentity | None:
        """Compatibility alias for :attr:`identity`."""

        return self.identity

    @property
    def current(self) -> AdbServerIdentity | None:
        """Compatibility alias for :attr:`current_identity`."""

        return self.current_identity

    def snapshot(self) -> AdbServerState:
        """Atomically capture the current immutable authoritative state value."""

        return self.state

    def activate(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        """Activate ``endpoint`` when its authority-identity basis is current.

        A fresh server identity is issued after the activation fence succeeds, immediately
        before the active state is committed.
        """

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if expected is not None and not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity or None")

        with self._lock:
            current = self._state
            if current.active or current.identity != expected:
                return AdbServerActivationStateConflict(current)

            identity = self._identity_issuer.issue()
            next_state = AdbServerState(
                endpoint,
                identity,
                AdbServerStateStatus.ACTIVE,
            )
            self._state = next_state
            return AdbServerActivated(next_state)

    def deactivate(self, expected: AdbServerIdentity) -> AdbServerDeactivationResult:
        """Make ``expected`` inactive while preserving endpoint and identity watermark."""

        if not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity")

        with self._lock:
            current = self._state
            if current.current_identity != expected:
                return AdbServerDeactivationStateConflict(current)
            next_state = AdbServerState(
                endpoint=current.endpoint,
                identity=current.identity,
                status=AdbServerStateStatus.INACTIVE,
            )
            self._state = next_state
            return AdbServerDeactivated(next_state)


__all__ = [
    "AdbServerActivated",
    "AdbServerActivationStateConflict",
    "AdbServerActivationResult",
    "AdbServerDeactivated",
    "AdbServerDeactivationStateConflict",
    "AdbServerDeactivationResult",
    "AdbServerState",
    "AdbServerStateStatus",
    "AdbServerStateStore",
    "AdbServerStateView",
    "AdbServerStateWriter",
]
