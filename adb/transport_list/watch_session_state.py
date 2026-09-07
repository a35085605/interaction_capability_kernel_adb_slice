from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.transport_list.session_identity import AdbTransportListSessionIdentity


class AdbTransportListWatchSessionStateStatus(str, Enum):
    """Lifecycle status of the authoritative transport-list watch session."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListWatchSessionState:
    """Immutable authoritative transport-list watch-session state.

    Inactive state retains the last session identity as a stale-work CAS watermark.
    """

    identity: AdbTransportListSessionIdentity | None = None
    status: AdbTransportListWatchSessionStateStatus = (
        AdbTransportListWatchSessionStateStatus.INACTIVE
    )

    def __init__(
        self,
        identity: AdbTransportListSessionIdentity | None = None,
        status: AdbTransportListWatchSessionStateStatus | None = None,
    ) -> None:
        if status is None:
            status = (
                AdbTransportListWatchSessionStateStatus.ACTIVE
                if identity is not None
                else AdbTransportListWatchSessionStateStatus.INACTIVE
            )
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.identity is not None and not isinstance(
            self.identity, AdbTransportListSessionIdentity
        ):
            raise TypeError("identity must be AdbTransportListSessionIdentity or None")
        if not isinstance(self.status, AdbTransportListWatchSessionStateStatus):
            raise TypeError("status must be AdbTransportListWatchSessionStateStatus")
        if (
            self.status is AdbTransportListWatchSessionStateStatus.ACTIVE
            and self.identity is None
        ):
            raise ValueError("active watch-session state must have an identity")

    @property
    def active(self) -> bool:
        """Whether a transport-list watch session is currently authoritative."""

        return self.status is AdbTransportListWatchSessionStateStatus.ACTIVE

    @property
    def current_identity(self) -> AdbTransportListSessionIdentity | None:
        """Return the currently authoritative watch-session identity, if active."""

        return self.identity if self.active else None


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchSessionActivated:
    """Evidence that a watch-session activation committed this authoritative state."""

    state: AdbTransportListWatchSessionState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListWatchSessionState):
            raise TypeError("state must be AdbTransportListWatchSessionState")
        if not self.state.active:
            raise ValueError("activated result requires active watch-session state")

    @property
    def session(self) -> AdbTransportListSessionIdentity:
        identity = self.state.current_identity
        assert identity is not None
        return identity


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchSessionActivationStateConflict:
    """Evidence that activation lost its expected watch-session identity fence."""

    state: AdbTransportListWatchSessionState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListWatchSessionState):
            raise TypeError("state must be AdbTransportListWatchSessionState")


AdbTransportListWatchSessionActivationResult: TypeAlias = (
    AdbTransportListWatchSessionActivated
    | AdbTransportListWatchSessionActivationStateConflict
)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchSessionDeactivated:
    """Evidence that a watch-session deactivation committed this authoritative state."""

    state: AdbTransportListWatchSessionState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListWatchSessionState):
            raise TypeError("state must be AdbTransportListWatchSessionState")
        if self.state.active:
            raise ValueError("deactivated result requires inactive watch-session state")
        if self.state.identity is None:
            raise ValueError("deactivated result requires preserved session identity metadata")

    @property
    def session(self) -> AdbTransportListSessionIdentity:
        identity = self.state.identity
        assert identity is not None
        return identity


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchSessionDeactivationStateConflict:
    """Evidence that deactivation lost its expected active-session identity fence."""

    state: AdbTransportListWatchSessionState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListWatchSessionState):
            raise TypeError("state must be AdbTransportListWatchSessionState")


AdbTransportListWatchSessionDeactivationResult: TypeAlias = (
    AdbTransportListWatchSessionDeactivated
    | AdbTransportListWatchSessionDeactivationStateConflict
)


@runtime_checkable
class AdbTransportListWatchSessionStateView(Protocol):
    """Read the authoritative transport-list watch-session identity and status."""

    @property
    def identity(self) -> AdbTransportListSessionIdentity | None: ...

    @property
    def status(self) -> AdbTransportListWatchSessionStateStatus: ...

    @property
    def active(self) -> bool: ...

    @property
    def current_identity(self) -> AdbTransportListSessionIdentity | None: ...

    def snapshot(self) -> AdbTransportListWatchSessionState: ...


@runtime_checkable
class AdbTransportListWatchSessionStateWriter(Protocol):
    """Apply authoritative watch-session activation and deactivation transitions."""

    def activate(
        self,
        identity: AdbTransportListSessionIdentity,
        *,
        expected: AdbTransportListSessionIdentity | None,
    ) -> AdbTransportListWatchSessionActivationResult: ...

    def deactivate(
        self,
        expected: AdbTransportListSessionIdentity,
    ) -> AdbTransportListWatchSessionDeactivationResult: ...


class AdbTransportListWatchSessionStateStore(
    AdbTransportListWatchSessionStateView,
    AdbTransportListWatchSessionStateWriter,
):
    """Thread-safe authority for transport-list watch-session state transitions."""

    def __init__(self, initial: AdbTransportListWatchSessionState | None = None) -> None:
        if initial is None:
            state = AdbTransportListWatchSessionState()
        elif isinstance(initial, AdbTransportListWatchSessionState):
            state = initial
        else:
            raise TypeError("initial must be AdbTransportListWatchSessionState or None")
        self._lock = Lock()
        self._state = state

    @property
    def state(self) -> AdbTransportListWatchSessionState:
        with self._lock:
            return self._state

    @property
    def identity(self) -> AdbTransportListSessionIdentity | None:
        return self.state.identity

    @property
    def status(self) -> AdbTransportListWatchSessionStateStatus:
        return self.state.status

    @property
    def active(self) -> bool:
        return self.state.active

    @property
    def current_identity(self) -> AdbTransportListSessionIdentity | None:
        return self.state.current_identity

    def snapshot(self) -> AdbTransportListWatchSessionState:
        return self.state

    def activate(
        self,
        identity: AdbTransportListSessionIdentity,
        *,
        expected: AdbTransportListSessionIdentity | None,
    ) -> AdbTransportListWatchSessionActivationResult:
        """Make ``identity`` authoritative when the retained identity fence is current."""

        if not isinstance(identity, AdbTransportListSessionIdentity):
            raise TypeError("identity must be AdbTransportListSessionIdentity")
        if expected is not None and not isinstance(
            expected, AdbTransportListSessionIdentity
        ):
            raise TypeError("expected must be AdbTransportListSessionIdentity or None")

        with self._lock:
            current = self._state
            if current.active or current.identity is not expected:
                return AdbTransportListWatchSessionActivationStateConflict(current)

            next_state = AdbTransportListWatchSessionState(
                identity,
                AdbTransportListWatchSessionStateStatus.ACTIVE,
            )
            self._state = next_state
            return AdbTransportListWatchSessionActivated(next_state)

    def deactivate(
        self,
        expected: AdbTransportListSessionIdentity,
    ) -> AdbTransportListWatchSessionDeactivationResult:
        """Make ``expected`` inactive while preserving its identity watermark."""

        if not isinstance(expected, AdbTransportListSessionIdentity):
            raise TypeError("expected must be AdbTransportListSessionIdentity")

        with self._lock:
            current = self._state
            if current.current_identity is not expected:
                return AdbTransportListWatchSessionDeactivationStateConflict(current)
            next_state = AdbTransportListWatchSessionState(
                identity=current.identity,
                status=AdbTransportListWatchSessionStateStatus.INACTIVE,
            )
            self._state = next_state
            return AdbTransportListWatchSessionDeactivated(next_state)


__all__ = [
    "AdbTransportListWatchSessionActivated",
    "AdbTransportListWatchSessionActivationResult",
    "AdbTransportListWatchSessionActivationStateConflict",
    "AdbTransportListWatchSessionDeactivated",
    "AdbTransportListWatchSessionDeactivationResult",
    "AdbTransportListWatchSessionDeactivationStateConflict",
    "AdbTransportListWatchSessionState",
    "AdbTransportListWatchSessionStateStatus",
    "AdbTransportListWatchSessionStateStore",
    "AdbTransportListWatchSessionStateView",
    "AdbTransportListWatchSessionStateWriter",
]
