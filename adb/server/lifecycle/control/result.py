from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifetime import AdbServerLifetime


def _normalize_diagnostic(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerProvisioned:
    """A fresh usable ADB server domain lifetime was provisioned."""

    server: AdbServerLifetime

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")


@dataclass(frozen=True, slots=True)
class AdbServerProvisionDeferred:
    """Provisioning cannot proceed yet because lifecycle work is still converging."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_diagnostic(
                self.diagnostic,
                field_name="ADB server provision deferral diagnostic",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbServerProvisionFailed:
    """A provisioning attempt ran but did not produce a usable ADB server lifetime."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_diagnostic(
                self.diagnostic,
                field_name="ADB server provision failure diagnostic",
            ),
        )


AdbServerProvisionResult: TypeAlias = (
    AdbServerProvisioned
    | AdbServerProvisionDeferred
    | AdbServerProvisionFailed
)


__all__ = [
    "AdbServerProvisionDeferred",
    "AdbServerProvisionFailed",
    "AdbServerProvisionResult",
    "AdbServerProvisioned",
]
