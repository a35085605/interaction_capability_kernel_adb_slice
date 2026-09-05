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

        Completion marks the end of backend ownership; teardown may continue afterward.
        """
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
        """Obtain an acquisition handle and its usable endpoint.

        Raise ``AdbServerBackendAcquireError`` for expected acquisition failures.
        """

    @abstractmethod
    def _release_handle(self, handle: HandleT) -> None:
        """Release a previously obtained acquisition handle."""

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
        # Wait for any active operation, then linearize this release.
        with self._operation_condition:
            while self._active_operation is not None:
                self._operation_condition.wait()
            self._active_operation = operation

        try:
            handle = self._handle
            if handle is None:
                return

            # Clear the handle at the logical release point before implementation-specific teardown.
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
