from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from threading import Condition, Lock
from typing import Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from eventing import EventPublisher
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireAchieved:
    """A backend acquisition newly established by this call."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquirePreexisting:
    """A backend acquisition already existed when this call linearized."""


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireInProgress:
    """Backend acquisition is already in progress."""

    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.diagnostic is not None:
            object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireBlocked:
    """Backend acquisition is currently unable to satisfy the request."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireFailed:
    """Backend acquisition failed to satisfy the request."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AdbServerBackendAcquireResult: TypeAlias = (
    AdbServerBackendAcquireAchieved
    | AdbServerBackendAcquirePreexisting
    | AdbServerBackendAcquireInProgress
    | AdbServerBackendAcquireBlocked
    | AdbServerBackendAcquireFailed
)


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseCleanupUnconfirmed:
    """Signal that logical backend release completed without confirmed handle cleanup.

    ``handle`` is the detached implementation-defined acquisition handle. Consumers may retain it
    for diagnostics or implementation-specific follow-up cleanup without restoring backend
    ownership.
    """

    handle: object
    diagnostic: str

    def __post_init__(self) -> None:
        if self.handle is None:
            raise TypeError("handle cannot be None")
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@runtime_checkable
class AdbServerBackend(Protocol):
    """Acquire and release usable ADB server access.

    Calls may overlap; each operation must be concurrency-safe and independently linearizable.
    """

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        """Acquire usable ADB server access, optionally constrained to ``endpoint_constraint``."""
        ...

    def release(self) -> None:
        """Release the current backend acquisition.

        Completion marks the end of backend ownership; unconfirmed residual cleanup may be
        reported asynchronously through a backend signal.
        """
        ...


@runtime_checkable
class AdbServerBackendEventPublisherBinding(Protocol):
    """Optional capability for binding backend signals to a runtime event publisher."""

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher used for subsequent backend signals."""
        ...


class AdbServerBackendAcquireError(RuntimeError):
    """Expected failure while obtaining a backend acquisition handle."""

    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        super().__init__(self.diagnostic)


class _AdbServerBackendOperation(str, Enum):
    ACQUIRE = "acquire"
    RELEASE = "release"


HandleT = TypeVar("HandleT")


class AdbServerBackendBase(Generic[HandleT], ABC):
    """Serialize backend acquisition ownership around one implementation-defined handle.

    A retained handle represents the current backend acquisition.
    """

    def __init__(self, *, publisher: EventPublisher | None = None) -> None:
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._operation_state_lock = Lock()
        self._operation_condition = Condition(self._operation_state_lock)
        self._active_operation: _AdbServerBackendOperation | None = None
        self._handle: HandleT | None = None
        self._publisher = publisher

    def bind_event_publisher(self, publisher: EventPublisher) -> None:
        """Bind the publisher used for subsequent release-cleanup signals.

        Binding is an orchestration-time operation and cannot overlap acquisition or release.
        """

        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        with self._operation_condition:
            if self._active_operation is not None:
                raise RuntimeError("cannot bind an event publisher during a backend operation")
            self._publisher = publisher

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
        """Best-effort publish after logical release has fully linearized.

        Publication is observational and must not turn a completed backend release back into a
        caller-visible release failure.
        """

        try:
            publisher.publish(signal)
        except Exception:
            return

    def _begin_operation(
        self,
        operation: _AdbServerBackendOperation,
    ) -> AdbServerBackendAcquireInProgress | AdbServerBackendAcquireBlocked | None:
        with self._operation_condition:
            active_operation = self._active_operation
            if active_operation is operation:
                return AdbServerBackendAcquireInProgress(
                    f"ADB server backend {operation.value} is already in progress"
                )
            if active_operation is not None:
                return AdbServerBackendAcquireBlocked(
                    f"ADB server backend {operation.value} cannot begin while "
                    f"{active_operation.value} is in progress"
                )
            self._active_operation = operation
            return None

    def _end_operation(self, operation: _AdbServerBackendOperation) -> None:
        with self._operation_condition:
            if self._active_operation is not operation:
                raise RuntimeError("ADB server backend operation state is inconsistent")
            self._active_operation = None
            self._operation_condition.notify_all()

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")

        operation = _AdbServerBackendOperation.ACQUIRE
        unavailable = self._begin_operation(operation)
        if unavailable is not None:
            return unavailable

        release_signal: AdbServerBackendReleaseCleanupUnconfirmed | None = None
        publisher: EventPublisher | None = None
        try:
            handle = self._handle
            if handle is not None:
                return AdbServerBackendAcquirePreexisting()

            try:
                handle, endpoint = self._obtain_handle(endpoint_constraint)
            except AdbServerBackendAcquireError as exc:
                return AdbServerBackendAcquireFailed(exc.diagnostic)

            try:
                acquisition = AdbServerBackendAcquireAchieved(endpoint)
            except BaseException:
                release_signal = self._release_handle(handle)
                if release_signal is not None:
                    publisher = self._publisher
                raise

            self._handle = handle
            return acquisition
        finally:
            self._end_operation(operation)
            if release_signal is not None and publisher is not None:
                self._publish_release_signal(publisher, release_signal)

    def release(self) -> None:
        operation = _AdbServerBackendOperation.RELEASE
        # Wait for any active operation, then linearize this release.
        with self._operation_condition:
            while self._active_operation is not None:
                self._operation_condition.wait()
            self._active_operation = operation

        release_signal: AdbServerBackendReleaseCleanupUnconfirmed | None = None
        publisher: EventPublisher | None = None
        try:
            handle = self._handle
            if handle is not None:
                release_signal = self._release_handle(handle)
                # The backend relinquishes active ownership after the implementation-specific
                # release attempt, even when teardown could not be confirmed. The detached handle
                # is preserved in release_signal for external follow-up.
                self._handle = None
                if release_signal is not None:
                    publisher = self._publisher
        finally:
            self._end_operation(operation)

        # Publish only after RELEASE is no longer the active operation so synchronous subscribers
        # can safely re-enter backend operations without waiting on the publisher's own call stack.
        if release_signal is not None and publisher is not None:
            self._publish_release_signal(publisher, release_signal)


__all__ = [
    "AdbServerBackend",
    "AdbServerBackendAcquireAchieved",
    "AdbServerBackendAcquireBlocked",
    "AdbServerBackendAcquireError",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireInProgress",
    "AdbServerBackendAcquirePreexisting",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendBase",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendReleaseCleanupUnconfirmed",
]
