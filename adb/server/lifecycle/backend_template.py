from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event, Lock
from typing import Generic, Protocol, TypeVar, runtime_checkable

from eventing import EventPublisher
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer
from adb.server.lifecycle.backend import (
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInterrupted,
    AdbServerBackendAcquireResult,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendReleased,
    AdbServerBackendReleaseInterruptedAcquire,
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
    """Cooperative interruption after the current server authority was released."""


HandleT = TypeVar("HandleT")


@dataclass(frozen=True, slots=True)
class _AdbServerBackendOwnership(Generic[HandleT]):
    handle: HandleT
    acquisition: AdbServerBackendAcquired


@dataclass(slots=True)
class _AdbServerBackendPendingAcquire:
    identity: AdbServerIdentity
    cancellation: Event
    revoked: bool = False


class AdbServerBackendTemplate(Generic[HandleT], ABC):
    """Template for one fenced server authority and its optional usable endpoint.

    A fresh server identity is issued before implementation-specific acquisition begins.
    While acquisition is pending, ``identity`` is non-``None`` and ``current`` is ``None``.
    Release may revoke that identity and signal acquisition cancellation without waiting for
    the blocking acquisition path to finish. Subclasses provide cancellable handle acquisition
    and handle release mechanics.
    """

    def __init__(
        self,
        identity_issuer: AdbServerIdentityIssuer,
        *,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(identity_issuer, AdbServerIdentityIssuer):
            raise TypeError("identity_issuer must be AdbServerIdentityIssuer")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._state_lock = Lock()
        self._identity_issuer = identity_issuer
        self._pending: _AdbServerBackendPendingAcquire | None = None
        self._ownership: _AdbServerBackendOwnership[HandleT] | None = None
        self._releasing: _AdbServerBackendOwnership[HandleT] | None = None
        self._publisher = publisher

    @property
    def identity(self) -> AdbServerIdentity | None:
        """Atomically return the current authority identity, including pending acquisition."""

        with self._state_lock:
            pending = self._pending
            if pending is not None and not pending.revoked:
                return pending.identity
            ownership = self._ownership
            return None if ownership is None else ownership.acquisition.identity

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

            # Identity is the authority fence for the entire acquisition attempt. It exists
            # before any blocking implementation-specific acquisition work can begin.
            pending = _AdbServerBackendPendingAcquire(
                identity=self._identity_issuer.issue(),
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
                if self._pending is pending:
                    self._pending = None
            return AdbServerBackendAcquireInterrupted(pending.identity)
        except AdbServerBackendAcquireError as exc:
            with self._state_lock:
                revoked = pending.revoked
                if self._pending is pending:
                    self._pending = None
            if revoked:
                return AdbServerBackendAcquireInterrupted(pending.identity)
            return AdbServerBackendAcquireFailed(exc.diagnostic)
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            raise

        try:
            acquisition = AdbServerBackendAcquired(
                endpoint=endpoint,
                identity=pending.identity,
            )
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            self._cleanup_obtained_handle(handle)
            raise

        with self._state_lock:
            if self._pending is pending and not pending.revoked:
                self._pending = None
                self._ownership = _AdbServerBackendOwnership(handle, acquisition)
                return acquisition
            if self._pending is pending:
                self._pending = None

        # Release won the commit race. The endpoint must remain unavailable and a handle
        # obtained after revocation is cleanup-only evidence, never a new acquisition.
        self._cleanup_obtained_handle(handle)
        return AdbServerBackendAcquireInterrupted(pending.identity)

    def release(self, expected: AdbServerIdentity) -> AdbServerBackendReleaseResult:
        if not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity")

        ownership_to_release: _AdbServerBackendOwnership[HandleT] | None = None
        with self._state_lock:
            pending = self._pending
            if pending is not None and not pending.revoked and pending.identity == expected:
                # Revocation linearizes here: the pending authority no longer has an endpoint
                # and may never commit one. The operation remains registered only to prevent a
                # new acquisition from overlapping its cooperative cleanup.
                pending.revoked = True
                pending.cancellation.set()
                return AdbServerBackendReleaseInterruptedAcquire(expected)

            ownership = self._ownership
            if ownership is not None and ownership.acquisition.identity == expected:
                # Logical release linearizes before potentially blocking handle cleanup. From
                # this point current endpoint is None and stale work is fenced by identity.
                self._ownership = None
                self._releasing = ownership
                ownership_to_release = ownership
            else:
                current_identity: AdbServerIdentity | None = None
                if pending is not None and not pending.revoked:
                    current_identity = pending.identity
                elif ownership is not None:
                    current_identity = ownership.acquisition.identity
                current = None if ownership is None else ownership.acquisition
                return AdbServerBackendReleaseMismatch(
                    current=current,
                    current_identity=current_identity,
                )

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
        return AdbServerBackendReleased(ownership_to_release.acquisition)


__all__ = [
    "AdbServerBackendAcquireError",
    "AdbServerBackendAcquireInterruptedError",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendReleaseCleanupUnconfirmed",
    "AdbServerBackendTemplate",
]
