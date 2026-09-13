from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.supervision.release import ReleaseSupervisor
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerGenerationMismatch,
    AdbServerReleaseAlreadyIdle,
    AdbServerReleaseRequestMismatch,
    AdbServerReleaseResult,
    AdbServerReleaseSucceeded,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import AdbServerReleaseSupervisionPolicy


_Sleeper = Callable[[float], None]


@runtime_checkable
class AdbServerReleaser(Protocol):
    """Narrow release-only lifecycle surface used by release supervision."""

    def release(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerReleaseResult: ...


AdbServerReleaseSupervisionResult: TypeAlias = (
    AdbServerReleaseSucceeded
    | AdbServerReleaseAlreadyIdle
    | AdbServerGenerationMismatch
    | AdbServerReleaseRequestMismatch
)


class AdbServerReleaseSupervisor(
    ReleaseSupervisor[AdbServerGeneration, AdbServerRequest, AdbServerCapability]
):
    """ADB server specialization of generation-scoped release supervision."""

    def __init__(
        self,
        releaser: AdbServerReleaser,
        *,
        policy: AdbServerReleaseSupervisionPolicy = AdbServerReleaseSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(releaser, AdbServerReleaser):
            raise TypeError("releaser must satisfy AdbServerReleaser")
        if not isinstance(policy, AdbServerReleaseSupervisionPolicy):
            raise TypeError("policy must be AdbServerReleaseSupervisionPolicy")
        super().__init__(releaser, policy=policy, _sleeper=_sleeper)

    @property
    def releaser(self) -> AdbServerReleaser:
        return self._releaser

    @property
    def policy(self) -> AdbServerReleaseSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerReleaseSupervisionResult:
        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        return super().supervise(generation, request)


__all__ = [
    "AdbServerReleaser",
    "AdbServerReleaseSupervisionResult",
    "AdbServerReleaseSupervisor",
]
