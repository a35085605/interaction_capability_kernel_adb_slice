from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from random import random
from time import sleep
from typing import TypeAlias

from lifecycle.capability.supervision.control import (
    CancellationSignal,
    SupervisionStopReason,
)

from adb._recovery import (
    RandomSource,
    RecoveryAttempt,
    RecoveryExhausted,
    RecoveryOutcome,
    RecoveryRetryConfiguration,
    RecoveryRetryController,
    RecoverySucceeded,
    normalize_recovery_retry_configuration,
)
from adb.runtime.server.mutation import (
    AdbServerActivateAlreadyActive,
    AdbServerActivateConflict,
    AdbServerActivateFailed,
    AdbServerActivateIncomplete,
    AdbServerActivateReleaseRequired,
    AdbServerActivateSucceeded,
    AdbServerDeactivateIncomplete,
)
from adb.runtime.server.runtime import AdbServerRuntime
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerPhase, AdbServerSnapshot


_Sleeper = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityPolicy:
    """Cross-generation retry policy for obtaining one usable ADB server."""

    retry_initial_seconds: float = 0.1
    retry_max_seconds: float = 5.0
    retry_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.1
    deferred_retry_seconds: float = 0.1
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        configuration = normalize_recovery_retry_configuration(
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
            retry_multiplier=self.retry_multiplier,
            retry_jitter_ratio=self.retry_jitter_ratio,
            deferred_retry_seconds=self.deferred_retry_seconds,
            max_attempts=self.max_attempts,
            subject="ADB server availability",
        )
        object.__setattr__(
            self,
            "retry_initial_seconds",
            configuration.retry_initial_seconds,
        )
        object.__setattr__(self, "retry_max_seconds", configuration.retry_max_seconds)
        object.__setattr__(self, "retry_multiplier", configuration.retry_multiplier)
        object.__setattr__(self, "retry_jitter_ratio", configuration.retry_jitter_ratio)
        object.__setattr__(
            self,
            "deferred_retry_seconds",
            configuration.deferred_retry_seconds,
        )
        object.__setattr__(self, "max_attempts", configuration.max_attempts)

    def _configuration(self) -> RecoveryRetryConfiguration:
        return RecoveryRetryConfiguration(
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
            retry_multiplier=self.retry_multiplier,
            retry_jitter_ratio=self.retry_jitter_ratio,
            deferred_retry_seconds=self.deferred_retry_seconds,
            max_attempts=self.max_attempts,
        )


@dataclass(frozen=True, slots=True)
class AdbServerAvailable:
    """Report that the requested server is active with a usable capability."""

    snapshot: AdbServerSnapshot
    attempts: int
    failed_attempts: int

    def __post_init__(self) -> None:
        if self.snapshot.phase is not AdbServerPhase.ACTIVE:
            raise ValueError("available server snapshot must be active")
        if self.snapshot.capability is None:
            raise ValueError("available server snapshot must expose a capability")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts < 0:
            raise ValueError("attempts must be greater than or equal to zero")
        if isinstance(self.failed_attempts, bool) or not isinstance(self.failed_attempts, int):
            raise TypeError("failed_attempts must be an integer")
        if not 0 <= self.failed_attempts <= self.attempts:
            raise ValueError("failed_attempts must be between zero and attempts")


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityConflict:
    """Report that another healthy server request already owns this Runtime scope."""

    current_request: AdbServerRequest

    def __post_init__(self) -> None:
        if not isinstance(self.current_request, AdbServerRequest):
            raise TypeError("current_request must be AdbServerRequest")


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityIncomplete:
    """Report that recovery stopped because one bounded server command did not finish."""

    snapshot: AdbServerSnapshot
    reason: SupervisionStopReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, SupervisionStopReason):
            raise TypeError("reason must be SupervisionStopReason")


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityFailed:
    """Report exhausted cross-generation recovery after the last generation was released."""

    snapshot: AdbServerSnapshot
    failed_attempts: int
    cause: BaseException

    def __post_init__(self) -> None:
        if self.snapshot.phase is not AdbServerPhase.IDLE:
            raise ValueError("failed availability snapshot must be idle after cleanup")
        if isinstance(self.failed_attempts, bool) or not isinstance(self.failed_attempts, int):
            raise TypeError("failed_attempts must be an integer")
        if self.failed_attempts <= 0:
            raise ValueError("failed_attempts must be greater than zero")
        if not isinstance(self.cause, BaseException):
            raise TypeError("cause must be a BaseException")


AdbServerAvailabilityResult: TypeAlias = (
    AdbServerAvailable
    | AdbServerAvailabilityConflict
    | AdbServerAvailabilityFailed
    | AdbServerAvailabilityIncomplete
)


class AdbServerAvailabilitySupervisor:
    """Recover across server generations until ``request`` is usable or terminal.

    A failed activation is not a completed recovery attempt until its generation has
    been deactivated successfully. Existing ``RELEASE_REQUIRED`` debt is normalized
    before the retry budget starts and therefore does not consume an attempt. A
    conflicting healthy request is terminal and is never implicitly replaced.
    """

    __slots__ = ("_policy", "_random", "_runtime", "_sleep")

    def __init__(
        self,
        runtime: AdbServerRuntime,
        *,
        policy: AdbServerAvailabilityPolicy = AdbServerAvailabilityPolicy(),
        _sleeper: _Sleeper = sleep,
        _random: RandomSource = random,
    ) -> None:
        if not isinstance(runtime, AdbServerRuntime):
            raise TypeError("runtime must be AdbServerRuntime")
        if not isinstance(policy, AdbServerAvailabilityPolicy):
            raise TypeError("policy must be AdbServerAvailabilityPolicy")
        if not callable(_sleeper):
            raise TypeError("_sleeper must be callable")
        if not callable(_random):
            raise TypeError("_random must be callable")
        self._runtime = runtime
        self._policy = policy
        self._sleep = _sleeper
        self._random = _random

    @property
    def runtime(self) -> AdbServerRuntime:
        return self._runtime

    @property
    def policy(self) -> AdbServerAvailabilityPolicy:
        return self._policy

    def supervise(
        self,
        request: AdbServerRequest,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerAvailabilityResult:
        """Drive ``request`` to an active capability across as many generations as allowed."""

        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")

        normalized = self._normalize_existing_state(request, cancellation=cancellation)
        if normalized is not None:
            return normalized

        retry = RecoveryRetryController(
            self._policy._configuration(),
            _random=self._random,
            random_source_error=(
                "ADB server availability random source must return a value in [0, 1]"
            ),
        )
        decision = retry.begin()
        last_failure: BaseException | None = None

        while isinstance(decision, RecoveryAttempt):
            if decision.delay_seconds > 0.0:
                self._sleep(decision.delay_seconds)

            with self._runtime.commands._exclusive(
                timeout_seconds=self._runtime.commands.policy.activate_timeout_seconds,
                cancellation=cancellation,
            ) as stop_reason:
                if stop_reason is not None:
                    return AdbServerAvailabilityIncomplete(
                        self._runtime.snapshot.read(),
                        stop_reason,
                    )
                result = self._runtime.commands.activate(
                    request,
                    cancellation=cancellation,
                )

                if isinstance(result, AdbServerActivateIncomplete):
                    return AdbServerAvailabilityIncomplete(result.snapshot, result.reason)

                if isinstance(
                    result,
                    (AdbServerActivateSucceeded, AdbServerActivateAlreadyActive),
                ):
                    terminal = retry.decide_after(RecoveryOutcome.SUCCEEDED)
                    if not isinstance(terminal, RecoverySucceeded):
                        raise RuntimeError(
                            "successful recovery produced a non-terminal retry decision"
                        )
                    return AdbServerAvailable(
                        result.snapshot,
                        attempts=retry.attempt_number,
                        failed_attempts=retry.failed_attempts,
                    )

                if isinstance(result, AdbServerActivateConflict):
                    return AdbServerAvailabilityConflict(result.current_request)

                if isinstance(result, AdbServerActivateFailed):
                    last_failure = result.snapshot.last_error
                    if last_failure is None:
                        raise RuntimeError("failed server activation is missing its failure cause")
                    deactivated = self._runtime.commands.deactivate(
                        cancellation=cancellation
                    )
                    if isinstance(deactivated, AdbServerDeactivateIncomplete):
                        return AdbServerAvailabilityIncomplete(
                            deactivated.snapshot,
                            deactivated.reason,
                        )
                    idle_snapshot = deactivated.snapshot
                    outcome = RecoveryOutcome.FAILED
                elif isinstance(result, AdbServerActivateReleaseRequired):
                    # This attempt encountered cleanup debt it did not create. Release it
                    # before retrying, but do not consume the failure budget.
                    deactivated = self._runtime.commands.deactivate(
                        cancellation=cancellation
                    )
                    if isinstance(deactivated, AdbServerDeactivateIncomplete):
                        return AdbServerAvailabilityIncomplete(
                            deactivated.snapshot,
                            deactivated.reason,
                        )
                    idle_snapshot = deactivated.snapshot
                    outcome = RecoveryOutcome.DEFERRED
                else:
                    raise TypeError("server mutation facade returned an unsupported result")

            decision = retry.decide_after(outcome)
            if isinstance(decision, RecoveryExhausted):
                if last_failure is None:
                    raise RuntimeError("recovery exhausted without a recorded server failure")
                return AdbServerAvailabilityFailed(
                    idle_snapshot,
                    failed_attempts=decision.failed_attempts,
                    cause=last_failure,
                )

        raise RuntimeError("server availability retry controller terminated unexpectedly")

    def ensure_available(
        self,
        request: AdbServerRequest,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerAvailabilityResult:
        """Alias for ``supervise`` with application-oriented wording."""

        return self.supervise(request, cancellation=cancellation)

    def _normalize_existing_state(
        self,
        request: AdbServerRequest,
        *,
        cancellation: CancellationSignal | None,
    ) -> (
        AdbServerAvailable
        | AdbServerAvailabilityConflict
        | AdbServerAvailabilityIncomplete
        | None
    ):
        with self._runtime.commands._exclusive(
            timeout_seconds=self._runtime.commands.policy.deactivate_timeout_seconds,
            cancellation=cancellation,
        ) as stop_reason:
            if stop_reason is not None:
                return AdbServerAvailabilityIncomplete(
                    self._runtime.snapshot.read(),
                    stop_reason,
                )
            snapshot = self._runtime.snapshot.read()

            if snapshot.phase is AdbServerPhase.ACTIVE:
                if snapshot.request == request:
                    return AdbServerAvailable(snapshot, attempts=0, failed_attempts=0)
                if snapshot.request is None:
                    raise RuntimeError("active server snapshot is missing its request")
                return AdbServerAvailabilityConflict(snapshot.request)

            if snapshot.phase is AdbServerPhase.RELEASE_REQUIRED:
                deactivated = self._runtime.commands.deactivate(
                    cancellation=cancellation
                )
                if isinstance(deactivated, AdbServerDeactivateIncomplete):
                    return AdbServerAvailabilityIncomplete(
                        deactivated.snapshot,
                        deactivated.reason,
                    )

        return None


__all__ = [
    "AdbServerAvailabilityConflict",
    "AdbServerAvailabilityFailed",
    "AdbServerAvailabilityIncomplete",
    "AdbServerAvailabilityPolicy",
    "AdbServerAvailabilityResult",
    "AdbServerAvailabilitySupervisor",
    "AdbServerAvailable",
]
