from __future__ import annotations

from dataclasses import dataclass

from adb.transport.lifecycle.ensure import AdbTransportEnsurePolicy


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportSupervisionPolicy:
    """Policy for configured-transport projection and optional automatic recovery."""

    recovery_ensure_policy: AdbTransportEnsurePolicy | None = None

    def __post_init__(self) -> None:
        if self.recovery_ensure_policy is not None and not isinstance(
            self.recovery_ensure_policy, AdbTransportEnsurePolicy
        ):
            raise TypeError(
                "recovery_ensure_policy must be AdbTransportEnsurePolicy or None"
            )


__all__ = ["AdbConfiguredTransportSupervisionPolicy"]
