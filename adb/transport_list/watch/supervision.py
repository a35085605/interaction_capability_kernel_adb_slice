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
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAcquireAlreadyActive,
    AdbTransportListWatchAcquireFailed,
    AdbTransportListWatchAcquireReleaseRequired,
    AdbTransportListWatchAcquireRequestMismatch,
    AdbTransportListWatchAcquireSucceeded,
    AdbTransportListWatchGenerationMismatch,
    AdbTransportListWatchLifecycle,
    AdbTransportListWatchReleaseAlreadyIdle,
    AdbTransportListWatchReleaseRequestMismatch,
    AdbTransportListWatchReleaseSucceeded,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream


_Sleeper = Callable[[float], None]

AdbTransportListWatchAcquireSupervisionPolicy = AcquireSupervisionPolicy
AdbTransportListWatchReleaseSupervisionPolicy = ReleaseSupervisionPolicy

AdbTransportListWatchAcquireSupervisionResult: TypeAlias = (
    AdbTransportListWatchAcquireSucceeded
    | AdbTransportListWatchAcquireAlreadyActive
    | AdbTransportListWatchAcquireFailed
    | AdbTransportListWatchAcquireReleaseRequired
    | AdbTransportListWatchGenerationMismatch
    | AdbTransportListWatchAcquireRequestMismatch
    | SupervisionStopped
)

AdbTransportListWatchReleaseSupervisionResult: TypeAlias = (
    AdbTransportListWatchReleaseSucceeded
    | AdbTransportListWatchReleaseAlreadyIdle
    | AdbTransportListWatchGenerationMismatch
    | AdbTransportListWatchReleaseRequestMismatch
    | SupervisionStopped
)


class AdbTransportListWatchAcquireSupervisor(
    AcquireSupervisor[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchRequest,
        AdbTransportListWatchStream,
    ]
):
    """Watch specialization of generation-scoped acquire supervision."""

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
        if not isinstance(policy, AdbTransportListWatchAcquireSupervisionPolicy):
            raise TypeError(
                "policy must be AdbTransportListWatchAcquireSupervisionPolicy"
            )
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    @property
    def lifecycle(self) -> AdbTransportListWatchLifecycle:
        return self._lifecycle

    @property
    def policy(self) -> AdbTransportListWatchAcquireSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbTransportListWatchAcquireSupervisionResult:
        if not isinstance(generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")

        result = super().supervise(
            generation,
            request,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        if isinstance(result, AdbTransportListWatchGenerationMismatch):
            if not isinstance(
                result.current_generation,
                AdbTransportListWatchGeneration,
            ):
                raise TypeError(
                    "watch current generation must be AdbTransportListWatchGeneration"
                )
        elif isinstance(result, AdbTransportListWatchAcquireRequestMismatch):
            if not isinstance(result.current_request, AdbTransportListWatchRequest):
                raise TypeError(
                    "watch current request must be AdbTransportListWatchRequest"
                )
        return result


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
        if not isinstance(policy, AdbTransportListWatchReleaseSupervisionPolicy):
            raise TypeError(
                "policy must be AdbTransportListWatchReleaseSupervisionPolicy"
            )
        super().__init__(lifecycle, policy=policy, _sleeper=_sleeper, _clock=_clock)

    @property
    def lifecycle(self) -> AdbTransportListWatchLifecycle:
        return self._lifecycle

    @property
    def policy(self) -> AdbTransportListWatchReleaseSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> AdbTransportListWatchReleaseSupervisionResult:
        if not isinstance(generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")
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
    "AdbTransportListWatchReleaseSupervisionPolicy",
    "AdbTransportListWatchReleaseSupervisionResult",
    "AdbTransportListWatchReleaseSupervisor",
]
