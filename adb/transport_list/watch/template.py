from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Event, Lock
from typing import Protocol

from networking import TcpAddress

from adb.cleanup import BackgroundCleanup, CleanupAttempt, CleanupDelegate
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.contract import (
    AdbTransportListWatchAcquisition,
    AdbTransportListWatchAcquireBlocked,
    AdbTransportListWatchAcquireCommitted,
    AdbTransportListWatchAcquireFailed,
    AdbTransportListWatchAcquireSuperseded,
    AdbTransportListWatchAcquireOutcome,
    AdbTransportListWatchAcquireExisting,
    AdbTransportListWatchReleaseApplied,
    AdbTransportListWatchReleaseInactive,
    AdbTransportListWatchReleaseGenerationMismatch,
    AdbTransportListWatchReleaseOutcome,
)
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import AdbTransportListWatchState
from adb.transport_list.watch.stream import AdbTransportListWatchStream


class AdbTransportListWatchAcquireError(RuntimeError):
    """Expected failure while obtaining a usable transport-list watch handle."""

    def __init__(self, failure: AdbTransportListWatchFailure) -> None:
        if not isinstance(failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        self.failure = failure
        super().__init__(failure.diagnostic or type(failure).__name__)


class AdbTransportListWatchAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured watch generation was released."""

    def __init__(self) -> None:
        super().__init__("ADB transport-list watch acquisition was interrupted")


class _AdbTransportListWatchHandle(AdbTransportListWatchStream, Protocol):
    """Lifecycle-private physical watch handle with lifecycle cleanup operations."""

    def close(self) -> object | None:
        """Attempt regular cleanup; return unresolved delegate resource or ``None``."""
        ...


class _AdbTransportListWatchStreamView:
    """Narrow producer capability over a lifecycle-owned physical handle."""

    __slots__ = ("__handle",)

    def __init__(self, handle: _AdbTransportListWatchHandle) -> None:
        self.__handle = handle

    @property
    def initial(self) -> AdbTransportList:
        return self.__handle.initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self.__handle.updates()


@dataclass(frozen=True, slots=True)
class _PendingAcquire:
    generation: AdbTransportListWatchGeneration
    cancellation: Event


@dataclass(frozen=True, slots=True)
class _Ownership:
    """Lifecycle-private physical ownership plus producer-facing data capability."""

    handle: _AdbTransportListWatchHandle
    stream: AdbTransportListWatchStream
    acquisition: AdbTransportListWatchAcquisition


class AdbTransportListWatchLifecycleTemplate(ABC):
    """Template for one current watch generation and its optional physical handle.

    Logical release advances the generation and detaches producer authority immediately. Physical
    cleanup is tracked independently in the background. A retired handle remains cleanup debt until
    regular cleanup succeeds or the configured ``CleanupDelegate`` eventually returns ``True``.
    Cleanup debt blocks a new acquisition only when it belongs to the same server endpoint.
    """

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        *,
        cleanup_delegate: CleanupDelegate,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError(
                "generation_issuer must be AdbTransportListWatchGenerationIssuer"
            )
        if not isinstance(cleanup_delegate, CleanupDelegate):
            raise TypeError("cleanup_delegate must satisfy CleanupDelegate")
        self._state_lock = Lock()
        self._generation_issuer = generation_issuer
        self._generation = generation_issuer.issue()
        self._pending: _PendingAcquire | None = None
        self._ownership: _Ownership | None = None
        self._cleanup = BackgroundCleanup(cleanup_delegate)

    def read(self) -> AdbTransportListWatchState:
        """Atomically return current generation and usable endpoint metadata, if any."""

        with self._state_lock:
            ownership = self._ownership
            return AdbTransportListWatchState(
                generation=self._generation,
                endpoint=None if ownership is None else ownership.acquisition.endpoint,
            )

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint: TcpAddress,
        cancellation: Event,
    ) -> _AdbTransportListWatchHandle:
        """Obtain a fully usable lifecycle-owned physical watch handle."""

    def _schedule_cleanup(
        self,
        handle: _AdbTransportListWatchHandle,
        endpoint: TcpAddress,
    ) -> None:
        self._cleanup.submit(handle, handle.close, conflict_key=endpoint)

    def _schedule_resource_cleanup(
        self,
        resource: object,
        regular_cleanup: CleanupAttempt,
        endpoint: TcpAddress,
    ) -> None:
        self._cleanup.submit(resource, regular_cleanup, conflict_key=endpoint)

    def _schedule_delegated_cleanup(
        self,
        resource: object,
        endpoint: TcpAddress,
    ) -> None:
        self._cleanup.submit_delegated(resource, conflict_key=endpoint)

    def _borrow_stream(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchStream | None:
        """Return the narrow producer stream while matching watch authority is current."""

        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        with self._state_lock:
            ownership = self._ownership
            if (
                expected != self._generation
                or ownership is None
                or ownership.acquisition.generation != expected
            ):
                return None
            return ownership.stream

    def acquire(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchAcquireOutcome:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")

        with self._state_lock:
            ownership = self._ownership
            if ownership is not None:
                if ownership.acquisition.endpoint == endpoint:
                    return AdbTransportListWatchAcquireExisting(ownership.acquisition)
                return AdbTransportListWatchAcquireBlocked(
                    "ADB transport-list watch lifecycle already retains a different endpoint"
                )
            if self._pending is not None:
                return AdbTransportListWatchAcquireBlocked(
                    "ADB transport-list watch lifecycle is busy with another acquisition"
                )
            if self._cleanup.has_conflict(endpoint):
                return AdbTransportListWatchAcquireBlocked(
                    "ADB transport-list watch lifecycle is cleaning a resource for this endpoint"
                )
            pending = _PendingAcquire(
                generation=self._generation,
                cancellation=Event(),
            )
            self._pending = pending

        try:
            handle = self._obtain_handle(endpoint, pending.cancellation)
        except AdbTransportListWatchAcquireInterruptedError as exc:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            if revoked:
                return AdbTransportListWatchAcquireSuperseded(pending.generation)
            raise RuntimeError(
                "ADB transport-list watch acquisition was interrupted without generation revocation"
            ) from exc
        except AdbTransportListWatchAcquireError as exc:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            if revoked:
                return AdbTransportListWatchAcquireSuperseded(pending.generation)
            return AdbTransportListWatchAcquireFailed(exc.failure)
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
                return AdbTransportListWatchAcquireSuperseded(pending.generation)
            return AdbTransportListWatchAcquireBlocked(
                "ADB transport-list watch lifecycle obtained a resource whose prior endpoint is still cleaning"
            )

        try:
            acquisition = AdbTransportListWatchAcquisition(
                endpoint=endpoint,
                generation=pending.generation,
            )
            ownership = _Ownership(
                handle=handle,
                stream=_AdbTransportListWatchStreamView(handle),
                acquisition=acquisition,
            )
        except BaseException:
            with self._state_lock:
                self._schedule_cleanup(handle, endpoint)
                if self._pending is pending:
                    self._pending = None
            raise

        committed = False
        with self._state_lock:
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._ownership = ownership
                committed = True
            else:
                self._schedule_cleanup(handle, endpoint)
                if self._pending is pending:
                    self._pending = None

        if committed:
            return AdbTransportListWatchAcquireCommitted(acquisition)

        return AdbTransportListWatchAcquireSuperseded(pending.generation)

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchReleaseOutcome:
        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        with self._state_lock:
            if expected != self._generation:
                ownership = self._ownership
                return AdbTransportListWatchReleaseGenerationMismatch(
                    current=None if ownership is None else ownership.acquisition,
                    current_generation=self._generation,
                )

            pending = self._pending
            current_pending = (
                pending
                if pending is not None and pending.generation == self._generation
                else None
            )
            ownership = self._ownership
            if current_pending is None and ownership is None:
                return AdbTransportListWatchReleaseInactive(expected)

            released_generation = self._generation
            self._generation = self._generation_issuer.issue()

            if current_pending is not None:
                current_pending.cancellation.set()
                return AdbTransportListWatchReleaseApplied(
                    generation=released_generation
                )

            if ownership is None:
                raise RuntimeError(
                    "ADB transport-list watch lifecycle authority state is inconsistent"
                )

            self._ownership = None
            self._schedule_cleanup(ownership.handle, ownership.acquisition.endpoint)

        return AdbTransportListWatchReleaseApplied(
            generation=released_generation,
            acquisition=ownership.acquisition,
        )


__all__ = [
    "AdbTransportListWatchAcquireError",
    "AdbTransportListWatchAcquireInterruptedError",
    "AdbTransportListWatchLifecycleTemplate",
]
