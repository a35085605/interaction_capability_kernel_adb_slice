from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real

from adb.transport.lifecycle.ensure import AdbTransportEnsurePolicy


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportSupervisionPolicy:
    """Policy for configured-transport projection and optional disappearance recovery."""

    recovery_ensure_policy: AdbTransportEnsurePolicy | None = None

    def __post_init__(self) -> None:
        if self.recovery_ensure_policy is not None and not isinstance(
            self.recovery_ensure_policy, AdbTransportEnsurePolicy
        ):
            raise TypeError(
                "recovery_ensure_policy must be AdbTransportEnsurePolicy or None"
            )


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingSupervisionPolicy:
    """Policy for establishing transport-inventory tracker scopes.

    ``episode_timeout_seconds`` now bounds the source connection and ADB service handshake
    directly. Tracking supervision does not retry failed starts; server connection failures
    request server reconciliation.
    """

    episode_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "episode_timeout_seconds",
            _normalize_positive_seconds(
                self.episode_timeout_seconds,
                field_name="ADB tracking startup timeout",
            ),
        )


__all__ = [
    "AdbConfiguredTransportSupervisionPolicy",
    "AdbDevicesTrackingSupervisionPolicy",
]
