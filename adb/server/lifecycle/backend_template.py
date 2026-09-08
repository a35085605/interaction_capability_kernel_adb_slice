from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event, Lock
from typing import Generic, Protocol, TypeVar, runtime_checkable

from eventing import EventPublisher
from networking import TcpAddress
from adb.cleanup import CleanupHandoff, CleanupHandoffError, CleanupSink
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerState
from adb.server.lifecycle.errors import AdbServerLifecycleConsistencyError
from adb.server.lifecycle.events import AdbServerActivated, AdbServerDeactivated
from adb.server.lifecycle.backend import (
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireRevoked,
    AdbServerBackendAcquireResult,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendReleased,
    AdbServerBackendReleaseInactive,
    AdbServerBackendReleaseMismatch,
    AdbServerBackendReleaseResult,
)


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


def _cleanup_exception_diagnostic(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


@runtime_checkable
class AdbServerBackendEventPublisherBinding(Protocol):
    """Optional capability for binding backend state-transition notifications."""

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher used for non-authoritative state-transition notifications."""
        ...


class AdbServerBackendAcquireError(RuntimeError):
    """Expected failure while obtaining a backend acquisition handle."""

    def __init__(
        self,
        diagnostic: str,
        cleanup_handoff: CleanupHandoff | None = None,
    ) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        if cleanup_handoff is not None and not isinstance(cleanup_handoff, CleanupHandoff):
            raise TypeError("cleanup_handoff must be CleanupHandoff or None")
        self.cleanup_handoff = cleanup_handoff
        super().__init__(self.diagnostic)


class AdbServerBackendAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured server generation was released."""

    def __init__(self, cleanup_handoff: CleanupHandoff | None = None) -> None:
        if cleanup_handoff is not None and not isinstance(cleanup_handoff, CleanupHandoff):
            raise TypeError("cleanup_handoff must be CleanupHandoff or None")
        self.cleanup_handoff = cleanup_handoff
        super().__init__("ADB server backend acquisition was interrupted")


HandleT = TypeVar("HandleT")


@dataclass(frozen=True, slots=True)
class _AdbServerBackendOwnership(Generic[HandleT]):
    handle: HandleT
    acquisition: AdbServerBackendAcquired


@dataclass(frozen=True, slots=True)
class _AdbServerBackendPendingAcquire:
    generation: AdbServerGeneration
    cancellation: Event


class AdbServerBackendTemplate(Generic[HandleT], ABC):
    """Template for one current server generation and its optional usable endpoint.

    The backend receives its first generation during construction. Each acquisition captures
    that current generation before implementation-specific blocking work begins. Failed or
    retried acquisitions do not change it. A matching release advances the generation at the
    logical revocation point, before cancellation or physical handle cleanup completes.

    Normal physical cleanup remains backend-owned. When normal cleanup cannot be confirmed,
    ownership is synchronously transferred to the configured ``CleanupSink`` before the backend
    abandons the resource. Cleanup transfer never appears in lifecycle results.
    """

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
        cleanup_sink: CleanupSink,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(generation_issuer, AdbServerGenerationIssuer):
            raise TypeError("generation_issuer must be AdbServerGenerationIssuer")
        if not isinstance(cleanup_sink, CleanupSink):
            raise TypeError("cleanup_sink must satisfy CleanupSink")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._state_lock = Lock()
        self._generation_issuer = generation_issuer
        self._generation = generation_issuer.issue()
        self._pending: _AdbServerBackendPendingAcquire | None = None
        self._ownership: _AdbServerBackendOwnership[HandleT] | None = None
        self._releasing: _AdbServerBackendOwnership[HandleT] | None = None
        self._cleanup_sink = cleanup_sink
        self._publisher = publisher

    def read(self) -> AdbServerState:
        """Atomically return the current generation and its usable endpoint, if any."""

        with self._state_lock:
            ownership = self._ownership
            return AdbServerState(
                generation=self._generation,
                endpoint=None if ownership is None else ownership.acquisition.endpoint,
            )

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher for subsequent state-transition notifications."""

        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        with self._state_lock:
            if self._pending is not None or self._releasing is not None:
                raise RuntimeError("cannot bind an event publisher during a backend operation")
            self._publisher = publisher

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
        cancellation: Event,
    ) -> tuple[HandleT, AdbServerEndpoint]:
        """Obtain an acquisition handle and its usable endpoint.

        Implementations should observe ``cancellation`` during blocking startup and raise
        ``AdbServerBackendAcquireInterruptedError`` when cancellation is requested. Raise
        ``AdbServerBackendAcquireError`` for expected acquisition failures. If implementation
        startup created a resource whose cleanup could not be confirmed, carry it in the
        template-facing exception as ``CleanupHandoff``.
        """

    @abstractmethod
    def _release_handle(self, handle: HandleT) -> CleanupHandoff | None:
        """Attempt normal cleanup and return unresolved ownership when cleanup is unconfirmed."""

    @staticmethod
    def _publish_notification(
        publisher: EventPublisher,
        event: AdbServerActivated | AdbServerDeactivated,
    ) -> None:
        """Publish a non-authoritative state-transition notification after commit."""

        try:
            publisher.publish(event)
        except Exception:
            return

    def _accept_cleanup(self, cleanup_handoff: CleanupHandoff | None) -> None:
        if cleanup_handoff is None:
            return
        if not isinstance(cleanup_handoff, CleanupHandoff):
            raise TypeError("cleanup_handoff must be CleanupHandoff or None")
        # Ownership transfers out of the backend here. The backend never reclaims accepted work.
        self._cleanup_sink.accept(cleanup_handoff)

    def _cleanup_obtained_handle(self, handle: HandleT) -> None:
        """Clean a detached handle or transfer unresolved ownership to the cleanup sink."""

        try:
            cleanup_handoff = self._release_handle(handle)
        except BaseException as exc:
            cleanup_handoff = CleanupHandoff(
                resource=handle,
                diagnostic=(
                    "ADB server backend cleanup raised unexpectedly: "
                    f"{_cleanup_exception_diagnostic(exc)}"
                ),
            )
        self._accept_cleanup(cleanup_handoff)

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")

        with self._state_lock:
            ownership = self._ownership
            if ownership is not None:
                return AdbServerBackendAlreadyAcquired(ownership.acquisition)
            if self._pending is not None or self._releasing is not None:
                return AdbServerBackendAcquireDeferred(
                    "ADB server backend is busy with another operation"
                )

            pending = _AdbServerBackendPendingAcquire(
                generation=self._generation,
                cancellation=Event(),
            )
            self._pending = pending

        try:
            handle, endpoint = self._obtain_handle(endpoint_constraint, pending.cancellation)
        except AdbServerBackendAcquireInterruptedError as exc:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            self._accept_cleanup(exc.cleanup_handoff)
            if revoked:
                return AdbServerBackendAcquireRevoked(pending.generation)
            raise RuntimeError(
                "ADB server backend acquisition was interrupted without generation revocation"
            ) from exc
        except AdbServerBackendAcquireError as exc:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            self._accept_cleanup(exc.cleanup_handoff)
            if revoked:
                return AdbServerBackendAcquireRevoked(pending.generation)
            return AdbServerBackendAcquireFailed(exc.diagnostic)
        except CleanupHandoffError as exc:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            self._accept_cleanup(exc.cleanup_handoff)
            raise exc.primary_error from exc
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            raise

        if endpoint_constraint is not None and endpoint != endpoint_constraint:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            self._cleanup_obtained_handle(handle)
            if revoked:
                return AdbServerBackendAcquireRevoked(pending.generation)
            raise AdbServerLifecycleConsistencyError(
                "endpoint-constrained ADB server backend acquisition returned a different endpoint"
            )

        try:
            acquisition = AdbServerBackendAcquired(
                endpoint=endpoint,
                generation=pending.generation,
            )
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            self._cleanup_obtained_handle(handle)
            raise

        committed = False
        publisher: EventPublisher | None = None
        with self._state_lock:
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._ownership = _AdbServerBackendOwnership(handle, acquisition)
                publisher = self._publisher
                committed = True
            elif self._pending is pending:
                self._pending = None

        if committed:
            if publisher is not None:
                self._publish_notification(publisher, AdbServerActivated(acquisition.generation))
            return acquisition

        self._cleanup_obtained_handle(handle)
        return AdbServerBackendAcquireRevoked(pending.generation)

    def release(self, expected: AdbServerGeneration) -> AdbServerBackendReleaseResult:
        if not isinstance(expected, AdbServerGeneration):
            raise TypeError("expected must be AdbServerGeneration")

        ownership_to_release: _AdbServerBackendOwnership[HandleT] | None = None
        released_generation = expected
        with self._state_lock:
            if expected != self._generation:
                ownership = self._ownership
                return AdbServerBackendReleaseMismatch(
                    current=None if ownership is None else ownership.acquisition,
                    current_generation=self._generation,
                )

            pending = self._pending
            current_pending = (
                pending if pending is not None and pending.generation == self._generation else None
            )
            ownership = self._ownership
            if current_pending is None and ownership is None:
                return AdbServerBackendReleaseInactive(generation=expected)

            released_generation = self._generation
            self._generation = self._generation_issuer.issue()

            if current_pending is not None:
                current_pending.cancellation.set()
                return AdbServerBackendReleased(generation=released_generation)

            if ownership is None:
                raise RuntimeError("ADB server backend authority state is inconsistent")

            self._ownership = None
            self._releasing = ownership
            ownership_to_release = ownership

        publisher: EventPublisher | None = None
        try:
            self._cleanup_obtained_handle(ownership_to_release.handle)
        finally:
            with self._state_lock:
                if self._releasing is ownership_to_release:
                    self._releasing = None
                publisher = self._publisher
            if publisher is not None:
                self._publish_notification(
                    publisher,
                    AdbServerDeactivated(released_generation),
                )

        return AdbServerBackendReleased(
            generation=released_generation,
            acquisition=ownership_to_release.acquisition,
        )


__all__ = [
    "AdbServerBackendAcquireError",
    "AdbServerBackendAcquireInterruptedError",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendTemplate",
]
