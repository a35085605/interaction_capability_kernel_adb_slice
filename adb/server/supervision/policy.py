from __future__ import annotations

from dataclasses import dataclass

from adb._recovery import normalize_recovery_retry_configuration


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryPolicy:
    """Retry and backoff policy for one ADB server recovery cycle."""

    retry_initial_seconds: float = 0.5
    retry_max_seconds: float = 30.0
    retry_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.2
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
            subject="ADB server recovery",
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


__all__ = ["AdbServerRecoveryPolicy"]
