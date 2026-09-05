from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifecycle.backend import (
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAlreadyAcquired,
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
    """Terminal instruction to finish the current recovery cycle."""


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
        # Authority changed after this provision began. This cycle is terminal; any successor
        # recovery must be driven by a distinct reconciliation demand.
        return AdbServerRecoveryCompleted()

    if isinstance(
        outcome,
        (
            AdbServerBackendAlreadyAcquired,
            AdbServerBackendAcquireDeferred,
            AdbServerBackendAcquireFailed,
        ),
    ):
        decision = recovery.decide_after(outcome)
        if isinstance(decision, (AdbServerRecoveryAttempt, AdbServerRecoveryFailed)):
            return decision
        if isinstance(decision, AdbServerRecoveryAcquired):
            raise TypeError("recovery acquired a non-committable backend provision outcome")
        raise TypeError("recovery returned an unsupported decision")

    raise TypeError("outcome must be AdbServerProvisionOutcome")


__all__ = [
    "AdbServerRecoveryCompleted",
    "AdbServerRecoveryInstruction",
    "decide_recovery_after_provision",
]
