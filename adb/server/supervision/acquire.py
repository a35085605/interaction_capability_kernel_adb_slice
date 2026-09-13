from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import TypeAlias

from _lifecycle_new.capability.supervision.acquire import AcquireSupervisor
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerAcquireAlreadyActive,
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerAcquireRequestMismatch,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
    AdbServerLifecycle,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import AdbServerAcquireSupervisionPolicy


_Sleeper = Callable[[float], None]


AdbServerAcquireSupervisionResult: TypeAlias = (
    AdbServerAcquireSucceeded
    | AdbServerAcquireAlreadyActive
    | AdbServerAcquireFailed
    | AdbServerAcquireReleaseRequired
    | AdbServerGenerationMismatch
    | AdbServerAcquireRequestMismatch
)


class AdbServerAcquireSupervisor(
    AcquireSupervisor[AdbServerGeneration, AdbServerRequest, AdbServerCapability]
):
    """ADB server specialization of generation-scoped acquire supervision."""

    def __init__(
        self,
        lifecycle: AdbServerLifecycle,
        *,
        policy: AdbServerAcquireSupervisionPolicy = AdbServerAcquireSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if not isinstance(policy, AdbServerAcquireSupervisionPolicy):
            raise TypeError("policy must be AdbServerAcquireSupervisionPolicy")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper)

    @property
    def lifecycle(self) -> AdbServerLifecycle:
        return self._lifecycle

    @property
    def policy(self) -> AdbServerAcquireSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerAcquireSupervisionResult:
        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")

        result = super().supervise(generation, request)
        if isinstance(result, AdbServerGenerationMismatch):
            if not isinstance(result.current_generation, AdbServerGeneration):
                raise TypeError("server current generation must be AdbServerGeneration")
        elif isinstance(result, AdbServerAcquireRequestMismatch):
            if not isinstance(result.current_request, AdbServerRequest):
                raise TypeError("server current request must be AdbServerRequest")
        return result


__all__ = [
    "AdbServerAcquireSupervisionResult",
    "AdbServerAcquireSupervisor",
]
