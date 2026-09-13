from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.supervision.acquire import AcquireSupervisor
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerAcquireAlreadyActive,
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerAcquireRequestMismatch,
    AdbServerAcquireResult,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import AdbServerAcquireSupervisionPolicy


_Sleeper = Callable[[float], None]


@runtime_checkable
class AdbServerAcquirer(Protocol):
    """Narrow acquire-only lifecycle surface used by acquisition supervision."""

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerAcquireResult: ...


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
        acquirer: AdbServerAcquirer,
        *,
        policy: AdbServerAcquireSupervisionPolicy = AdbServerAcquireSupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(acquirer, AdbServerAcquirer):
            raise TypeError("acquirer must satisfy AdbServerAcquirer")
        if not isinstance(policy, AdbServerAcquireSupervisionPolicy):
            raise TypeError("policy must be AdbServerAcquireSupervisionPolicy")
        super().__init__(acquirer, policy=policy, _sleeper=_sleeper)

    @property
    def acquirer(self) -> AdbServerAcquirer:
        return self._acquirer

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
    "AdbServerAcquirer",
    "AdbServerAcquireSupervisionResult",
    "AdbServerAcquireSupervisor",
]
