from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from random import random
from time import sleep
from typing import TypeAlias

from lifecycle.capability.result import LifecycleDiagnostics, LifecycleOutcome
from lifecycle.capability.supervision.control import CancellationSignal, SupervisionStopReason
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
from adb.runtime.server.commands import AdbServerActivateIncomplete, AdbServerDeactivateIncomplete
from adb.runtime.server.runtime import AdbServerRuntime
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerPhase, AdbServerSnapshot


_Sleeper = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityPolicy:
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
        for field in (
            "retry_initial_seconds",
            "retry_max_seconds",
            "retry_multiplier",
            "retry_jitter_ratio",
            "deferred_retry_seconds",
            "max_attempts",
        ):
            object.__setattr__(self, field, getattr(configuration, field))

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
    snapshot: AdbServerSnapshot
    attempts: int
    failed_attempts: int
    diagnostics: tuple[LifecycleDiagnostics, ...] = ()

    def __post_init__(self) -> None:
        if self.snapshot.phase is not AdbServerPhase.ACTIVE:
            raise ValueError("available server snapshot must be active")
        if self.snapshot.capability is None:
            raise ValueError("available server snapshot must expose a capability")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if isinstance(self.failed_attempts, bool) or not isinstance(self.failed_attempts, int):
            raise TypeError("failed_attempts must be an integer")
        if not 0 <= self.failed_attempts <= self.attempts:
            raise ValueError("failed_attempts must be between zero and attempts")


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityConflict:
    current_request: AdbServerRequest

    def __post_init__(self) -> None:
        if not isinstance(self.current_request, AdbServerRequest):
            raise TypeError("current_request must be AdbServerRequest")


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityIncomplete:
    snapshot: AdbServerSnapshot
    reason: SupervisionStopReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, SupervisionStopReason):
            raise TypeError("reason must be SupervisionStopReason")


@dataclass(frozen=True, slots=True)
class AdbServerAvailabilityFailed:
    snapshot: AdbServerSnapshot
    failed_attempts: int
    cause: BaseException
    diagnostics: tuple[LifecycleDiagnostics, ...] = ()

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
    """Obtain one usable request across lifecycle generations.

    Failed acquisition is counted from the explicit operation outcome, never inferred
    from the resulting snapshot. Pending cleanup/finalization is recovered before a new
    attempt and recovery itself does not consume the acquisition-failure budget.
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
        if not callable(_sleeper) or not callable(_random):
            raise TypeError("_sleeper and _random must be callable")
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
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")

        normalized, carried_diagnostics = self._normalize_existing_state(
            request, cancellation=cancellation
        )
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
        collected_diagnostics: list[LifecycleDiagnostics] = list(carried_diagnostics)
        idle_snapshot = self._runtime.snapshot.read()

        while isinstance(decision, RecoveryAttempt):
            if decision.delay_seconds > 0.0:
                self._sleep(decision.delay_seconds)

            with self._runtime.commands._exclusive(
                timeout_seconds=self._runtime.commands.policy.activate_timeout_seconds,
                cancellation=cancellation,
            ) as stop:
                if stop is not None:
                    return AdbServerAvailabilityIncomplete(self._runtime.snapshot.read(), stop)

                result = self._runtime.commands.activate(request, cancellation=cancellation)
                if isinstance(result, AdbServerActivateIncomplete):
                    return AdbServerAvailabilityIncomplete(result.snapshot, result.reason)

                snapshot = result.snapshot
                if snapshot.phase is AdbServerPhase.ACTIVE:
                    if snapshot.request != request:
                        if snapshot.request is None:
                            raise RuntimeError("active server snapshot is missing its request")
                        return AdbServerAvailabilityConflict(snapshot.request)
                    terminal = retry.decide_after(RecoveryOutcome.SUCCEEDED)
                    if not isinstance(terminal, RecoverySucceeded):
                        raise RuntimeError("successful recovery produced a non-terminal decision")
                    return AdbServerAvailable(
                        snapshot,
                        attempts=retry.attempt_number,
                        failed_attempts=retry.failed_attempts,
                        diagnostics=tuple(collected_diagnostics),
                    )

                if result.outcome is LifecycleOutcome.ACQUIRE_FAILED:
                    last_failure = result.diagnostics.acquire_error
                    if last_failure is None:
                        raise RuntimeError("failed activation is missing acquire_error")
                    collected_diagnostics.append(result.diagnostics)
                    outcome = RecoveryOutcome.FAILED
                elif snapshot.phase in (
                    AdbServerPhase.CLEANUP_PENDING,
                    AdbServerPhase.FINALIZATION_PENDING,
                ):
                    # Pending debt predating this activation attempt is normalized
                    # without consuming a new acquisition failure.
                    outcome = RecoveryOutcome.DEFERRED
                else:
                    raise TypeError("server activation returned a non-terminal result")

                if snapshot.phase is AdbServerPhase.IDLE:
                    idle_snapshot = snapshot
                else:
                    deactivated = self._runtime.commands.deactivate(cancellation=cancellation)
                    if isinstance(deactivated, AdbServerDeactivateIncomplete):
                        return AdbServerAvailabilityIncomplete(
                            deactivated.snapshot, deactivated.reason
                        )
                    idle_snapshot = deactivated.snapshot
                    if idle_snapshot.phase is not AdbServerPhase.IDLE:
                        raise RuntimeError("completed recovery must finish idle")
                    if result.outcome is LifecycleOutcome.ACQUIRE_FAILED:
                        # Recovery carries the original acquire failure plus every
                        # cleanup/finalization diagnostic accumulated afterwards.
                        collected_diagnostics[-1] = deactivated.diagnostics
                    else:
                        collected_diagnostics.append(deactivated.diagnostics)

            decision = retry.decide_after(outcome)
            if isinstance(decision, RecoveryExhausted):
                if last_failure is None:
                    raise RuntimeError("recovery exhausted without a recorded failure")
                return AdbServerAvailabilityFailed(
                    idle_snapshot,
                    failed_attempts=decision.failed_attempts,
                    cause=last_failure,
                    diagnostics=tuple(collected_diagnostics),
                )

        raise RuntimeError("server availability retry controller terminated unexpectedly")

    def ensure_available(
        self,
        request: AdbServerRequest,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> AdbServerAvailabilityResult:
        return self.supervise(request, cancellation=cancellation)

    def _normalize_existing_state(
        self,
        request: AdbServerRequest,
        *,
        cancellation: CancellationSignal | None,
    ) -> tuple[
        AdbServerAvailable
        | AdbServerAvailabilityConflict
        | AdbServerAvailabilityIncomplete
        | None,
        tuple[LifecycleDiagnostics, ...],
    ]:
        with self._runtime.commands._exclusive(
            timeout_seconds=self._runtime.commands.policy.deactivate_timeout_seconds,
            cancellation=cancellation,
        ) as stop:
            if stop is not None:
                return (
                    AdbServerAvailabilityIncomplete(self._runtime.snapshot.read(), stop),
                    (),
                )
            snapshot = self._runtime.snapshot.read()

            if snapshot.phase is AdbServerPhase.ACTIVE:
                if snapshot.request == request:
                    return (AdbServerAvailable(snapshot, attempts=0, failed_attempts=0), ())
                if snapshot.request is None:
                    raise RuntimeError("active server snapshot is missing its request")
                return (AdbServerAvailabilityConflict(snapshot.request), ())

            if snapshot.phase in (
                AdbServerPhase.CLEANUP_PENDING,
                AdbServerPhase.FINALIZATION_PENDING,
            ):
                deactivated = self._runtime.commands.deactivate(cancellation=cancellation)
                if isinstance(deactivated, AdbServerDeactivateIncomplete):
                    return (
                        AdbServerAvailabilityIncomplete(
                            deactivated.snapshot, deactivated.reason
                        ),
                        (),
                    )
                return (None, (deactivated.diagnostics,))

        return (None, ())



__all__ = [
    "AdbServerAvailabilityConflict",
    "AdbServerAvailabilityFailed",
    "AdbServerAvailabilityIncomplete",
    "AdbServerAvailabilityPolicy",
    "AdbServerAvailabilityResult",
    "AdbServerAvailabilitySupervisor",
    "AdbServerAvailable",
]
