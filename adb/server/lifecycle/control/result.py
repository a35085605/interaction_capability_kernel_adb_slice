from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from networking import TcpAddress


def _normalize_diagnostic(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerProvisioned:
    """A usable ADB server backend endpoint was provisioned."""

    endpoint: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerProvisionDeferred:
    """Provisioning result indicating lifecycle work is still converging."""

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
    """Provisioning result indicating that an attempt produced no usable ADB server endpoint."""

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
