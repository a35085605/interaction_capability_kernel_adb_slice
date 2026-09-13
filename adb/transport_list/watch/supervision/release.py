from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, TypeAlias, runtime_checkable

from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchGenerationMismatch,
    AdbTransportListWatchLifecycleBusy,
    AdbTransportListWatchReleaseAlreadyIdle,
    AdbTransportListWatchReleaseFailed,
    AdbTransportListWatchReleaseRequestMismatch,
    AdbTransportListWatchReleaseResult,
    AdbTransportListWatchReleaseSucceeded,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.supervision.policy import (
    AdbTransportListWatchReleaseSupervisionPolicy,
)


_Sleeper = Callable[[float], None]


@runtime_checkable
class AdbTransportListWatchReleaser(Protocol):
    """Narrow release-only lifecycle surface used by watch release supervision."""

    def release(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchReleaseResult: ...


AdbTransportListWatchReleaseSupervisionResult: TypeAlias = (
    AdbTransportListWatchReleaseSucceeded
    | AdbTransportListWatchReleaseAlreadyIdle
    | AdbTransportListWatchGenerationMismatch
    | AdbTransportListWatchReleaseRequestMismatch
)


class AdbTransportListWatchReleaseSupervisor:
    """Retry release failures/busy states until the requested watch lifetime is terminal."""

    def __init__(
        self,
        releaser: AdbTransportListWatchReleaser,
        *,
        policy: AdbTransportListWatchReleaseSupervisionPolicy = (
            AdbTransportListWatchReleaseSupervisionPolicy()
        ),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(releaser, AdbTransportListWatchReleaser):
            raise TypeError("releaser must satisfy AdbTransportListWatchReleaser")
        if not isinstance(policy, AdbTransportListWatchReleaseSupervisionPolicy):
            raise TypeError("policy must be AdbTransportListWatchReleaseSupervisionPolicy")
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        self._releaser = releaser
        self._policy = policy
        self._sleep = _sleeper

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchReleaseSupervisionResult:
        if not isinstance(generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")

        while True:
            result = self._releaser.release(generation, request)
            if isinstance(
                result,
                (
                    AdbTransportListWatchReleaseSucceeded,
                    AdbTransportListWatchReleaseAlreadyIdle,
                    AdbTransportListWatchGenerationMismatch,
                    AdbTransportListWatchReleaseRequestMismatch,
                ),
            ):
                return result
            if not isinstance(
                result,
                (AdbTransportListWatchReleaseFailed, AdbTransportListWatchLifecycleBusy),
            ):
                raise TypeError(
                    "releaser returned an unsupported AdbTransportListWatchReleaseResult"
                )
            self._sleep(self._policy.retry_seconds)


__all__ = [
    "AdbTransportListWatchReleaser",
    "AdbTransportListWatchReleaseSupervisionResult",
    "AdbTransportListWatchReleaseSupervisor",
]
