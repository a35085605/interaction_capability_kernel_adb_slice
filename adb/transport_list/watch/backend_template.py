from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event, Lock

from networking import TcpAddress

from adb.transport_list.session_identity import AdbTransportListSessionIdentity
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
from adb.transport_list.watch.session import AdbTransportListWatchSession
from adb.transport_list.watch.state import AdbTransportListWatchState


class AdbTransportListWatchBackendAcquireError(RuntimeError):
    """Expected failure while obtaining a usable transport-list watch session."""

    def __init__(self, failure: AdbTransportListWatchFailure) -> None:
        if not isinstance(failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        self.failure = failure
        super().__init__(failure.diagnostic or type(failure).__name__)


class AdbTransportListWatchBackendAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured watch generation was released."""


@dataclass(frozen=True, slots=True)
class _AdbTransportListWatchBackendPendingAcquire:
    generation: AdbTransportListWatchGeneration
    cancellation: Event


class AdbTransportListWatchBackendTemplate(ABC):
    """Template for one current watch generation and its optional usable session.

    The current generation exists before acquisition starts. Failed/retried acquisitions
    retain it. Matching release advances the generation at the logical revocation point,
    before startup cancellation or physical session cleanup. This makes the backend the
    sole linearizable authority for watch lifecycle and resource ownership.
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
        self._acquisition: AdbTransportListWatchBackendAcquired | None = None
        self._releasing: AdbTransportListWatchBackendAcquired | None = None

    def read(self) -> AdbTransportListWatchState:
        """Atomically return current generation and usable session metadata, if any."""

        with self._state_lock:
            acquisition = self._acquisition
            return AdbTransportListWatchState(
                generation=self._generation,
                endpoint=None if acquisition is None else acquisition.endpoint,
                session_identity=(
                    None if acquisition is None else acquisition.session_identity
                ),
            )

    @abstractmethod
    def _obtain_session(
        self,
        endpoint: TcpAddress,
        startup_timeout_seconds: float,
        cancellation: Event,
    ) -> AdbTransportListWatchSession:
        """Obtain a fully usable session.

        Implementations should observe ``cancellation`` while startup blocks and raise
        ``AdbTransportListWatchBackendAcquireInterruptedError`` when cancellation wins.
        Expected establishment failures should be wrapped in
        ``AdbTransportListWatchBackendAcquireError``. Programming errors propagate.
        """

    def _release_session(self, session: AdbTransportListWatchSession) -> None:
        """Physically release a previously committed or rollback-only session."""

        session.close()

    def _resource_closed(
        self,
        session_identity: AdbTransportListSessionIdentity,
    ) -> None:
        """Revoke current authority when an owned session closes itself unexpectedly.

        Adapters whose sessions can self-close on read failure should call this hook from
        their close callback. It is identity-fenced and becomes a no-op during normal
        ``release()``, which detaches ownership before physical cleanup.
        """

        if not isinstance(session_identity, AdbTransportListSessionIdentity):
            raise TypeError("session_identity must be AdbTransportListSessionIdentity")
        with self._state_lock:
            acquisition = self._acquisition
            if (
                acquisition is None
                or acquisition.session_identity is not session_identity
            ):
                return
            self._acquisition = None
            self._generation = self._generation_issuer.issue()

    def acquire(
        self,
        endpoint: TcpAddress,
        *,
        startup_timeout_seconds: float = 5.0,
    ) -> AdbTransportListWatchBackendAcquireResult:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")

        with self._state_lock:
            acquisition = self._acquisition
            if acquisition is not None:
                return AdbTransportListWatchBackendAlreadyAcquired(acquisition)
            if self._pending is not None or self._releasing is not None:
                return AdbTransportListWatchBackendAcquireDeferred(
                    "ADB transport-list watch backend is busy with another operation"
                )
            pending = _AdbTransportListWatchBackendPendingAcquire(
                generation=self._generation,
                cancellation=Event(),
            )
            self._pending = pending

        try:
            session = self._obtain_session(
                endpoint,
                startup_timeout_seconds,
                pending.cancellation,
            )
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
                session=session,
            )
        except BaseException:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            self._release_session(session)
            raise

        committed = False
        with self._state_lock:
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._acquisition = acquisition
                committed = True
            elif self._pending is pending:
                self._pending = None

        if committed:
            return acquisition

        # Matching release advanced the generation before this session could commit.
        self._release_session(session)
        return AdbTransportListWatchBackendAcquireRevoked(pending.generation)

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchBackendReleaseResult:
        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        acquisition_to_release: AdbTransportListWatchBackendAcquired | None = None
        with self._state_lock:
            if expected != self._generation:
                return AdbTransportListWatchBackendReleaseMismatch(
                    current=self._acquisition,
                    current_generation=self._generation,
                )

            pending = self._pending
            current_pending = (
                pending
                if pending is not None and pending.generation == self._generation
                else None
            )
            acquisition = self._acquisition
            if current_pending is None and acquisition is None:
                return AdbTransportListWatchBackendReleaseInactive(expected)

            released_generation = self._generation
            self._generation = self._generation_issuer.issue()

            if current_pending is not None:
                current_pending.cancellation.set()
                return AdbTransportListWatchBackendReleased(released_generation)

            if acquisition is None:
                raise RuntimeError(
                    "ADB transport-list watch backend authority state is inconsistent"
                )

            self._acquisition = None
            self._releasing = acquisition
            acquisition_to_release = acquisition

        try:
            self._release_session(acquisition_to_release.session)
        finally:
            with self._state_lock:
                if self._releasing is acquisition_to_release:
                    self._releasing = None

        return AdbTransportListWatchBackendReleased(
            generation=released_generation,
            acquisition=acquisition_to_release,
        )


__all__ = [
    "AdbTransportListWatchBackendAcquireError",
    "AdbTransportListWatchBackendAcquireInterruptedError",
    "AdbTransportListWatchBackendTemplate",
]
