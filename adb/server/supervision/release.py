from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import TypeAlias

from _lifecycle_new.capability.supervision.release import ReleaseSupervisor
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerGenerationMismatch,
    AdbServerLifecycle,
    AdbServerReleaseAlreadyIdle,
    AdbServerReleaseRequestMismatch,
    AdbServerReleaseSucceeded,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import AdbServerReleaseSupervisionPolicy


_Sleeper = Callable[[float], None]


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
        lifecycle: AdbServerLifecycle,
        *,
        policy: AdbServerReleaseSupervisionPolicy = AdbServerReleaseSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if not isinstance(policy, AdbServerReleaseSupervisionPolicy):
            raise TypeError("policy must be AdbServerReleaseSupervisionPolicy")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper)

    @property
    def lifecycle(self) -> AdbServerLifecycle:
        return self._lifecycle

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
    "AdbServerReleaseSupervisionResult",
    "AdbServerReleaseSupervisor",
]
