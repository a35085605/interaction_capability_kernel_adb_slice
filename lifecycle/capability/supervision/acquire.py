from __future__ import annotations

from collections.abc import Callable
from time import monotonic, sleep
from typing import Generic, TypeAlias, TypeVar

from lifecycle.capability.lifecycle import CapabilityLifecycle
from lifecycle.capability.result import (
    AcquireAlreadyActive,
    AcquireFailed,
    AcquireReleaseRequired,
    AcquireRequestMismatch,
    AcquireSucceeded,
    GenerationMismatch,
    LifecycleBusy,
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
from lifecycle.capability.supervision.policy import AcquireSupervisionPolicy


GenerationT = TypeVar("GenerationT")
RequestT = TypeVar("RequestT")
CapabilityT = TypeVar("CapabilityT")

_Sleeper = Callable[[float], None]


AcquireSupervisionResult: TypeAlias = (
    AcquireSucceeded[GenerationT, RequestT, CapabilityT]
    | AcquireAlreadyActive[GenerationT, RequestT, CapabilityT]
    | AcquireFailed[GenerationT, RequestT, CapabilityT]
    | AcquireReleaseRequired[GenerationT, RequestT, CapabilityT]
    | GenerationMismatch[GenerationT]
    | AcquireRequestMismatch[RequestT]
    | SupervisionStopped
)


class AcquireSupervisor(Generic[GenerationT, RequestT, CapabilityT]):
    """Drive one request to an acquisition-side terminal lifecycle result.

    Supervision is generation-scoped. ``LifecycleBusy`` is retried for the same
    generation/request pair; every other supported lifecycle result is returned
    unchanged. Callers may supply a timeout and/or cancellation signal to bound the
    retry loop. A bound never changes lifecycle ownership; it only stops waiting.
    """

    def __init__(
        self,
        lifecycle: CapabilityLifecycle[GenerationT, RequestT, CapabilityT],
        *,
        policy: AcquireSupervisionPolicy = AcquireSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, CapabilityLifecycle):
            raise TypeError("lifecycle must satisfy CapabilityLifecycle")
        if not isinstance(policy, AcquireSupervisionPolicy):
            raise TypeError("policy must be AcquireSupervisionPolicy")
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
    def policy(self) -> AcquireSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: GenerationT,
        request: RequestT,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AcquireSupervisionResult[GenerationT, RequestT, CapabilityT]:
        """Retry busy results until terminal or until the caller's bound stops waiting."""

        if generation is None:
            raise TypeError("generation cannot be None")
        if request is None:
            raise TypeError("request cannot be None")
        timeout = normalize_supervision_timeout(
            timeout_seconds,
            field_name="acquire supervision timeout",
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
            result = self._lifecycle.acquire(generation, request)
            if isinstance(
                result,
                (
                    AcquireSucceeded,
                    AcquireAlreadyActive,
                    AcquireFailed,
                    AcquireReleaseRequired,
                    GenerationMismatch,
                    AcquireRequestMismatch,
                ),
            ):
                return result
            if not isinstance(result, LifecycleBusy):
                raise TypeError("lifecycle returned an unsupported AcquireResult")

            reason = stop_reason(
                deadline=deadline,
                cancellation=cancellation,
                clock=self._clock,
            )
            if reason is not None:
                return SupervisionStopped(reason, attempts)

            delay = retry_delay(
                self._policy.deferred_retry_seconds,
                deadline=deadline,
                clock=self._clock,
            )
            if delay <= 0.0:
                return SupervisionStopped(SupervisionStopReason.TIMED_OUT, attempts)
            self._sleep(delay)


__all__ = ["AcquireSupervisionResult", "AcquireSupervisor"]
