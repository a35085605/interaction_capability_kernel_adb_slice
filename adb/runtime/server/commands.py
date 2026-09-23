from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from time import monotonic, sleep
from typing import TypeAlias

from lifecycle.capability.result import LifecycleResult
from lifecycle.capability.supervision.acquire import (
    AcquireDisposition,
    classify_acquire_result,
)
from lifecycle.capability.supervision.control import (
    CancellationSignal,
    Clock,
    SupervisionStopped,
    SupervisionStopReason,
    normalize_supervision_timeout,
    stop_reason,
    validate_cancellation,
)
from lifecycle.capability.supervision.release import (
    ReleaseDisposition,
    classify_release_result,
)
from adb.server.lifecycle import AdbServerLifecycle, AdbServerLifecycleResult
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
class AdbServerActivateIncomplete:
    """Report that activation stopped waiting before a terminal result was reached."""

    snapshot: AdbServerSnapshot
    reason: SupervisionStopReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, SupervisionStopReason):
            raise TypeError("reason must be SupervisionStopReason")


AdbServerActivateResult: TypeAlias = AdbServerLifecycleResult | AdbServerActivateIncomplete


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
    AdbServerLifecycleResult | AdbServerDeactivateIncomplete
)


class AdbServerCommands:
    """Serialize caller-facing ADB server activate/deactivate commands.

    The command surface owns generation selection and caller wait bounds. Generation
    fencing and same-generation retry mechanics remain delegated to the lifecycle
    supervisors. Successful/terminal commands return the lifecycle result directly;
    only timeout/cancellation adds command-specific information.
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

            if snapshot.phase in (
                AdbServerPhase.ACTIVE,
                AdbServerPhase.RELEASE_REQUIRED,
            ):
                return LifecycleResult(snapshot, False)

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
            if isinstance(result, SupervisionStopped):
                return AdbServerActivateIncomplete(self._lifecycle.read(), result.reason)

            disposition = classify_acquire_result(
                result,
                snapshot.generation,
                request,
            )
            if disposition is AcquireDisposition.GENERATION_MISMATCH:
                raise RuntimeError(
                    "server lifecycle generation changed outside the command surface"
                )
            return result
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
                return LifecycleResult(snapshot, False)
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
            if isinstance(result, SupervisionStopped):
                return AdbServerDeactivateIncomplete(self._lifecycle.read(), result.reason)

            disposition = classify_release_result(
                result,
                snapshot.generation,
                snapshot.request,
            )
            if disposition is ReleaseDisposition.GENERATION_MISMATCH:
                raise RuntimeError(
                    "server lifecycle generation changed outside the command surface"
                )
            if disposition is ReleaseDisposition.REQUEST_MISMATCH:
                raise RuntimeError(
                    "server lifecycle request changed outside the command surface"
                )
            if disposition not in (
                ReleaseDisposition.SUCCEEDED,
                ReleaseDisposition.ALREADY_IDLE,
            ):
                raise TypeError("release supervisor returned a non-terminal result")
            return result
        finally:
            self._lock.release()


__all__ = [
    "AdbServerActivateIncomplete",
    "AdbServerActivateResult",
    "AdbServerCommandPolicy",
    "AdbServerCommands",
    "AdbServerDeactivateIncomplete",
    "AdbServerDeactivateResult",
]
