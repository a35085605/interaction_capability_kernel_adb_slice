from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Event, Lock
from typing import Protocol

from networking import TcpAddress

from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.backend import (
    AdbTransportListWatchBackendAcquired,
    AdbTransportListWatchBackendAcquireDeferred,
    AdbTransportListWatchBackendAcquireFailed,
    AdbTransportListWatchBackendAcquireRevoked,
    AdbTransportListWatchBackendAcquireResult,
    AdbTransportListWatchBackendAlreadyAcquired,
    AdbTransportListWatchBackendReleased,
    AdbTransportListWatchBackendReleaseInactive,
    AdbTransportListWatchBackendReleaseMismatch,
    AdbTransportListWatchBackendReleaseResult,
)
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import AdbTransportListWatchState
from adb.transport_list.watch.stream import AdbTransportListWatchStream


class AdbTransportListWatchBackendAcquireError(RuntimeError):
    """Expected failure while obtaining a usable transport-list watch handle."""

    def __init__(self, failure: AdbTransportListWatchFailure) -> None:
        if not isinstance(failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        self.failure = failure
        super().__init__(failure.diagnostic or type(failure).__name__)


class AdbTransportListWatchBackendAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured watch generation was released."""


class _AdbTransportListWatchHandle(AdbTransportListWatchStream, Protocol):
    """Backend-private physical watch handle.

    Implementations also provide the producer data plane, but cancellation and cleanup remain
    exclusively under backend ownership.
    """

    def cancel(self) -> None:
        """Request non-blocking retirement and interrupt active blocking I/O."""
        ...

    def close(self) -> None:
        """Perform idempotent final physical cleanup."""
        ...


class _AdbTransportListWatchStreamView:
    """Narrow producer capability over a backend-owned physical handle."""

    __slots__ = ("__handle",)

    def __init__(self, handle: _AdbTransportListWatchHandle) -> None:
        self.__handle = handle

    @property
    def initial(self) -> AdbTransportList:
        return self.__handle.initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self.__handle.updates()


@dataclass(frozen=True, slots=True)
class _AdbTransportListWatchBackendPendingAcquire:
    generation: AdbTransportListWatchGeneration
    cancellation: Event


@dataclass(frozen=True, slots=True)
class _AdbTransportListWatchBackendOwnership:
    """Backend-private physical ownership plus producer-facing data capability."""

    handle: _AdbTransportListWatchHandle
    stream: AdbTransportListWatchStream
    acquisition: AdbTransportListWatchBackendAcquired


class AdbTransportListWatchBackendTemplate(ABC):
    """Template for one current watch generation and its optional physical handle.

    The current generation exists before acquisition starts. Failed/retried acquisitions retain
    it. Matching release advances the generation at the logical revocation point before
    cancellation of pending startup or retirement of a committed physical handle.

    Physical lifetime is entirely backend-owned. Producers receive only a narrow stream view for
    ``initial``/``updates()``; they never receive cancellation or cleanup authority.
    """

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError(
                "generation_issuer must be AdbTransportListWatchGenerationIssuer"
            )
        self._state_lock = Lock()
        self._generation_issuer = generation_issuer
        self._generation = generation_issuer.issue()
        self._pending: _AdbTransportListWatchBackendPendingAcquire | None = None
        self._ownership: _AdbTransportListWatchBackendOwnership | None = None

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
        """Obtain a fully usable backend-owned physical watch handle.

        Implementations should observe ``cancellation`` while startup blocks and raise
        ``AdbTransportListWatchBackendAcquireInterruptedError`` when cancellation wins.
        Expected establishment failures should be wrapped in
        ``AdbTransportListWatchBackendAcquireError``. Programming errors propagate.
        """

    @staticmethod
    def _cleanup_uncommitted_handle(handle: _AdbTransportListWatchHandle) -> None:
        """Best-effort final cleanup for a handle that never became authoritative."""

        try:
            handle.close()
        except Exception:
            # Cleanup diagnostics are non-authoritative and must not replace the primary
            # acquisition outcome or a programming exception already in flight.
            return

    @staticmethod
    def _retire_committed_handle(handle: _AdbTransportListWatchHandle) -> None:
        """Request non-blocking retirement of a detached committed handle."""

        try:
            handle.cancel()
        except Exception:
            # Logical release already linearized. Retirement failure is housekeeping evidence,
            # not a different lifecycle outcome. Adapters should make cancel() best-effort.
            return

    def _borrow_stream(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchStream | None:
        """Return the narrow producer stream while matching watch authority is current.

        This is package-internal orchestration plumbing, not part of
        ``AdbTransportListWatchBackend``. The returned stream does not own the physical handle;
        matching release may retire that handle concurrently after this method returns.
        """

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

    def _run_if_current(
        self,
        expected: AdbTransportListWatchGeneration,
        operation: Callable[[], None],
    ) -> bool:
        """Run one projection mutation while matching usable authority is current.

        This is package-internal coordination plumbing, deliberately excluded from the public
        backend protocol. The backend lock remains held for ``operation`` so matching release
        cannot advance the generation until the projection mutation finishes.
        """

        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")
        if not callable(operation):
            raise TypeError("operation must be callable")

        with self._state_lock:
            ownership = self._ownership
            if (
                expected != self._generation
                or ownership is None
                or ownership.acquisition.generation != expected
            ):
                return False
            operation()
            return True

    def acquire(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchBackendAcquireResult:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")

        with self._state_lock:
            ownership = self._ownership
            if ownership is not None:
                return AdbTransportListWatchBackendAlreadyAcquired(ownership.acquisition)
            if self._pending is not None:
                return AdbTransportListWatchBackendAcquireDeferred(
                    "ADB transport-list watch backend is busy with another acquisition"
                )
            pending = _AdbTransportListWatchBackendPendingAcquire(
                generation=self._generation,
                cancellation=Event(),
            )
            self._pending = pending

        try:
            handle = self._obtain_handle(endpoint, pending.cancellation)
        except AdbTransportListWatchBackendAcquireInterruptedError:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            if revoked:
                return AdbTransportListWatchBackendAcquireRevoked(pending.generation)
            raise RuntimeError(
                "ADB transport-list watch acquisition was interrupted without generation revocation"
            )
        except AdbTransportListWatchBackendAcquireError as exc:
            with self._state_lock:
                revoked = self._generation != pending.generation
                if self._pending is pending:
                    self._pending = None
            if revoked:
                return AdbTransportListWatchBackendAcquireRevoked(pending.generation)
            return AdbTransportListWatchBackendAcquireFailed(exc.failure)
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            raise

        try:
            acquisition = AdbTransportListWatchBackendAcquired(
                endpoint=endpoint,
                generation=pending.generation,
            )
            ownership = _AdbTransportListWatchBackendOwnership(
                handle=handle,
                stream=_AdbTransportListWatchStreamView(handle),
                acquisition=acquisition,
            )
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            self._cleanup_uncommitted_handle(handle)
            raise

        committed = False
        with self._state_lock:
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._ownership = ownership
                committed = True
            elif self._pending is pending:
                self._pending = None

        if committed:
            return acquisition

        # Matching release advanced the generation before this handle could commit. No producer
        # received its stream, so final cleanup can happen directly.
        self._cleanup_uncommitted_handle(handle)
        return AdbTransportListWatchBackendAcquireRevoked(pending.generation)

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchBackendReleaseResult:
        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        ownership_to_retire: _AdbTransportListWatchBackendOwnership | None = None
        with self._state_lock:
            if expected != self._generation:
                ownership = self._ownership
                return AdbTransportListWatchBackendReleaseMismatch(
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
                return AdbTransportListWatchBackendReleaseInactive(expected)

            # Logical revocation linearizes here. Once the generation advances, stale producer
            # commits are fenced by _run_if_current() regardless of when physical I/O unwinds.
            released_generation = self._generation
            self._generation = self._generation_issuer.issue()

            if current_pending is not None:
                current_pending.cancellation.set()
                return AdbTransportListWatchBackendReleased(released_generation)

            if ownership is None:
                raise RuntimeError(
                    "ADB transport-list watch backend authority state is inconsistent"
                )

            self._ownership = None
            ownership_to_retire = ownership

        # Physical retirement is deliberately outside authoritative backend state. cancel() only
        # interrupts the detached old handle and must not delay admission of the new generation.
        self._retire_committed_handle(ownership_to_retire.handle)
        return AdbTransportListWatchBackendReleased(
            generation=released_generation,
            acquisition=ownership_to_retire.acquisition,
        )


__all__ = [
    "AdbTransportListWatchBackendAcquireError",
    "AdbTransportListWatchBackendAcquireInterruptedError",
    "AdbTransportListWatchBackendTemplate",
]
