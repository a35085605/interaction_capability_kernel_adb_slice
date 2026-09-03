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

    restart_if_inactive: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.restart_if_inactive, bool):
            raise TypeError("restart_if_inactive must be bool")


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
        # An active conflict satisfied this cycle at its activation linearization point. A later
        # inactive state must not let this stale cycle resurrect a server unless newer work is
        # pending. An inactive conflict remains unsatisfied and may start a successor cycle without
        # consuming a backend failure attempt.
        return AdbServerRecoveryCompleted(
            restart_if_inactive=not outcome.activation.state.active,
        )

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
