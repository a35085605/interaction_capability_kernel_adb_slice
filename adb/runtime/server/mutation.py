from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Iterator, TypeAlias

from adb.server.lifecycle import (
    AdbServerAcquireAlreadyActive,
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerAcquireRequestMismatch,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
    AdbServerLifecycle,
    AdbServerReleaseAlreadyIdle,
    AdbServerReleaseRequestMismatch,
    AdbServerReleaseSucceeded,
)
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerPhase, AdbServerSnapshot
from adb.server.supervision import AdbServerAcquireSupervisor, AdbServerReleaseSupervisor


@dataclass(frozen=True, slots=True)
class AdbServerActivateSucceeded:
    """Report that the requested ADB server runtime is active."""

    snapshot: AdbServerSnapshot


@dataclass(frozen=True, slots=True)
class AdbServerActivateAlreadyActive:
    """Report that the requested ADB server runtime was already active."""

    snapshot: AdbServerSnapshot


@dataclass(frozen=True, slots=True)
class AdbServerActivateConflict:
    """Report that another server request is already active."""

    current_request: AdbServerRequest


@dataclass(frozen=True, slots=True)
class AdbServerActivateReleaseRequired:
    """Report that a prior lifecycle failure must be released before activation."""

    snapshot: AdbServerSnapshot


@dataclass(frozen=True, slots=True)
class AdbServerActivateFailed:
    """Report that this activation failed and now requires explicit deactivation."""

    snapshot: AdbServerSnapshot


AdbServerActivateResult: TypeAlias = (
    AdbServerActivateSucceeded
    | AdbServerActivateAlreadyActive
    | AdbServerActivateConflict
    | AdbServerActivateReleaseRequired
    | AdbServerActivateFailed
)


@dataclass(frozen=True, slots=True)
class AdbServerDeactivateSucceeded:
    """Report that server runtime deactivation committed an idle snapshot."""

    snapshot: AdbServerSnapshot


@dataclass(frozen=True, slots=True)
class AdbServerDeactivateAlreadyIdle:
    """Report that the server runtime was already idle."""

    snapshot: AdbServerSnapshot


AdbServerDeactivateResult: TypeAlias = (
    AdbServerDeactivateSucceeded | AdbServerDeactivateAlreadyIdle
)


class AdbServerMutationFacade:
    """Serialize runtime-level ADB server activation and deactivation commands.

    The facade owns generation selection for callers. Generation fencing, lifecycle-busy
    retries, and same-generation release retries remain delegated to the lifecycle
    supervisors. Raw lifecycle mutation methods are intentionally not exposed.
    """

    __slots__ = ("_acquire", "_lifecycle", "_lock", "_release")

    def __init__(self, lifecycle: AdbServerLifecycle) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        self._lifecycle = lifecycle
        self._acquire = AdbServerAcquireSupervisor(lifecycle)
        self._release = AdbServerReleaseSupervisor(lifecycle)
        self._lock = RLock()

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Hold the mutation lock across one runtime-internal compound operation.

        ``activate()`` and ``deactivate()`` acquire the same reentrant lock, allowing
        a higher-level runtime supervisor to make an activate/cleanup pair atomic
        without exposing raw lifecycle mutations.
        """

        with self._lock:
            yield

    def activate(self, request: AdbServerRequest) -> AdbServerActivateResult:
        """Activate ``request`` without implicitly replacing another active request."""

        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")

        with self._lock:
            snapshot = self._lifecycle.read()

            if snapshot.phase is AdbServerPhase.ACTIVE:
                if snapshot.request == request:
                    return AdbServerActivateAlreadyActive(snapshot)
                if snapshot.request is None:
                    raise RuntimeError("active server snapshot is missing its request")
                return AdbServerActivateConflict(snapshot.request)

            if snapshot.phase is AdbServerPhase.RELEASE_REQUIRED:
                return AdbServerActivateReleaseRequired(snapshot)

            result = self._acquire.supervise(snapshot.generation, request)
            if isinstance(result, AdbServerAcquireSucceeded):
                return AdbServerActivateSucceeded(result.snapshot)
            if isinstance(result, AdbServerAcquireAlreadyActive):
                return AdbServerActivateAlreadyActive(result.snapshot)
            if isinstance(result, AdbServerAcquireFailed):
                return AdbServerActivateFailed(result.snapshot)
            if isinstance(result, AdbServerAcquireReleaseRequired):
                return AdbServerActivateReleaseRequired(result.snapshot)
            if isinstance(result, AdbServerAcquireRequestMismatch):
                return AdbServerActivateConflict(result.current_request)
            if isinstance(result, AdbServerGenerationMismatch):
                raise RuntimeError(
                    "server lifecycle generation changed outside the mutation facade"
                )
            raise TypeError("acquire supervisor returned an unsupported result")

    def deactivate(self) -> AdbServerDeactivateResult:
        """Deactivate the current server request without selecting generations externally."""

        with self._lock:
            snapshot = self._lifecycle.read()
            if snapshot.phase is AdbServerPhase.IDLE:
                return AdbServerDeactivateAlreadyIdle(snapshot)
            if snapshot.request is None:
                raise RuntimeError("non-idle server snapshot is missing its request")

            result = self._release.supervise(snapshot.generation, snapshot.request)
            if isinstance(result, AdbServerReleaseSucceeded):
                current = self._lifecycle.read()
                if (
                    current.phase is not AdbServerPhase.IDLE
                    or current.generation != result.next_generation
                ):
                    raise RuntimeError(
                        "server lifecycle release did not commit the expected idle generation"
                    )
                return AdbServerDeactivateSucceeded(current)
            if isinstance(result, AdbServerReleaseAlreadyIdle):
                current = self._lifecycle.read()
                if current.phase is not AdbServerPhase.IDLE:
                    raise RuntimeError(
                        "server lifecycle reported idle release without an idle snapshot"
                    )
                return AdbServerDeactivateAlreadyIdle(current)
            if isinstance(result, AdbServerGenerationMismatch):
                raise RuntimeError(
                    "server lifecycle generation changed outside the mutation facade"
                )
            if isinstance(result, AdbServerReleaseRequestMismatch):
                raise RuntimeError(
                    "server lifecycle request changed outside the mutation facade"
                )
            raise TypeError("release supervisor returned an unsupported result")


__all__ = [
    "AdbServerActivateAlreadyActive",
    "AdbServerActivateConflict",
    "AdbServerActivateFailed",
    "AdbServerActivateReleaseRequired",
    "AdbServerActivateResult",
    "AdbServerActivateSucceeded",
    "AdbServerDeactivateAlreadyIdle",
    "AdbServerDeactivateResult",
    "AdbServerDeactivateSucceeded",
    "AdbServerMutationFacade",
]
