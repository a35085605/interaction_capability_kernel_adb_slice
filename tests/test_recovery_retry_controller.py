from __future__ import annotations

from adb._recovery import (
    RecoveryAttempt,
    RecoveryAttemptOutcome,
    RecoveryDecisionCore,
    RecoveryExhausted,
    RecoveryOutcome,
    RecoveryRetryConfiguration,
    RecoveryRetryController,
    RecoverySucceeded,
)


def _configuration(*, max_attempts: int | None = 3) -> RecoveryRetryConfiguration:
    return RecoveryRetryConfiguration(
        retry_initial_seconds=0.5,
        retry_max_seconds=2.0,
        retry_multiplier=2.0,
        retry_jitter_ratio=0.0,
        deferred_retry_seconds=0.25,
        max_attempts=max_attempts,
    )


def test_retry_controller_tracks_failure_backoff_and_exhaustion() -> None:
    controller = RecoveryRetryController(_configuration(max_attempts=2), _random=lambda: 0.5)

    first = controller.begin()
    assert first == RecoveryAttempt(1, 0.0)

    second = controller.decide_after(RecoveryOutcome.FAILED)
    assert second == RecoveryAttempt(2, 0.5)

    exhausted = controller.decide_after(RecoveryOutcome.FAILED)
    assert exhausted == RecoveryExhausted(2)
    assert controller.failed_attempts == 2


def test_retry_controller_deferred_attempt_does_not_consume_failure_budget() -> None:
    controller = RecoveryRetryController(_configuration(max_attempts=1), _random=lambda: 0.5)
    controller.begin()

    deferred = controller.decide_after(RecoveryOutcome.DEFERRED)

    assert deferred == RecoveryAttempt(2, 0.25)
    assert controller.failed_attempts == 0
    assert isinstance(controller.decide_after(RecoveryOutcome.SUCCEEDED), RecoverySucceeded)


def test_original_recovery_names_remain_compatible_aliases() -> None:
    assert RecoveryDecisionCore is RecoveryRetryController
    assert RecoveryAttemptOutcome is RecoveryOutcome
    assert RecoveryAttemptOutcome.ACQUIRED is RecoveryOutcome.SUCCEEDED
