from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.supervision.release import ReleaseSupervisor
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchGenerationMismatch,
    AdbTransportListWatchReleaseAlreadyIdle,
    AdbTransportListWatchReleaseRequestMismatch,
    AdbTransportListWatchReleaseResult,
    AdbTransportListWatchReleaseSucceeded,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream
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


class AdbTransportListWatchReleaseSupervisor(
    ReleaseSupervisor[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchRequest,
        AdbTransportListWatchStream,
    ]
):
    """Watch specialization of generation-scoped release supervision."""

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
            raise TypeError(
                "policy must be AdbTransportListWatchReleaseSupervisionPolicy"
            )
        super().__init__(releaser, policy=policy, _sleeper=_sleeper)

    @property
    def releaser(self) -> AdbTransportListWatchReleaser:
        return self._releaser

    @property
    def policy(self) -> AdbTransportListWatchReleaseSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchReleaseSupervisionResult:
        if not isinstance(generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")
        return super().supervise(generation, request)


__all__ = [
    "AdbTransportListWatchReleaser",
    "AdbTransportListWatchReleaseSupervisionResult",
    "AdbTransportListWatchReleaseSupervisor",
]
