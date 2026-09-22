from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from time import monotonic, sleep
from typing import TypeAlias

from lifecycle.capability.supervision.control import (
    CancellationSignal,
    Clock,
    SupervisionStopped,
    SupervisionStopReason,
    normalize_supervision_timeout,
    stop_reason,
    validate_cancellation,
)
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


_Sleeper = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class AdbServerCommandPolicy:
    """Caller-facing wait bounds for serialized ADB server commands.

    ``None`` explicitly opts a command back into unbounded waiting. The defaults bound
    serialization-lock waiting and supervisor retries while preserving the lifecycle's
    retained-resource state when the caller stops waiting. One in-flight lifecycle I/O
    call still follows its lower-level operation-specific timeout/cancellation contract.
    """

    activate_timeout_seconds: float | None = 5.0
    deactivate_timeout_seconds: float | None = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "activate_timeout_seconds",
            normalize_supervision_timeout(
                self.activate_timeout_seconds,
                field_name="ADB server activate timeout",
            ),
        )
        object.__setattr__(
            self,
            "deactivate_timeout_seconds",
            normalize_supervision_timeout(
                self.deactivate_timeout_seconds,
                field_name="ADB server deactivate timeout",
            ),
        )


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


@dataclass(frozen=True, slots=True)
class AdbServerActivateIncomplete:
    """Report that activation stopped waiting before a terminal result was reached."""

    snapshot: AdbServerSnapshot
    reason: SupervisionStopReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, SupervisionStopReason):
            raise TypeError("reason must be SupervisionStopReason")


AdbServerActivateResult: TypeAlias = (
    AdbServerActivateSucceeded
    | AdbServerActivateAlreadyActive
    | AdbServerActivateConflict
    | AdbServerActivateReleaseRequired
    | AdbServerActivateFailed
    | AdbServerActivateIncomplete
)


@dataclass(frozen=True, slots=True)
class AdbServerDeactivateSucceeded:
    """Report that server runtime deactivation committed an idle snapshot."""

    snapshot: AdbServerSnapshot


@dataclass(frozen=True, slots=True)
class AdbServerDeactivateAlreadyIdle:
    """Report that the server runtime was already idle."""

    snapshot: AdbServerSnapshot


@dataclass(frozen=True, slots=True)
class AdbServerDeactivateIncomplete:
    """Report that deactivation stopped waiting while cleanup remains non-terminal.

    The snapshot is the lifecycle state at the point the command stopped waiting. In
    particular, a failed cleanup attempt remains ``RELEASE_REQUIRED`` and retains the
    resources owned by that generation for a later retry.
    """

    snapshot: AdbServerSnapshot
    reason: SupervisionStopReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, SupervisionStopReason):
            raise TypeError("reason must be SupervisionStopReason")


AdbServerDeactivateResult: TypeAlias = (
    AdbServerDeactivateSucceeded
    | AdbServerDeactivateAlreadyIdle
    | AdbServerDeactivateIncomplete
)


class AdbServerCommands:
    """Serialize caller-facing ADB server activate/deactivate commands.

    The command surface owns generation selection and caller wait bounds. Generation
    fencing and same-generation retry mechanics remain delegated to the lifecycle
    supervisors. Raw lifecycle mutation methods are intentionally not exposed.
    """

    __slots__ = (
        "_acquire",
        "_clock",
        "_lifecycle",
        "_lock",
        "_policy",
        "_release",
    )

    def __init__(
        self,
        lifecycle: AdbServerLifecycle,
        *,
        policy: AdbServerCommandPolicy = AdbServerCommandPolicy(),
        _sleeper: _Sleeper = sleep,
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if not isinstance(policy, AdbServerCommandPolicy):
            raise TypeError("policy must be AdbServerCommandPolicy")
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        if not callable(_clock):
            raise TypeError("_clock must be callable")
        self._lifecycle = lifecycle
        self._policy = policy
        self._clock = _clock
        self._acquire = AdbServerAcquireSupervisor(
            lifecycle,
            _sleeper=_sleeper,
            _clock=_clock,
        )
        self._release = AdbServerReleaseSupervisor(
            lifecycle,
            _sleeper=_sleeper,
            _clock=_clock,
        )
        self._lock = RLock()

    @property
    def policy(self) -> AdbServerCommandPolicy:
        return self._policy

    def _deadline(self, timeout_seconds: float | None) -> float | None:
        return None if timeout_seconds is None else self._clock() + timeout_seconds

    def _remaining_timeout(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - self._clock())

    def _acquire_lock(
        self,
        *,
        deadline: float | None,
        cancellation: CancellationSignal | None,
    ) -> SupervisionStopReason | None:
        """Acquire the command lock without exceeding caller wait bounds."""

        validate_cancellation(cancellation)
        if deadline is None and cancellation is None:
            self._lock.acquire()
            return None

        while True:
            reason = stop_reason(
                deadline=deadline,
                cancellation=cancellation,
                clock=self._clock,
            )
            if reason is not None:
                return reason

            wait_seconds = 0.05
            if deadline is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(0.0, deadline - self._clock()),
                )
            if wait_seconds <= 0.0:
                return SupervisionStopReason.TIMED_OUT
            if self._lock.acquire(timeout=wait_seconds):
                return None

    @contextmanager
    def _exclusive(
        self,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> Iterator[SupervisionStopReason | None]:
        """Hold the command lock across one runtime-internal compound operation."""

        timeout = normalize_supervision_timeout(
            timeout_seconds,
            field_name="ADB server exclusive command timeout",
        )
        deadline = self._deadline(timeout)
        reason = self._acquire_lock(deadline=deadline, cancellation=cancellation)
        if reason is not None:
            yield reason
            return
        try:
            yield None
        finally:
            self._lock.release()

    def activate(
        self,
        request: AdbServerRequest,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerActivateResult:
        """Activate ``request`` without implicitly replacing another active request."""

        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        validate_cancellation(cancellation)
        deadline = self._deadline(self._policy.activate_timeout_seconds)
        reason = self._acquire_lock(deadline=deadline, cancellation=cancellation)
        if reason is not None:
            return AdbServerActivateIncomplete(self._lifecycle.read(), reason)

        try:
            snapshot = self._lifecycle.read()

            if snapshot.phase is AdbServerPhase.ACTIVE:
                if snapshot.request == request:
                    return AdbServerActivateAlreadyActive(snapshot)
                if snapshot.request is None:
                    raise RuntimeError("active server snapshot is missing its request")
                return AdbServerActivateConflict(snapshot.request)

            if snapshot.phase is AdbServerPhase.RELEASE_REQUIRED:
                return AdbServerActivateReleaseRequired(snapshot)

            remaining = self._remaining_timeout(deadline)
            if remaining is not None and remaining <= 0.0:
                return AdbServerActivateIncomplete(
                    self._lifecycle.read(),
                    SupervisionStopReason.TIMED_OUT,
                )
            result = self._acquire.supervise(
                snapshot.generation,
                request,
                timeout_seconds=remaining,
                cancellation=cancellation,
            )
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
            if isinstance(result, SupervisionStopped):
                return AdbServerActivateIncomplete(self._lifecycle.read(), result.reason)
            if isinstance(result, AdbServerGenerationMismatch):
                raise RuntimeError(
                    "server lifecycle generation changed outside the command surface"
                )
            raise TypeError("acquire supervisor returned an unsupported result")
        finally:
            self._lock.release()

    def deactivate(
        self,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerDeactivateResult:
        """Deactivate the current request while preserving cleanup debt on bounded exit."""

        validate_cancellation(cancellation)
        deadline = self._deadline(self._policy.deactivate_timeout_seconds)
        reason = self._acquire_lock(deadline=deadline, cancellation=cancellation)
        if reason is not None:
            return AdbServerDeactivateIncomplete(self._lifecycle.read(), reason)

        try:
            snapshot = self._lifecycle.read()
            if snapshot.phase is AdbServerPhase.IDLE:
                return AdbServerDeactivateAlreadyIdle(snapshot)
            if snapshot.request is None:
                raise RuntimeError("non-idle server snapshot is missing its request")

            remaining = self._remaining_timeout(deadline)
            if remaining is not None and remaining <= 0.0:
                return AdbServerDeactivateIncomplete(
                    self._lifecycle.read(),
                    SupervisionStopReason.TIMED_OUT,
                )
            result = self._release.supervise(
                snapshot.generation,
                snapshot.request,
                timeout_seconds=remaining,
                cancellation=cancellation,
            )
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
            if isinstance(result, SupervisionStopped):
                return AdbServerDeactivateIncomplete(self._lifecycle.read(), result.reason)
            if isinstance(result, AdbServerGenerationMismatch):
                raise RuntimeError(
                    "server lifecycle generation changed outside the command surface"
                )
            if isinstance(result, AdbServerReleaseRequestMismatch):
                raise RuntimeError(
                    "server lifecycle request changed outside the command surface"
                )
            raise TypeError("release supervisor returned an unsupported result")
        finally:
            self._lock.release()


__all__ = [
    "AdbServerActivateAlreadyActive",
    "AdbServerActivateConflict",
    "AdbServerActivateFailed",
    "AdbServerActivateIncomplete",
    "AdbServerActivateReleaseRequired",
    "AdbServerActivateResult",
    "AdbServerActivateSucceeded",
    "AdbServerCommandPolicy",
    "AdbServerCommands",
    "AdbServerDeactivateAlreadyIdle",
    "AdbServerDeactivateIncomplete",
    "AdbServerDeactivateResult",
    "AdbServerDeactivateSucceeded",
]
