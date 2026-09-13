from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerGenerationMismatch,
    AdbServerLifecycleBusy,
    AdbServerReleaseAlreadyIdle,
    AdbServerReleaseFailed,
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


class AdbServerReleaseSupervisor:
    """Drive one server lifetime to a release-side terminal lifecycle result.

    Supervision never reads lifecycle state. It advances only from ``release`` results:

    - ``ReleaseSucceeded`` and ``ReleaseAlreadyIdle`` terminate in IDLE.
    - ``ReleaseFailed`` and ``LifecycleBusy`` retry the same generation/request target.
    - ``GenerationMismatch`` terminates without following the newer generation because
      release supervision is lifetime-oriented.
    - ``ReleaseRequestMismatch`` terminates without releasing the current request because
      it is not the requested lifetime target.

    Terminal lifecycle results are returned unchanged so orchestration can decide how to
    proceed without reconstructing state.
    """

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
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        self._releaser = releaser
        self._policy = policy
        self._sleep = _sleeper

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
        """Retry release failures/busy results until the target is terminal."""

        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")

        while True:
            result = self._releaser.release(generation, request)

            if isinstance(
                result,
                (
                    AdbServerReleaseSucceeded,
                    AdbServerReleaseAlreadyIdle,
                    AdbServerGenerationMismatch,
                    AdbServerReleaseRequestMismatch,
                ),
            ):
                return result

            if not isinstance(
                result,
                (AdbServerReleaseFailed, AdbServerLifecycleBusy),
            ):
                raise TypeError("releaser returned an unsupported AdbServerReleaseResult")

            self._sleep(self._policy.retry_seconds)


__all__ = [
    "AdbServerReleaser",
    "AdbServerReleaseSupervisionResult",
    "AdbServerReleaseSupervisor",
]
