from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifecycle.backend import (
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
)
from adb.server.lifecycle.coordinator import AdbServerAlreadyActive
from adb.server.lifecycle.provision import (
    AdbServerProvisionActivated,
    AdbServerProvisionActivationConflict,
    AdbServerProvisionOutcome,
)
from adb.server.lifecycle.supervision.recovery import (
    AdbServerRecovery,
    AdbServerRecoveryAcquired,
    AdbServerRecoveryAttempt,
    AdbServerRecoveryFailed,
)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryCompleted:
    """Instruction to finish the current recovery cycle after a satisfied provision outcome."""


AdbServerRecoveryInstruction: TypeAlias = (
    AdbServerRecoveryCompleted | AdbServerRecoveryAttempt | AdbServerRecoveryFailed
)


def decide_recovery_after_provision(
    recovery: AdbServerRecovery,
    outcome: AdbServerProvisionOutcome,
) -> AdbServerRecoveryInstruction:
    """Combine validated lifecycle outcome with the stateful recovery retry policy."""

    if not isinstance(recovery, AdbServerRecovery):
        raise TypeError("recovery must be AdbServerRecovery")

    if isinstance(outcome, (AdbServerAlreadyActive, AdbServerProvisionActivated)):
        return AdbServerRecoveryCompleted()

    if isinstance(outcome, AdbServerProvisionActivationConflict):
        # The conflict result is authoritative evidence at the activation linearization point.
        # An active conflict satisfies this task. An inactive conflict leaves the same task
        # unsatisfied, so retry it without consuming a backend-failure attempt; do not manufacture
        # a successor recovery task from a later state observation.
        if outcome.activation.state.active:
            return AdbServerRecoveryCompleted()
        return recovery.retry_after_unsatisfied()

    if isinstance(
        outcome,
        (
            AdbServerBackendAcquireInProgress,
            AdbServerBackendAcquireBlocked,
            AdbServerBackendAcquireFailed,
        ),
    ):
        decision = recovery.decide_after(outcome)
        if isinstance(decision, (AdbServerRecoveryAttempt, AdbServerRecoveryFailed)):
            return decision
        if isinstance(decision, AdbServerRecoveryAcquired):
            raise TypeError("recovery acquired a non-usable backend provision outcome")
        raise TypeError("recovery returned an unsupported decision")

    raise TypeError("outcome must be AdbServerProvisionOutcome")


__all__ = [
    "AdbServerRecoveryCompleted",
    "AdbServerRecoveryInstruction",
    "decide_recovery_after_provision",
]
