from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from random import random
from typing import TypeAlias

from adb.server.lifecycle.backend import (
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquireResult,
    AdbServerBackendAcquireSatisfied,
    AdbServerBackendAcquireSucceeded,
)
from adb.server.lifecycle.supervision.intent import AdbServerAcquireOnceIntent
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy


_RandomSource = Callable[[], float]
_UsableAcquireResult: TypeAlias = (
    AdbServerBackendAcquireSucceeded | AdbServerBackendAcquireSatisfied
)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryCompleted:
    """The acquisition recovery task obtained a usable backend attachment."""

    acquire: _UsableAcquireResult

    def __post_init__(self) -> None:
        if not isinstance(
            self.acquire,
            (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
        ):
            raise TypeError("acquire must be a usable ADB server backend acquire result")


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryExhaust:
    """Genuine backend acquisition failures exhausted the configured retry budget."""

    attempts: int
    acquire: AdbServerBackendAcquireFailed

    def __post_init__(self) -> None:
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be greater than zero")
        if not isinstance(self.acquire, AdbServerBackendAcquireFailed):
            raise TypeError("acquire must be AdbServerBackendAcquireFailed")


AdbServerRecoveryDecision: TypeAlias = (
    AdbServerAcquireOnceIntent | AdbServerRecoveryCompleted | AdbServerRecoveryExhaust
)


class AdbServerRecovery:
    """Bounded retry state machine for repeated ADB server backend acquisition.

    Recovery deliberately has no knowledge of authoritative runtime server state, activation,
    reconciliation, scheduling, threads, or the reason recovery was requested. Its owner decides
    when recovery is needed, executes each emitted acquisition intent, and feeds the raw backend
    result back into :meth:`accept`.
    """

    def __init__(
        self,
        policy: AdbServerRecoveryPolicy,
        *,
        _random: _RandomSource = random,
    ) -> None:
        if not isinstance(policy, AdbServerRecoveryPolicy):
            raise TypeError("policy must be AdbServerRecoveryPolicy")
        if not callable(_random):
            raise TypeError("_random must be callable")
        self._policy = policy
        self._random = _random
        self._started = False
        self._finished = False
        self._awaiting_result = False
        self._attempt_number = 0
        self._failed_attempts = 0

    @property
    def started(self) -> bool:
        return self._started

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def failed_attempts(self) -> int:
        return self._failed_attempts

    def start(self) -> AdbServerAcquireOnceIntent:
        """Start this single-use recovery task and request one immediate acquisition."""

        if self._started:
            raise RuntimeError("ADB server recovery is already started")
        self._started = True
        return self._next_intent(0.0)

    def accept(self, result: AdbServerBackendAcquireResult) -> AdbServerRecoveryDecision:
        """Advance from one raw backend acquisition result."""

        if not self._started:
            raise RuntimeError("ADB server recovery must be started first")
        if self._finished:
            raise RuntimeError("ADB server recovery is already finished")
        if not self._awaiting_result:
            raise RuntimeError("ADB server recovery has no acquisition result pending")
        if not isinstance(
            result,
            (
                AdbServerBackendAcquireSucceeded,
                AdbServerBackendAcquireSatisfied,
                AdbServerBackendAcquireInProgress,
                AdbServerBackendAcquireBlocked,
                AdbServerBackendAcquireFailed,
            ),
        ):
            raise TypeError("result must be AdbServerBackendAcquireResult")

        self._awaiting_result = False

        if isinstance(
            result,
            (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
        ):
            self._finished = True
            return AdbServerRecoveryCompleted(result)

        if isinstance(result, (AdbServerBackendAcquireInProgress, AdbServerBackendAcquireBlocked)):
            return self._next_intent(self._policy.deferred_retry_seconds)

        self._failed_attempts += 1
        if (
            self._policy.max_attempts is not None
            and self._failed_attempts >= self._policy.max_attempts
        ):
            self._finished = True
            return AdbServerRecoveryExhaust(self._failed_attempts, result)

        return self._next_intent(self._retry_delay(self._failed_attempts))

    def _next_intent(self, delay_seconds: float) -> AdbServerAcquireOnceIntent:
        self._attempt_number += 1
        self._awaiting_result = True
        return AdbServerAcquireOnceIntent(self._attempt_number, delay_seconds)

    def _retry_delay(self, failed_attempts: int) -> float:
        base = min(
            self._policy.retry_initial_seconds
            * (self._policy.retry_multiplier ** max(0, failed_attempts - 1)),
            self._policy.retry_max_seconds,
        )
        sample = self._random()
        if not 0.0 <= sample <= 1.0:
            raise ValueError("server recovery random source must return a value in [0, 1]")
        jitter = self._policy.retry_jitter_ratio
        factor = 1.0 + ((sample * 2.0) - 1.0) * jitter
        return max(base * factor, 1e-6)


__all__ = [
    "AdbServerRecovery",
    "AdbServerRecoveryCompleted",
    "AdbServerRecoveryDecision",
    "AdbServerRecoveryExhaust",
]
