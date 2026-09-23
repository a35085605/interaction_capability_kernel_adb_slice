from __future__ import annotations

from collections.abc import Callable
from time import monotonic, sleep
from typing import TypeAlias

from lifecycle.capability.supervision.acquire import AcquireSupervisor
from lifecycle.capability.supervision.control import (
    CancellationSignal,
    Clock,
    SupervisionStopped,
)
from lifecycle.capability.supervision.policy import (
    AcquireSupervisionPolicy,
    ReleaseSupervisionPolicy,
)
from lifecycle.capability.supervision.release import ReleaseSupervisor
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import AdbServerLifecycle, AdbServerLifecycleResult
from adb.server.request import AdbServerRequest


_Sleeper = Callable[[float], None]

AdbServerAcquireSupervisionPolicy = AcquireSupervisionPolicy
AdbServerReleaseSupervisionPolicy = ReleaseSupervisionPolicy

AdbServerAcquireSupervisionResult: TypeAlias = (
    AdbServerLifecycleResult | SupervisionStopped
)
AdbServerReleaseSupervisionResult: TypeAlias = (
    AdbServerLifecycleResult | SupervisionStopped
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
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if not isinstance(policy, AdbServerAcquireSupervisionPolicy):
            raise TypeError("policy must be AdbServerAcquireSupervisionPolicy")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

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
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerAcquireSupervisionResult:
        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        return super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
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
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if not isinstance(policy, AdbServerReleaseSupervisionPolicy):
            raise TypeError("policy must be AdbServerReleaseSupervisionPolicy")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

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
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerReleaseSupervisionResult:
        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        return super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )


__all__ = [
    "AdbServerAcquireSupervisionPolicy",
    "AdbServerAcquireSupervisionResult",
    "AdbServerAcquireSupervisor",
    "AdbServerReleaseSupervisionPolicy",
    "AdbServerReleaseSupervisionResult",
    "AdbServerReleaseSupervisor",
]
