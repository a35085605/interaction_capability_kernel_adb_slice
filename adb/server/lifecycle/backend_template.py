from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Lock
from typing import Generic, Protocol, TypeVar, runtime_checkable

from eventing import EventPublisher
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer
from adb.server.lifecycle.backend import (
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireResult,
    AdbServerBackendAlreadyAcquired,
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


HandleT = TypeVar("HandleT")


@dataclass(frozen=True, slots=True)
class _AdbServerBackendOwnership(Generic[HandleT]):
    handle: HandleT
    acquisition: AdbServerBackendAcquired


class AdbServerBackendTemplate(Generic[HandleT], ABC):
    """Template for serialized ownership of one backend acquisition.

    The template defines ownership, concurrency, and cleanup signaling; subclasses
    provide handle acquisition and release mechanics.
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
        self._operation_lock = Lock()
        self._identity_issuer = identity_issuer
        self._ownership: _AdbServerBackendOwnership[HandleT] | None = None
        self._publisher = publisher

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher for subsequent release-cleanup signals.

        Call during orchestration before acquisition or release begins.
        """

        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("cannot bind an event publisher during a backend operation")
        try:
            self._publisher = publisher
        finally:
            self._operation_lock.release()

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
    ) -> tuple[HandleT, AdbServerEndpoint]:
        """Obtain an acquisition handle and its usable endpoint.

        Raise ``AdbServerBackendAcquireError`` for expected acquisition failures.
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

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")

        if not self._operation_lock.acquire(blocking=False):
            return AdbServerBackendAcquireDeferred(
                "ADB server backend is busy with another operation"
            )

        release_signal: AdbServerBackendReleaseCleanupUnconfirmed | None = None
        publisher: EventPublisher | None = None
        try:
            ownership = self._ownership
            if ownership is not None:
                return AdbServerBackendAlreadyAcquired()

            try:
                handle, endpoint = self._obtain_handle(endpoint_constraint)
            except AdbServerBackendAcquireError as exc:
                return AdbServerBackendAcquireFailed(exc.diagnostic)

            try:
                acquisition = AdbServerBackendAcquired(
                    endpoint=endpoint,
                    identity=self._identity_issuer.issue(),
                )
            except BaseException:
                release_signal = self._release_handle(handle)
                if release_signal is not None:
                    publisher = self._publisher
                raise

            self._ownership = _AdbServerBackendOwnership(handle, acquisition)
            return acquisition
        finally:
            self._operation_lock.release()
            if release_signal is not None and publisher is not None:
                self._publish_release_signal(publisher, release_signal)

    def release(self, expected: AdbServerIdentity) -> bool:
        if not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity")

        release_signal: AdbServerBackendReleaseCleanupUnconfirmed | None = None
        publisher: EventPublisher | None = None
        released = False
        # Release waits for any active backend operation, then remains busy until logical release
        # has fully linearized.
        with self._operation_lock:
            ownership = self._ownership
            if ownership is not None and ownership.acquisition.identity == expected:
                release_signal = self._release_handle(ownership.handle)
                # The backend relinquishes active ownership after the implementation-specific
                # release attempt, even when teardown could not be confirmed. The detached handle
                # is preserved in release_signal for external follow-up.
                self._ownership = None
                released = True
                if release_signal is not None:
                    publisher = self._publisher

        # Publish only after the backend is no longer busy so synchronous subscribers can safely
        # re-enter backend operations without waiting on the publisher's own call stack.
        if release_signal is not None and publisher is not None:
            self._publish_release_signal(publisher, release_signal)
        return released


__all__ = [
    "AdbServerBackendAcquireError",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendReleaseCleanupUnconfirmed",
    "AdbServerBackendTemplate",
]
