from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto
from time import monotonic, sleep
from typing import Generic, TypeAlias, TypeVar

from lifecycle.capability.lifecycle import CapabilityLifecycle
from lifecycle.capability.result import LifecycleResult
from lifecycle.capability.snapshot import LifecyclePhase
from lifecycle.capability.supervision.control import (
    CancellationSignal,
    Clock,
    SupervisionStopped,
    SupervisionStopReason,
    normalize_supervision_timeout,
    retry_delay,
    stop_reason,
    validate_cancellation,
)
from lifecycle.capability.supervision.policy import ReleaseSupervisionPolicy


GenerationT = TypeVar("GenerationT")
RequestT = TypeVar("RequestT")
CapabilityT = TypeVar("CapabilityT")

_Sleeper = Callable[[float], None]


class ReleaseDisposition(Enum):
    """Interpret one release result relative to its generation and request inputs."""

    SUCCEEDED = auto()
    ALREADY_IDLE = auto()
    FAILED = auto()
    GENERATION_MISMATCH = auto()
    REQUEST_MISMATCH = auto()
    BUSY = auto()


def classify_release_result(
    result: LifecycleResult[GenerationT, RequestT, CapabilityT],
    generation: GenerationT,
    request: RequestT,
) -> ReleaseDisposition:
    """Classify one raw lifecycle result using release's fencing precedence."""

    if not isinstance(result, LifecycleResult):
        raise TypeError("result must be LifecycleResult")

    snapshot = result.snapshot
    if result.execution_started:
        # A successful release advances generation, so execution must be considered
        # before generation fencing.
        if snapshot.phase is LifecyclePhase.IDLE:
            return ReleaseDisposition.SUCCEEDED
        if snapshot.phase is LifecyclePhase.RELEASE_REQUIRED:
            return ReleaseDisposition.FAILED
        raise RuntimeError(
            "executed release must finish IDLE or RELEASE_REQUIRED"
        )

    if snapshot.generation != generation:
        return ReleaseDisposition.GENERATION_MISMATCH
    if snapshot.phase in (LifecyclePhase.ACQUIRING, LifecyclePhase.RELEASING):
        return ReleaseDisposition.BUSY
    if snapshot.phase is LifecyclePhase.IDLE:
        return ReleaseDisposition.ALREADY_IDLE
    if snapshot.phase in (LifecyclePhase.ACTIVE, LifecyclePhase.RELEASE_REQUIRED):
        if snapshot.request != request:
            return ReleaseDisposition.REQUEST_MISMATCH

    raise RuntimeError("non-executed release returned an unsupported lifecycle state")


ReleaseSupervisionResult: TypeAlias = (
    LifecycleResult[GenerationT, RequestT, CapabilityT] | SupervisionStopped
)


class ReleaseSupervisor(Generic[GenerationT, RequestT, CapabilityT]):
    """Drive one request to a release-side terminal lifecycle result.

    Supervision is generation-scoped. Failed releases and busy lifecycle results are
    retried for the same generation/request pair; terminal results are returned
    unchanged and a newer generation is never followed. Caller-supplied timeout or
    cancellation bounds stop retrying without discarding retained cleanup ownership.
    """

    def __init__(
        self,
        lifecycle: CapabilityLifecycle[GenerationT, RequestT, CapabilityT],
        *,
        policy: ReleaseSupervisionPolicy = ReleaseSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, CapabilityLifecycle):
            raise TypeError("lifecycle must satisfy CapabilityLifecycle")
        if not isinstance(policy, ReleaseSupervisionPolicy):
            raise TypeError("policy must be ReleaseSupervisionPolicy")
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        if not callable(_clock):
            raise TypeError("_clock must be callable")
        self._lifecycle = lifecycle
        self._policy = policy
        self._sleep = _sleeper
        self._clock = _clock

    @property
    def lifecycle(self) -> CapabilityLifecycle[GenerationT, RequestT, CapabilityT]:
        return self._lifecycle

    @property
    def policy(self) -> ReleaseSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: GenerationT,
        request: RequestT,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> ReleaseSupervisionResult[GenerationT, RequestT, CapabilityT]:
        """Retry release failures/busy results until terminal or the caller stops waiting."""

        if generation is None:
            raise TypeError("generation cannot be None")
        if request is None:
            raise TypeError("request cannot be None")
        timeout = normalize_supervision_timeout(
            timeout_seconds,
            field_name="release supervision timeout",
        )
        validate_cancellation(cancellation)
        deadline = None if timeout is None else self._clock() + timeout
        attempts = 0

        while True:
            reason = stop_reason(
                deadline=deadline,
                cancellation=cancellation,
                clock=self._clock,
            )
            if reason is not None:
                return SupervisionStopped(reason, attempts)

            attempts += 1
            result = self._lifecycle.release(generation, request)
            disposition = classify_release_result(result, generation, request)
            if disposition not in (ReleaseDisposition.FAILED, ReleaseDisposition.BUSY):
                return result

            reason = stop_reason(
                deadline=deadline,
                cancellation=cancellation,
                clock=self._clock,
            )
            if reason is not None:
                return SupervisionStopped(reason, attempts)

            delay = retry_delay(
                self._policy.retry_seconds,
                deadline=deadline,
                clock=self._clock,
            )
            if delay <= 0.0:
                return SupervisionStopped(SupervisionStopReason.TIMED_OUT, attempts)
            self._sleep(delay)


__all__ = [
    "ReleaseDisposition",
    "ReleaseSupervisionResult",
    "ReleaseSupervisor",
    "classify_release_result",
]
