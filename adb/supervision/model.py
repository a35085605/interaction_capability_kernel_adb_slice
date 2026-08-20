from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from uuid import uuid4

from adb.server.lifecycle import AdbServerEnsurePolicy
from adb.transport.lifecycle.ensure import AdbTransportEnsurePolicy


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def _normalize_retry_configuration(
    *,
    retry_initial_seconds: object,
    retry_max_seconds: object,
    retry_multiplier: object,
    retry_jitter_ratio: object,
    max_attempts: object,
    prefix: str,
) -> tuple[float, float, float, float, int | None]:
    initial = _normalize_positive_seconds(
        retry_initial_seconds,
        field_name=f"{prefix} initial retry",
    )
    maximum = _normalize_positive_seconds(
        retry_max_seconds,
        field_name=f"{prefix} maximum retry",
    )
    multiplier = _normalize_positive_seconds(
        retry_multiplier,
        field_name=f"{prefix} retry multiplier",
    )
    if multiplier < 1.0:
        raise ValueError(f"{prefix} retry multiplier must be at least one")
    if maximum < initial:
        raise ValueError(f"{prefix} maximum retry must be >= initial retry")
    if isinstance(retry_jitter_ratio, bool) or not isinstance(retry_jitter_ratio, Real):
        raise TypeError(f"{prefix} retry jitter ratio must be a real number")
    jitter = float(retry_jitter_ratio)
    if not math.isfinite(jitter) or not 0.0 <= jitter < 1.0:
        raise ValueError(f"{prefix} retry jitter ratio must be in [0, 1)")
    if max_attempts is not None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TypeError(f"{prefix} max_attempts must be an integer or None")
        if max_attempts <= 0:
            raise ValueError(f"{prefix} max_attempts must be greater than zero")
    return initial, maximum, multiplier, jitter, max_attempts


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportSupervisionPolicy:
    """Projection with optional recovery after a same-generation observed disappearance."""

    recovery_ensure_policy: AdbTransportEnsurePolicy | None = None

    def __post_init__(self) -> None:
        if self.recovery_ensure_policy is not None and not isinstance(
            self.recovery_ensure_policy, AdbTransportEnsurePolicy
        ):
            raise TypeError(
                "recovery_ensure_policy must be AdbTransportEnsurePolicy or None"
            )


@dataclass(frozen=True, slots=True, order=True)
class AdbServerRecoveryCycleId:
    """Opaque identity for one server-running recovery cycle."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_required_text(
                self.value,
                field_name="ADB server recovery cycle id",
            ),
        )

    @classmethod
    def new(cls) -> "AdbServerRecoveryCycleId":
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class AdbServerSupervisionPolicy:
    """Retry policy for maintaining one ADB server's desired running condition."""

    ensure_policy: AdbServerEnsurePolicy
    retry_initial_seconds: float = 0.5
    retry_max_seconds: float = 30.0
    retry_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.2
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ensure_policy, AdbServerEnsurePolicy):
            raise TypeError("ensure_policy must be AdbServerEnsurePolicy")
        initial, maximum, multiplier, jitter, max_attempts = _normalize_retry_configuration(
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
            retry_multiplier=self.retry_multiplier,
            retry_jitter_ratio=self.retry_jitter_ratio,
            max_attempts=self.max_attempts,
            prefix="ADB server supervision",
        )
        object.__setattr__(self, "retry_initial_seconds", initial)
        object.__setattr__(self, "retry_max_seconds", maximum)
        object.__setattr__(self, "retry_multiplier", multiplier)
        object.__setattr__(self, "retry_jitter_ratio", jitter)
        object.__setattr__(self, "max_attempts", max_attempts)


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingSupervisionPolicy:
    """Bound one transport-inventory tracking-start episode.

    Tracking supervision deliberately owns no retry/backoff policy. A server-connection
    failure requests upstream server reconciliation; later tracking reconciliation is driven
    by fresh external evidence or an explicit caller request.
    """

    episode_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "episode_timeout_seconds",
            _normalize_positive_seconds(
                self.episode_timeout_seconds,
                field_name="ADB tracking-start episode timeout",
            ),
        )


__all__ = [
    "AdbConfiguredTransportSupervisionPolicy",
    "AdbDevicesTrackingSupervisionPolicy",
    "AdbServerRecoveryCycleId",
    "AdbServerSupervisionPolicy",
]
