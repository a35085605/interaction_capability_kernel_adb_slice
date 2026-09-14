from __future__ import annotations

from collections.abc import Callable
from time import sleep
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
)


class AcquireSupervisor(Generic[GenerationT, RequestT, CapabilityT]):
    """Drive one request to an acquisition-side terminal lifecycle result.

    Supervision is generation-scoped. ``LifecycleBusy`` is retried for the same
    generation/request pair; every other supported result is returned unchanged.
    In particular, ``GenerationMismatch`` is reported rather than followed.
    """

    def __init__(
        self,
        lifecycle: CapabilityLifecycle[GenerationT, RequestT, CapabilityT],
        *,
        policy: AcquireSupervisionPolicy = AcquireSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(lifecycle, CapabilityLifecycle):
            raise TypeError("lifecycle must satisfy CapabilityLifecycle")
        if not isinstance(policy, AcquireSupervisionPolicy):
            raise TypeError("policy must be AcquireSupervisionPolicy")
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        self._lifecycle = lifecycle
        self._policy = policy
        self._sleep = _sleeper

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
    ) -> AcquireSupervisionResult[GenerationT, RequestT, CapabilityT]:
        """Retry busy results until the same-generation target becomes terminal."""

        if generation is None:
            raise TypeError("generation cannot be None")
        if request is None:
            raise TypeError("request cannot be None")

        while True:
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
            self._sleep(self._policy.deferred_retry_seconds)


__all__ = ["AcquireSupervisionResult", "AcquireSupervisor"]
