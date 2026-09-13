from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.supervision.acquire import AcquireSupervisor
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAcquireAlreadyActive,
    AdbTransportListWatchAcquireFailed,
    AdbTransportListWatchAcquireReleaseRequired,
    AdbTransportListWatchAcquireRequestMismatch,
    AdbTransportListWatchAcquireResult,
    AdbTransportListWatchAcquireSucceeded,
    AdbTransportListWatchGenerationMismatch,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream
from adb.transport_list.watch.supervision.policy import (
    AdbTransportListWatchAcquireSupervisionPolicy,
)


_Sleeper = Callable[[float], None]


@runtime_checkable
class AdbTransportListWatchAcquirer(Protocol):
    """Narrow acquire-only lifecycle surface used by watch acquisition supervision."""

    def acquire(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchAcquireResult: ...


AdbTransportListWatchAcquireSupervisionResult: TypeAlias = (
    AdbTransportListWatchAcquireSucceeded
    | AdbTransportListWatchAcquireAlreadyActive
    | AdbTransportListWatchAcquireFailed
    | AdbTransportListWatchAcquireReleaseRequired
    | AdbTransportListWatchGenerationMismatch
    | AdbTransportListWatchAcquireRequestMismatch
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
        acquirer: AdbTransportListWatchAcquirer,
        *,
        policy: AdbTransportListWatchAcquireSupervisionPolicy = (
            AdbTransportListWatchAcquireSupervisionPolicy()
        ),
        _sleeper: _Sleeper = sleep,
    ) -> None:
        if not isinstance(acquirer, AdbTransportListWatchAcquirer):
            raise TypeError("acquirer must satisfy AdbTransportListWatchAcquirer")
        if not isinstance(policy, AdbTransportListWatchAcquireSupervisionPolicy):
            raise TypeError(
                "policy must be AdbTransportListWatchAcquireSupervisionPolicy"
            )
        super().__init__(acquirer, policy=policy, _sleeper=_sleeper)

    @property
    def acquirer(self) -> AdbTransportListWatchAcquirer:
        return self._acquirer

    @property
    def policy(self) -> AdbTransportListWatchAcquireSupervisionPolicy:
        return self._policy

    def supervise(
        self,
        generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchAcquireSupervisionResult:
        if not isinstance(generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")

        result = super().supervise(generation, request)
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


__all__ = [
    "AdbTransportListWatchAcquirer",
    "AdbTransportListWatchAcquireSupervisionResult",
    "AdbTransportListWatchAcquireSupervisor",
]
