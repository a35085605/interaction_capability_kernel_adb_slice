from __future__ import annotations

from collections.abc import Callable
from time import monotonic, sleep
from typing import Generic, TypeAlias, TypeVar

from lifecycle.capability.lifecycle import CapabilityLifecycle
from lifecycle.capability.result import (
    GenerationMismatch,
    LifecycleBusy,
    ReleaseAlreadyIdle,
    ReleaseFailed,
    ReleaseRequestMismatch,
    ReleaseSucceeded,
)
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


ReleaseSupervisionResult: TypeAlias = (
    ReleaseSucceeded[GenerationT]
    | ReleaseAlreadyIdle
    | GenerationMismatch[GenerationT]
    | ReleaseRequestMismatch[RequestT]
    | SupervisionStopped
)


class ReleaseSupervisor(Generic[GenerationT, RequestT, CapabilityT]):
    """Drive one request to a release-side terminal lifecycle result.

    Supervision is generation-scoped. ``ReleaseFailed`` and ``LifecycleBusy`` are
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
    ) -> ReleaseSupervisionResult[GenerationT, RequestT]:
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
            if isinstance(
                result,
                (
                    ReleaseSucceeded,
                    ReleaseAlreadyIdle,
                    GenerationMismatch,
                    ReleaseRequestMismatch,
                ),
            ):
                return result
            if not isinstance(result, (ReleaseFailed, LifecycleBusy)):
                raise TypeError("lifecycle returned an unsupported ReleaseResult")

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


__all__ = ["ReleaseSupervisionResult", "ReleaseSupervisor"]
