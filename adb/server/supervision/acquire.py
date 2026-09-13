from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerAcquireAlreadyActive,
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerAcquireRequestMismatch,
    AdbServerAcquireResult,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
    AdbServerLifecycleBusy,
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


class AdbServerAcquireSupervisor:
    """Drive one server request to an acquisition-side terminal lifecycle result.

    Supervision never reads lifecycle state. It advances only from ``acquire`` results:

    - ``AcquireSucceeded`` and ``AcquireAlreadyActive`` terminate in ACTIVE.
    - ``AcquireFailed`` and ``AcquireReleaseRequired`` terminate in RELEASE_REQUIRED.
    - ``GenerationMismatch`` terminates without following the newer generation.
    - ``AcquireRequestMismatch`` terminates because the requested generation belongs to
      another request.
    - ``LifecycleBusy`` is the only deferred result and retries the same generation/request.

    Supervision is generation-scoped: crossing into a newer generation is an orchestration
    decision, not an acquire-supervision side effect. Terminal lifecycle results are returned
    unchanged so orchestration can decide whether and how to continue.
    """

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
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        self._acquirer = acquirer
        self._policy = policy
        self._sleep = _sleeper

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
        """Retry same-generation busy results until a terminal acquire result is reached."""

        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")

        while True:
            result = self._acquirer.acquire(generation, request)

            if isinstance(result, AdbServerGenerationMismatch):
                if not isinstance(result.current_generation, AdbServerGeneration):
                    raise TypeError(
                        "server current generation must be AdbServerGeneration"
                    )
                return result

            if isinstance(result, AdbServerAcquireRequestMismatch):
                if not isinstance(result.current_request, AdbServerRequest):
                    raise TypeError("server current request must be AdbServerRequest")
                return result

            if isinstance(
                result,
                (
                    AdbServerAcquireSucceeded,
                    AdbServerAcquireAlreadyActive,
                    AdbServerAcquireFailed,
                    AdbServerAcquireReleaseRequired,
                ),
            ):
                return result

            if not isinstance(result, AdbServerLifecycleBusy):
                raise TypeError("acquirer returned an unsupported AdbServerAcquireResult")

            self._sleep(self._policy.deferred_retry_seconds)


__all__ = [
    "AdbServerAcquirer",
    "AdbServerAcquireSupervisionResult",
    "AdbServerAcquireSupervisor",
]
