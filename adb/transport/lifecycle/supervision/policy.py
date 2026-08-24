from __future__ import annotations

from dataclasses import dataclass

from adb.transport.lifecycle.ensure import AdbTcpTransportEnsurePolicy


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportSupervisionPolicy:
    """Policy for transport projection and optional TCP disappearance recovery."""

    tcp_recovery_ensure_policy: AdbTcpTransportEnsurePolicy | None = None

    def __post_init__(self) -> None:
        if self.tcp_recovery_ensure_policy is not None and not isinstance(
            self.tcp_recovery_ensure_policy, AdbTcpTransportEnsurePolicy
        ):
            raise TypeError(
                "tcp_recovery_ensure_policy must be AdbTcpTransportEnsurePolicy or None"
            )


__all__ = ["AdbConfiguredTransportSupervisionPolicy"]
