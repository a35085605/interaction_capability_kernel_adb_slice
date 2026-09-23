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
    RecoverySupervisionPolicy,
    ReleaseSupervisionPolicy,
)
from lifecycle.capability.supervision.recovery import RecoverySupervisor
from lifecycle.capability.supervision.release import ReleaseSupervisor
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import AdbServerLifecycle, AdbServerLifecycleResult
from adb.server.request import AdbServerRequest


_Sleeper = Callable[[float], None]

AdbServerAcquireSupervisionPolicy = AcquireSupervisionPolicy
AdbServerRecoverySupervisionPolicy = RecoverySupervisionPolicy
AdbServerReleaseSupervisionPolicy = ReleaseSupervisionPolicy

AdbServerAcquireSupervisionResult: TypeAlias = (
    AdbServerLifecycleResult | SupervisionStopped
)
AdbServerRecoverySupervisionResult: TypeAlias = (
    AdbServerLifecycleResult | SupervisionStopped
)
AdbServerReleaseSupervisionResult: TypeAlias = (
    AdbServerLifecycleResult | SupervisionStopped
)


class _TypedServerSupervisor:
    @staticmethod
    def _validate(generation: AdbServerGeneration, request: AdbServerRequest) -> None:
        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")


class AdbServerAcquireSupervisor(
    _TypedServerSupervisor,
    AcquireSupervisor[AdbServerGeneration, AdbServerRequest, AdbServerCapability],
):
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
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    def supervise(
        self,
        generation: AdbServerGeneration,
        request: AdbServerRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerAcquireSupervisionResult:
        self._validate(generation, request)
        return super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )


class AdbServerRecoverySupervisor(
    _TypedServerSupervisor,
    RecoverySupervisor[AdbServerGeneration, AdbServerRequest, AdbServerCapability],
):
    def __init__(
        self,
        lifecycle: AdbServerLifecycle,
        *,
        policy: AdbServerRecoverySupervisionPolicy = AdbServerRecoverySupervisionPolicy(),
        _sleeper: _Sleeper = sleep,
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    def supervise(
        self,
        generation: AdbServerGeneration,
        request: AdbServerRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerRecoverySupervisionResult:
        self._validate(generation, request)
        return super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )


class AdbServerReleaseSupervisor(
    _TypedServerSupervisor,
    ReleaseSupervisor[AdbServerGeneration, AdbServerRequest, AdbServerCapability],
):
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
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    def supervise(
        self,
        generation: AdbServerGeneration,
        request: AdbServerRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerReleaseSupervisionResult:
        self._validate(generation, request)
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
    "AdbServerRecoverySupervisionPolicy",
    "AdbServerRecoverySupervisionResult",
    "AdbServerRecoverySupervisor",
    "AdbServerReleaseSupervisionPolicy",
    "AdbServerReleaseSupervisionResult",
    "AdbServerReleaseSupervisor",
]
