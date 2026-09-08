from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event, Lock
from typing import Generic, Protocol, TypeVar, runtime_checkable

from eventing import EventPublisher
from networking import TcpAddress
from adb.cleanup import BackgroundCleanup, CleanupDelegate
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
    AdbServerBackendPendingAcquireReleased,
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


@runtime_checkable
class AdbServerBackendEventPublisherBinding(Protocol):
    """Optional capability for binding backend state-transition notifications."""

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher used for non-authoritative state-transition notifications."""
        ...


class AdbServerBackendAcquireError(RuntimeError):
    """Expected failure while obtaining a backend acquisition handle."""

    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        super().__init__(self.diagnostic)


class AdbServerBackendAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured server generation was released."""

    def __init__(self) -> None:
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

    Logical release is immediate: matching release advances the generation and detaches the
    current handle before returning. Physical cleanup is tracked independently in a background
    flow. A retired resource remains cleanup debt until regular cleanup succeeds or the configured
    ``CleanupDelegate`` eventually returns ``True``. Cleanup debt blocks a new acquisition only
    when both refer to the same server endpoint.
    """

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
        cleanup_delegate: CleanupDelegate,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(generation_issuer, AdbServerGenerationIssuer):
            raise TypeError("generation_issuer must be AdbServerGenerationIssuer")
        if not isinstance(cleanup_delegate, CleanupDelegate):
            raise TypeError("cleanup_delegate must satisfy CleanupDelegate")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._state_lock = Lock()
        self._generation_issuer = generation_issuer
        self._generation = generation_issuer.issue()
        self._pending: _AdbServerBackendPendingAcquire | None = None
        self._ownership: _AdbServerBackendOwnership[HandleT] | None = None
        self._cleanup = BackgroundCleanup(cleanup_delegate)
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
            if self._pending is not None:
                raise RuntimeError("cannot bind an event publisher during a backend acquisition")
            self._publisher = publisher

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
        cancellation: Event,
    ) -> tuple[HandleT, AdbServerEndpoint]:
        """Obtain an acquisition handle and its usable endpoint."""

    @abstractmethod
    def _cleanup_handle(self, handle: HandleT) -> object | None:
        """Attempt regular cleanup; return unresolved delegate resource or ``None`` on success."""

    def _schedule_cleanup(
        self,
        handle: HandleT,
        endpoint: AdbServerEndpoint,
    ) -> None:
        self._cleanup.submit(
            handle,
            lambda: self._cleanup_handle(handle),
            conflict_key=endpoint,
        )

    def _schedule_delegated_cleanup(
        self,
        resource: object,
        endpoint: AdbServerEndpoint | None,
    ) -> None:
        self._cleanup.submit_delegated(resource, conflict_key=endpoint)

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
            if self._pending is not None:
                return AdbServerBackendAcquireDeferred(
                    "ADB server backend is busy with another acquisition"
                )
            if endpoint_constraint is not None and self._cleanup.has_conflict(
                endpoint_constraint
            ):
                return AdbServerBackendAcquireDeferred(
                    "ADB server backend is cleaning a resource for the requested endpoint"
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
            if revoked:
                return AdbServerBackendAcquireRevoked(pending.generation)
            return AdbServerBackendAcquireFailed(exc.diagnostic)
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            raise

        if self._cleanup.has_conflict(endpoint):
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            self._schedule_cleanup(handle, endpoint)
            if revoked:
                return AdbServerBackendAcquireRevoked(pending.generation)
            return AdbServerBackendAcquireDeferred(
                "ADB server backend obtained an endpoint whose prior resource is still cleaning"
            )

        if endpoint_constraint is not None and endpoint != endpoint_constraint:
            with self._state_lock:
                revoked = self._generation != pending.generation
                self._schedule_cleanup(handle, endpoint)
                if self._pending is pending:
                    self._pending = None
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
                self._schedule_cleanup(handle, endpoint)
                if self._pending is pending:
                    self._pending = None
            raise

        committed = False
        publisher: EventPublisher | None = None
        with self._state_lock:
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._ownership = _AdbServerBackendOwnership(handle, acquisition)
                publisher = self._publisher
                committed = True
            else:
                self._schedule_cleanup(handle, endpoint)
                if self._pending is pending:
                    self._pending = None

        if committed:
            if publisher is not None:
                self._publish_notification(publisher, AdbServerActivated(acquisition.generation))
            return acquisition

        return AdbServerBackendAcquireRevoked(pending.generation)

    def release(self, expected: AdbServerGeneration) -> AdbServerBackendReleaseResult:
        if not isinstance(expected, AdbServerGeneration):
            raise TypeError("expected must be AdbServerGeneration")

        publisher: EventPublisher | None = None
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
                return AdbServerBackendPendingAcquireReleased(
                    generation=released_generation
                )

            if ownership is None:
                raise RuntimeError("ADB server backend authority state is inconsistent")

            self._ownership = None
            self._schedule_cleanup(ownership.handle, ownership.acquisition.endpoint)
            publisher = self._publisher

        if publisher is not None:
            self._publish_notification(
                publisher,
                AdbServerDeactivated(released_generation),
            )

        return AdbServerBackendReleased(generation=released_generation)


__all__ = [
    "AdbServerBackendAcquireError",
    "AdbServerBackendAcquireInterruptedError",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendTemplate",
]
