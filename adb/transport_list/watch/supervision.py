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
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchLifecycle,
    AdbTransportListWatchLifecycleResult,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream


_Sleeper = Callable[[float], None]

AdbTransportListWatchAcquireSupervisionPolicy = AcquireSupervisionPolicy
AdbTransportListWatchRecoverySupervisionPolicy = RecoverySupervisionPolicy
AdbTransportListWatchReleaseSupervisionPolicy = ReleaseSupervisionPolicy

AdbTransportListWatchAcquireSupervisionResult: TypeAlias = (
    AdbTransportListWatchLifecycleResult | SupervisionStopped
)
AdbTransportListWatchRecoverySupervisionResult: TypeAlias = (
    AdbTransportListWatchLifecycleResult | SupervisionStopped
)
AdbTransportListWatchReleaseSupervisionResult: TypeAlias = (
    AdbTransportListWatchLifecycleResult | SupervisionStopped
)


class _TypedWatchSupervisor:
    @staticmethod
    def _validate(
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> None:
        if not isinstance(generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")


class AdbTransportListWatchAcquireSupervisor(
    _TypedWatchSupervisor,
    AcquireSupervisor[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchRequest,
        AdbTransportListWatchStream,
    ],
):
    def __init__(
        self,
        lifecycle: AdbTransportListWatchLifecycle,
        *,
        policy: AdbTransportListWatchAcquireSupervisionPolicy = (
            AdbTransportListWatchAcquireSupervisionPolicy()
        ),
        _sleeper: _Sleeper = sleep,
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, AdbTransportListWatchLifecycle):
            raise TypeError("lifecycle must satisfy AdbTransportListWatchLifecycle")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbTransportListWatchAcquireSupervisionResult:
        self._validate(generation, request)
        return super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )


class AdbTransportListWatchRecoverySupervisor(
    _TypedWatchSupervisor,
    RecoverySupervisor[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchRequest,
        AdbTransportListWatchStream,
    ],
):
    def __init__(
        self,
        lifecycle: AdbTransportListWatchLifecycle,
        *,
        policy: AdbTransportListWatchRecoverySupervisionPolicy = (
            AdbTransportListWatchRecoverySupervisionPolicy()
        ),
        _sleeper: _Sleeper = sleep,
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, AdbTransportListWatchLifecycle):
            raise TypeError("lifecycle must satisfy AdbTransportListWatchLifecycle")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbTransportListWatchRecoverySupervisionResult:
        self._validate(generation, request)
        return super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )


class AdbTransportListWatchReleaseSupervisor(
    _TypedWatchSupervisor,
    ReleaseSupervisor[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchRequest,
        AdbTransportListWatchStream,
    ],
):
    def __init__(
        self,
        lifecycle: AdbTransportListWatchLifecycle,
        *,
        policy: AdbTransportListWatchReleaseSupervisionPolicy = (
            AdbTransportListWatchReleaseSupervisionPolicy()
        ),
        _sleeper: _Sleeper = sleep,
        _clock: Clock = monotonic,
    ) -> None:
        if not isinstance(lifecycle, AdbTransportListWatchLifecycle):
            raise TypeError("lifecycle must satisfy AdbTransportListWatchLifecycle")
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbTransportListWatchReleaseSupervisionResult:
        self._validate(generation, request)
        return super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )


__all__ = [
    "AdbTransportListWatchAcquireSupervisionPolicy",
    "AdbTransportListWatchAcquireSupervisionResult",
    "AdbTransportListWatchAcquireSupervisor",
    "AdbTransportListWatchRecoverySupervisionPolicy",
    "AdbTransportListWatchRecoverySupervisionResult",
    "AdbTransportListWatchRecoverySupervisor",
    "AdbTransportListWatchReleaseSupervisionPolicy",
    "AdbTransportListWatchReleaseSupervisionResult",
    "AdbTransportListWatchReleaseSupervisor",
]
