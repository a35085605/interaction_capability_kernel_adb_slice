from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from _lifecycle_new.capability.result import (
    GenerationMismatch,
    LifecycleBusy,
    ReleaseAlreadyIdle,
    ReleaseFailed,
    ReleaseRequestMismatch,
    ReleaseResult,
    ReleaseSucceeded,
)
from _lifecycle_new.capability.supervision.policy import ReleaseSupervisionPolicy


GenerationT = TypeVar("GenerationT")
RequestT = TypeVar("RequestT")
CapabilityT = TypeVar("CapabilityT")

_Sleeper = Callable[[float], None]


@runtime_checkable
class Releaser(Protocol[GenerationT, RequestT, CapabilityT]):
    """Narrow release-only lifecycle surface used by release supervision."""

    def release(
        self,
        expected_generation: GenerationT,
        request: RequestT,
    ) -> ReleaseResult[GenerationT, RequestT, CapabilityT]: ...


ReleaseSupervisionResult: TypeAlias = (
    ReleaseSucceeded[GenerationT]
    | ReleaseAlreadyIdle
    | GenerationMismatch[GenerationT]
    | ReleaseRequestMismatch[RequestT]
)


class ReleaseSupervisor(Generic[GenerationT, RequestT, CapabilityT]):
    """Drive one request to a release-side terminal lifecycle result.

    Supervision is generation-scoped. ``ReleaseFailed`` and ``LifecycleBusy``
    are retried for the same generation/request pair; terminal results are
    returned unchanged and a newer generation is never followed.
    """

    def __init__(
        self,
        releaser: Releaser[GenerationT, RequestT, CapabilityT],
        *,
        policy: ReleaseSupervisionPolicy = ReleaseSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(releaser, Releaser):
            raise TypeError("releaser must satisfy Releaser")
        if not isinstance(policy, ReleaseSupervisionPolicy):
            raise TypeError("policy must be ReleaseSupervisionPolicy")
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        self._releaser = releaser
        self._policy = policy
        self._sleep = _sleeper

    @property
    def releaser(self) -> Releaser[GenerationT, RequestT, CapabilityT]:
        return self._releaser

    @property
    def policy(self) -> ReleaseSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: GenerationT,
        request: RequestT,
    ) -> ReleaseSupervisionResult[GenerationT, RequestT]:
        """Retry release failures/busy results until the target becomes terminal."""

        if generation is None:
            raise TypeError("generation cannot be None")
        if request is None:
            raise TypeError("request cannot be None")

        while True:
            result = self._releaser.release(generation, request)
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
                raise TypeError("releaser returned an unsupported ReleaseResult")
            self._sleep(self._policy.retry_seconds)


__all__ = ["Releaser", "ReleaseSupervisionResult", "ReleaseSupervisor"]
