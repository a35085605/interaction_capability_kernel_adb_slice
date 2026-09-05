from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from threading import Condition, Lock
from typing import Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

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
    """A new backend acquisition was established during this call.

    Only this result authorizes the lifecycle coordinator to attempt a corresponding
    authoritative-state activation for the reported ``endpoint``.
    """

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquirePreexisting:
    """A backend acquisition existed before this call linearized.

    This call did not establish a new acquisition and therefore does not authorize a corresponding
    authoritative-state activation. Endpoint evidence belongs only to newly achieved acquisitions.
    """


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireInProgress:
    """Backend acquisition work is already in progress.

    This call did not establish a new acquisition.
    """

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


@runtime_checkable
class AdbServerBackend(Protocol):
    """Provide acquisition and relinquishment of usable ADB server access.

    Lifecycle coordination does not impose total ordering across backend effects. Calls may overlap;
    implementations must make each acquire/release effect concurrency-safe and independently
    linearizable.
    """

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        """Acquire usable ADB server access, optionally constrained to ``endpoint_constraint``."""
        ...

    def release(self) -> None:
        """Relinquish the current backend acquisition.

        Relinquishment is complete at this boundary when the backend accepts responsibility for release;
        implementation-specific cleanup may continue afterward and is not part of the lifecycle result.
        """
        ...


class AdbServerBackendAcquireError(RuntimeError):
    """Implementation-side signal that obtaining a new backend handle failed."""

    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        super().__init__(self.diagnostic)


class _AdbServerBackendOperation(str, Enum):
    ACQUIRE = "acquire"
    RELEASE = "release"


HandleT = TypeVar("HandleT")


class AdbServerBackendBase(Generic[HandleT], ABC):
    """Serialize backend ownership effects around one implementation-defined handle.

    The base persists only the implementation-defined handle and operation concurrency state.
    Endpoint evidence is produced only when a new handle is obtained and is not retained separately by
    the backend. A retained handle therefore makes later acquisitions preexisting without endpoint
    evidence; endpoint consistency remains a lifecycle/domain concern.
    """

    def __init__(self) -> None:
        self._operation_state_lock = Lock()
        self._operation_condition = Condition(self._operation_state_lock)
        self._active_operation: _AdbServerBackendOperation | None = None
        self._handle: HandleT | None = None

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
    ) -> tuple[HandleT, AdbServerEndpoint]:
        """Obtain one handle and return its one-shot endpoint evidence.

        Raise ``AdbServerBackendAcquireError`` on expected acquisition failure. The returned endpoint
        is surfaced in ``AdbServerBackendAcquireAchieved`` and is not retained by this base.
        """

    @abstractmethod
    def _release_handle(self, handle: HandleT) -> None:
        """Accept responsibility for releasing one previously obtained handle."""

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
                self._release_handle(handle)
                raise

            self._handle = handle
            return acquisition
        finally:
            self._end_operation(operation)

    def release(self) -> None:
        operation = _AdbServerBackendOperation.RELEASE
        # Release is a command rather than an observable acquisition result. Wait for any earlier
        # operation to hand ownership back before this invocation linearizes relinquishment.
        with self._operation_condition:
            while self._active_operation is not None:
                self._operation_condition.wait()
            self._active_operation = operation

        try:
            handle = self._handle
            if handle is None:
                return

            # Logical backend ownership ends before adapter-specific cleanup. From this point a
            # a handle is never retained merely to represent cleanup convergence.
            self._handle = None
            self._release_handle(handle)
        finally:
            self._end_operation(operation)


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
]
