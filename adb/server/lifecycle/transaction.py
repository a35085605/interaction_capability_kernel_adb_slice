from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifetime import AdbServerLifetime
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)


@dataclass(frozen=True, slots=True)
class AdbServerProvisionCommitted:
    """A provisioned endpoint committed as a fresh authoritative runtime lifetime."""

    server: AdbServerLifetime

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")


AdbServerProvisionTransactionResult: TypeAlias = (
    AdbServerProvisionCommitted
    | AdbServerProvisionDeferred
    | AdbServerProvisionFailed
)


__all__ = [
    "AdbServerProvisionCommitted",
    "AdbServerProvisionTransactionResult",
]
