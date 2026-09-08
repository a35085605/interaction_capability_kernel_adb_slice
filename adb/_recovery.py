from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
import math
from numbers import Real
from random import random
from typing import TypeAlias


RandomSource = Callable[[], float]


@dataclass(frozen=True, slots=True)
class RecoveryRetryConfiguration:
    """Domain-neutral retry configuration for one recovery cycle."""

    retry_initial_seconds: float
    retry_max_seconds: float
    retry_multiplier: float
    retry_jitter_ratio: float
    deferred_retry_seconds: float
    max_attempts: int | None


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


def normalize_recovery_retry_configuration(
    *,
    retry_initial_seconds: object,
    retry_max_seconds: object,
    retry_multiplier: object,
    retry_jitter_ratio: object,
    deferred_retry_seconds: object,
    max_attempts: object,
    subject: str,
) -> RecoveryRetryConfiguration:
    """Normalize shared recovery-policy fields while retaining domain diagnostics."""

    if not isinstance(subject, str):
        raise TypeError("subject must be a string")
    normalized_subject = subject.strip()
    if not normalized_subject:
        raise ValueError("subject cannot be empty")

    initial = _normalize_positive_seconds(
        retry_initial_seconds,
        field_name=f"{normalized_subject} initial retry",
    )
    maximum = _normalize_positive_seconds(
        retry_max_seconds,
        field_name=f"{normalized_subject} maximum retry",
    )
    multiplier = _normalize_positive_seconds(
        retry_multiplier,
        field_name=f"{normalized_subject} retry multiplier",
    )
    if multiplier < 1.0:
        raise ValueError(f"{normalized_subject} retry multiplier must be at least one")
    if maximum < initial:
        raise ValueError(f"{normalized_subject} maximum retry must be >= initial retry")
    if isinstance(retry_jitter_ratio, bool) or not isinstance(retry_jitter_ratio, Real):
        raise TypeError(f"{normalized_subject} retry jitter ratio must be a real number")
    jitter = float(retry_jitter_ratio)
    if not math.isfinite(jitter) or not 0.0 <= jitter < 1.0:
        raise ValueError(f"{normalized_subject} retry jitter ratio must be in [0, 1)")
    deferred = _normalize_positive_seconds(
        deferred_retry_seconds,
        field_name=f"{normalized_subject} deferred retry",
    )
    if max_attempts is not None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TypeError(f"{normalized_subject} max_attempts must be an integer or None")
        if max_attempts <= 0:
            raise ValueError(f"{normalized_subject} max_attempts must be greater than zero")

    return RecoveryRetryConfiguration(
        retry_initial_seconds=initial,
        retry_max_seconds=maximum,
        retry_multiplier=multiplier,
        retry_jitter_ratio=jitter,
        deferred_retry_seconds=deferred,
        max_attempts=max_attempts,
    )


class RecoveryAttemptOutcome(Enum):
    """Domain-neutral meaning of one completed acquisition attempt."""

    ACQUIRED = auto()
    DEFERRED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class RecoveryAttempt:
    """One acquisition attempt selected by the shared recovery state machine."""

    attempt_number: int
    delay_seconds: float


@dataclass(frozen=True, slots=True)
class RecoveryAcquired:
    """Terminal decision that recovery has a usable acquisition."""


@dataclass(frozen=True, slots=True)
class RecoveryExhausted:
    """Terminal decision after budget-consuming failures exhausted the policy."""

    failed_attempts: int


RecoveryDecision: TypeAlias = RecoveryAttempt | RecoveryAcquired | RecoveryExhausted


class RecoveryDecisionCore:
    """Shared recovery retry, backoff, jitter, and exhaustion state machine."""

    def __init__(
        self,
        configuration: RecoveryRetryConfiguration,
        *,
        _random: RandomSource = random,
        random_source_error: str = "recovery random source must return a value in [0, 1]",
    ) -> None:
        if not isinstance(configuration, RecoveryRetryConfiguration):
            raise TypeError("configuration must be RecoveryRetryConfiguration")
        if not callable(_random):
            raise TypeError("_random must be callable")
        if not isinstance(random_source_error, str):
            raise TypeError("random_source_error must be a string")
        normalized_error = random_source_error.strip()
        if not normalized_error:
            raise ValueError("random_source_error cannot be empty")

        self._configuration = configuration
        self._random = _random
        self._random_source_error = normalized_error
        self._attempt_number = 0
        self._failed_attempts = 0

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def failed_attempts(self) -> int:
        return self._failed_attempts

    def begin(self) -> RecoveryAttempt:
        if self._attempt_number != 0:
            raise RuntimeError("recovery has already begun")
        return self._next_attempt(0.0)

    def decide_after(self, outcome: RecoveryAttemptOutcome) -> RecoveryDecision:
        if self._attempt_number == 0:
            raise RuntimeError("recovery has not begun")
        if not isinstance(outcome, RecoveryAttemptOutcome):
            raise TypeError("outcome must be RecoveryAttemptOutcome")

        if outcome is RecoveryAttemptOutcome.ACQUIRED:
            return RecoveryAcquired()
        if outcome is RecoveryAttemptOutcome.DEFERRED:
            return self._next_attempt(self._configuration.deferred_retry_seconds)

        self._failed_attempts += 1
        if (
            self._configuration.max_attempts is not None
            and self._failed_attempts >= self._configuration.max_attempts
        ):
            return RecoveryExhausted(self._failed_attempts)

        return self._next_attempt(self._retry_delay(self._failed_attempts))

    def _next_attempt(self, delay_seconds: float) -> RecoveryAttempt:
        self._attempt_number += 1
        return RecoveryAttempt(self._attempt_number, delay_seconds)

    def _retry_delay(self, failed_attempts: int) -> float:
        base = min(
            self._configuration.retry_initial_seconds
            * (self._configuration.retry_multiplier ** max(0, failed_attempts - 1)),
            self._configuration.retry_max_seconds,
        )
        sample = self._random()
        if not 0.0 <= sample <= 1.0:
            raise ValueError(self._random_source_error)
        jitter = self._configuration.retry_jitter_ratio
        factor = 1.0 + ((sample * 2.0) - 1.0) * jitter
        return max(base * factor, 1e-6)


__all__ = [
    "RandomSource",
    "RecoveryAcquired",
    "RecoveryAttempt",
    "RecoveryAttemptOutcome",
    "RecoveryDecision",
    "RecoveryDecisionCore",
    "RecoveryExhausted",
    "RecoveryRetryConfiguration",
    "normalize_recovery_retry_configuration",
]
