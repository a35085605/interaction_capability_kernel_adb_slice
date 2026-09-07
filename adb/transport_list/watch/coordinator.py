from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from eventing import EventPublisher
from networking import TcpAddress
from adb.transport_list.session_identity import AdbTransportListSessionIdentity
from adb.transport_list.watch.backend import (
    AdbTransportListWatchBackend,
    AdbTransportListWatchBackendAcquireDeferred,
    AdbTransportListWatchBackendAcquireRevoked,
    AdbTransportListWatchBackendAlreadyOpen,
    AdbTransportListWatchBackendOpened,
    AdbTransportListWatchBackendOpenFailed,
    AdbTransportListWatchBackendOpenResult,
)
from adb.transport_list.watch_session_state import (
    AdbTransportListWatchSessionActivated,
    AdbTransportListWatchSessionActivationResult,
    AdbTransportListWatchSessionActivationStateConflict,
    AdbTransportListWatchSessionDeactivated,
    AdbTransportListWatchSessionDeactivationResult,
    AdbTransportListWatchSessionDeactivationStateConflict,
    AdbTransportListWatchSessionState,
    AdbTransportListWatchSessionStateView,
    AdbTransportListWatchSessionStateWriter,
)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAlreadyActive:
    """Evidence that provision linearized against an already-active watch session."""

    session: AdbTransportListSessionIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAlreadyInactive:
    """Evidence that unfenced retirement found no authoritative watch session."""

    state: AdbTransportListWatchSessionState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListWatchSessionState):
            raise TypeError("state must be AdbTransportListWatchSessionState")
        if self.state.active:
            raise ValueError("already-inactive result requires inactive watch-session state")


AdbTransportListWatchProvisionResult: TypeAlias = (
    tuple[AdbTransportListWatchAlreadyActive]
    | tuple[
        AdbTransportListWatchBackendAlreadyOpen
        | AdbTransportListWatchBackendOpenFailed
        | AdbTransportListWatchBackendAcquireDeferred
        | AdbTransportListWatchBackendAcquireRevoked
    ]
    | tuple[
        AdbTransportListWatchBackendOpened,
        AdbTransportListWatchSessionActivationResult,
    ]
)
AdbTransportListWatchRetireResult: TypeAlias = (
    AdbTransportListWatchAlreadyInactive
    | AdbTransportListWatchSessionDeactivationResult
)


class AdbTransportListWatchLifecycleCoordinator:
    """Coordinate watch-backend effects with fenced authoritative session state."""

    def __init__(
        self,
        state: AdbTransportListWatchSessionStateView,
        *,
        writer: AdbTransportListWatchSessionStateWriter,
        backend: AdbTransportListWatchBackend,
        endpoint: TcpAddress,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(state, AdbTransportListWatchSessionStateView):
            raise TypeError("state must satisfy AdbTransportListWatchSessionStateView")
        if not isinstance(writer, AdbTransportListWatchSessionStateWriter):
            raise TypeError("writer must satisfy AdbTransportListWatchSessionStateWriter")
        if not isinstance(backend, AdbTransportListWatchBackend):
            raise TypeError("backend must satisfy AdbTransportListWatchBackend")
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._state = state
        self._writer = writer
        self._backend = backend
        self._endpoint = endpoint
        self._publisher = publisher

    @property
    def endpoint(self) -> TcpAddress:
        return self._endpoint

    def provision(
        self,
        *,
        startup_timeout_seconds: float = 5.0,
    ) -> AdbTransportListWatchProvisionResult:
        """Acquire watch authority and mirror its session identity into legacy state.

        This remains a compatibility facade while orchestration migrates to backend
        acquire/release results directly. A newly acquired backend session is released
        when the legacy activation fence is lost or activation raises.
        """

        t0 = self._state.snapshot()
        if t0.active:
            session = t0.current_identity
            assert session is not None
            return (AdbTransportListWatchAlreadyActive(session),)

        opening = self._open_backend(startup_timeout_seconds)
        if not isinstance(opening, AdbTransportListWatchBackendOpened):
            return (opening,)

        try:
            activation = self._writer.activate(
                opening.identity,
                expected=t0.identity,
            )
        except BaseException:
            self._rollback_opening(opening)
            raise

        if isinstance(activation, AdbTransportListWatchSessionActivationStateConflict):
            self._rollback_opening(opening)
        elif not isinstance(activation, AdbTransportListWatchSessionActivated):
            self._rollback_opening(opening)
            raise TypeError("watch-session state activate() returned an unsupported result")

        result = (opening, activation)
        if (
            isinstance(activation, AdbTransportListWatchSessionActivated)
            and self._publisher is not None
        ):
            self._publisher.publish(activation)
        return result

    def _open_backend(
        self,
        startup_timeout_seconds: float,
    ) -> AdbTransportListWatchBackendOpenResult:
        opening = self._backend.acquire(
            self._endpoint,
            startup_timeout_seconds=startup_timeout_seconds,
        )
        if isinstance(
            opening,
            (
                AdbTransportListWatchBackendAlreadyOpen,
                AdbTransportListWatchBackendOpenFailed,
                AdbTransportListWatchBackendAcquireDeferred,
                AdbTransportListWatchBackendAcquireRevoked,
            ),
        ):
            return opening
        if not isinstance(opening, AdbTransportListWatchBackendOpened):
            raise TypeError("watch backend acquire() returned an unsupported result")
        return opening

    def _rollback_opening(self, opening: AdbTransportListWatchBackendOpened) -> None:
        if not isinstance(opening, AdbTransportListWatchBackendOpened):
            raise TypeError("opening must be AdbTransportListWatchBackendOpened")
        self._backend.release(opening.generation)

    def retire(
        self,
        *,
        expected_session: AdbTransportListSessionIdentity | None = None,
    ) -> AdbTransportListWatchRetireResult:
        """Retire legacy session state and release matching backend authority.

        ``expected_session`` fences stale retirement requests. Backend generation remains
        the canonical lifecycle fence; this facade mirrors deactivation for old consumers.
        """

        if expected_session is not None and not isinstance(
            expected_session, AdbTransportListSessionIdentity
        ):
            raise TypeError(
                "expected_session must be AdbTransportListSessionIdentity or None"
            )

        t0 = self._state.snapshot()
        if expected_session is None:
            session = t0.current_identity
            if session is None:
                return AdbTransportListWatchAlreadyInactive(t0)
        else:
            session = expected_session

        deactivation = self._writer.deactivate(session)
        if isinstance(
            deactivation,
            AdbTransportListWatchSessionDeactivationStateConflict,
        ):
            return deactivation
        if not isinstance(deactivation, AdbTransportListWatchSessionDeactivated):
            raise TypeError("watch-session state deactivate() returned an unsupported result")

        backend_state = self._backend.read()
        if backend_state.session_identity is session:
            self._backend.release(backend_state.generation)
        if self._publisher is not None:
            self._publisher.publish(deactivation)
        return deactivation


__all__ = [
    "AdbTransportListWatchAlreadyActive",
    "AdbTransportListWatchAlreadyInactive",
    "AdbTransportListWatchLifecycleCoordinator",
    "AdbTransportListWatchProvisionResult",
    "AdbTransportListWatchRetireResult",
]
