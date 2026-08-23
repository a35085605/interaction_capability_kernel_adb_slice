from __future__ import annotations

from dataclasses import dataclass, field
import math
from numbers import Real
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


def _normalize_retry_configuration(
    *,
    retry_initial_seconds: object,
    retry_max_seconds: object,
    retry_multiplier: object,
    retry_jitter_ratio: object,
    max_attempts: object,
) -> tuple[float, float, float, float, int | None]:
    initial = _normalize_positive_seconds(
        retry_initial_seconds,
        field_name="ADB server supervision initial retry",
    )
    maximum = _normalize_positive_seconds(
        retry_max_seconds,
        field_name="ADB server supervision maximum retry",
    )
    multiplier = _normalize_positive_seconds(
        retry_multiplier,
        field_name="ADB server supervision retry multiplier",
    )
    if multiplier < 1.0:
        raise ValueError("ADB server supervision retry multiplier must be at least one")
    if maximum < initial:
        raise ValueError("ADB server supervision maximum retry must be >= initial retry")
    if isinstance(retry_jitter_ratio, bool) or not isinstance(retry_jitter_ratio, Real):
        raise TypeError("ADB server supervision retry jitter ratio must be a real number")
    jitter = float(retry_jitter_ratio)
    if not math.isfinite(jitter) or not 0.0 <= jitter < 1.0:
        raise ValueError("ADB server supervision retry jitter ratio must be in [0, 1)")
    if max_attempts is not None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TypeError("ADB server supervision max_attempts must be an integer or None")
        if max_attempts <= 0:
            raise ValueError("ADB server supervision max_attempts must be greater than zero")
    return initial, maximum, multiplier, jitter, max_attempts


@dataclass(frozen=True, slots=True)
class AdbServerPerGenerationEndpoint:
    """Let every recovered server resolve its own endpoint independently."""


@dataclass(frozen=True, slots=True)
class AdbServerPinFirstResolvedEndpoint:
    """Pin the first server's resolved endpoint across later servers."""


@dataclass(frozen=True, slots=True)
class AdbServerFixedEndpoint:
    """Require every supervised server to use one explicitly configured endpoint."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


AdbServerEndpointPolicy: TypeAlias = (
    AdbServerPerGenerationEndpoint
    | AdbServerPinFirstResolvedEndpoint
    | AdbServerFixedEndpoint
)


def _require_endpoint_policy(value: object) -> AdbServerEndpointPolicy:
    if not isinstance(
        value,
        (
            AdbServerPerGenerationEndpoint,
            AdbServerPinFirstResolvedEndpoint,
            AdbServerFixedEndpoint,
        ),
    ):
        raise TypeError("endpoint_policy must be an ADB server endpoint policy")
    return value


@dataclass(frozen=True, slots=True)
class AdbServerSupervisionPolicy:
    """Policy for ADB server recovery."""

    retry_initial_seconds: float = 0.5
    retry_max_seconds: float = 30.0
    retry_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.2
    max_attempts: int | None = None
    endpoint_policy: AdbServerEndpointPolicy = field(
        default_factory=AdbServerPinFirstResolvedEndpoint
    )

    def __post_init__(self) -> None:
        endpoint_policy = _require_endpoint_policy(self.endpoint_policy)
        initial, maximum, multiplier, jitter, max_attempts = _normalize_retry_configuration(
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
            retry_multiplier=self.retry_multiplier,
            retry_jitter_ratio=self.retry_jitter_ratio,
            max_attempts=self.max_attempts,
        )
        object.__setattr__(self, "endpoint_policy", endpoint_policy)
        object.__setattr__(self, "retry_initial_seconds", initial)
        object.__setattr__(self, "retry_max_seconds", maximum)
        object.__setattr__(self, "retry_multiplier", multiplier)
        object.__setattr__(self, "retry_jitter_ratio", jitter)
        object.__setattr__(self, "max_attempts", max_attempts)


__all__ = [
    "AdbServerEndpointPolicy",
    "AdbServerFixedEndpoint",
    "AdbServerPerGenerationEndpoint",
    "AdbServerPinFirstResolvedEndpoint",
    "AdbServerSupervisionPolicy",
]
