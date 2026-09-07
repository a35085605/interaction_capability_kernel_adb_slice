from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event, Lock
from typing import Generic, Protocol, TypeVar, runtime_checkable

from eventing import EventPublisher
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerState
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


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseCleanupUnconfirmed:
    """Signal that backend ownership was released but handle cleanup remains unconfirmed.

    ``handle`` is detached from backend ownership and remains available for diagnostics
    or implementation-specific cleanup.
    """

    handle: object
    diagnostic: str

    def __post_init__(self) -> None:
        if self.handle is None:
            raise TypeError("handle cannot be None")
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@runtime_checkable
class AdbServerBackendEventPublisherBinding(Protocol):
    """Optional capability for binding backend-template signals to a runtime event publisher."""

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher used for subsequent backend-template signals."""
        ...


class AdbServerBackendAcquireError(RuntimeError):
    """Expected failure while obtaining a backend acquisition handle."""

    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        super().__init__(self.diagnostic)


class AdbServerBackendAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured server generation was released."""


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
    """

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(generation_issuer, AdbServerGenerationIssuer):
            raise TypeError("generation_issuer must be AdbServerGenerationIssuer")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._state_lock = Lock()
        self._generation_issuer = generation_issuer
        self._generation = generation_issuer.issue()
        self._pending: _AdbServerBackendPendingAcquire | None = None
        self._ownership: _AdbServerBackendOwnership[HandleT] | None = None
        self._releasing: _AdbServerBackendOwnership[HandleT] | None = None
        self._publisher = publisher

    def read(self) -> AdbServerState:
        """Atomically return the current generation and its usable endpoint, if any."""

        with self._state_lock:
            ownership = self._ownership
            return AdbServerState(
                generation=self._generation,
                endpoint=None if ownership is None else ownership.acquisition.endpoint,
            )

    @property
    def generation(self) -> AdbServerGeneration:
        """Atomically return the current generation, including while the backend is idle."""

        with self._state_lock:
            return self._generation

    @property
    def current(self) -> AdbServerBackendAcquired | None:
        """Atomically return the currently owned usable server acquisition."""

        with self._state_lock:
            ownership = self._ownership
            return None if ownership is None else ownership.acquisition

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher for subsequent release-cleanup signals.

        Call during orchestration while no acquisition or release operation is active.
        """

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
        ``AdbServerBackendAcquireError`` for expected acquisition failures.
        """

    @abstractmethod
    def _release_handle(
        self,
        handle: HandleT,
    ) -> AdbServerBackendReleaseCleanupUnconfirmed | None:
        """Release a previously obtained handle and report unconfirmed cleanup as signal data."""

    @staticmethod
    def _publish_release_signal(
        publisher: EventPublisher,
        signal: AdbServerBackendReleaseCleanupUnconfirmed,
    ) -> None:
        """Publish cleanup evidence after backend release has linearized.

        Publication is best-effort; the completed release outcome remains authoritative.
        """

        try:
            publisher.publish(signal)
        except Exception:
            return

    def _cleanup_obtained_handle(
        self,
        handle: HandleT,
    ) -> None:
        signal = self._release_handle(handle)
        if signal is None:
            return
        with self._state_lock:
            publisher = self._publisher
        if publisher is not None:
            self._publish_release_signal(publisher, signal)

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

            # Capture the already-current generation before blocking acquisition work begins.
            # Failed attempts leave it unchanged; only release advances the generation.
            pending = _AdbServerBackendPendingAcquire(
                generation=self._generation,
                cancellation=Event(),
            )
            self._pending = pending

        try:
            handle, endpoint = self._obtain_handle(
                endpoint_constraint,
                pending.cancellation,
            )
        except AdbServerBackendAcquireInterruptedError:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            if revoked:
                return AdbServerBackendAcquireRevoked(pending.generation)
            raise RuntimeError(
                "ADB server backend acquisition was interrupted without generation revocation"
            )
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

        with self._state_lock:
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._ownership = _AdbServerBackendOwnership(handle, acquisition)
                return acquisition
            if self._pending is pending:
                self._pending = None

        # Release won the commit race. The endpoint must remain unavailable and a handle
        # obtained for an old generation is cleanup-only evidence, never a new acquisition.
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

            # Logical revocation linearizes here. Advancing the generation immediately fences
            # all work captured under ``expected`` before cancellation or physical cleanup.
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

        signal: AdbServerBackendReleaseCleanupUnconfirmed | None = None
        try:
            signal = self._release_handle(ownership_to_release.handle)
        finally:
            with self._state_lock:
                if self._releasing is ownership_to_release:
                    self._releasing = None
                publisher = self._publisher if signal is not None else None

        if signal is not None and publisher is not None:
            self._publish_release_signal(publisher, signal)
        return AdbServerBackendReleased(
            generation=released_generation,
            acquisition=ownership_to_release.acquisition,
        )


__all__ = [
    "AdbServerBackendAcquireError",
    "AdbServerBackendAcquireInterruptedError",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendReleaseCleanupUnconfirmed",
    "AdbServerBackendTemplate",
]
