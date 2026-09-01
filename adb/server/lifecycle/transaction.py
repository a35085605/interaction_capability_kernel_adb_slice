from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)


@dataclass(frozen=True, slots=True)
class AdbServerProvisionCommitted:
    """A provisioned endpoint committed as a fresh authoritative server identity."""

    server: AdbServerIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")


AdbServerProvisionTransactionResult: TypeAlias = (
    AdbServerProvisionCommitted
    | AdbServerProvisionDeferred
    | AdbServerProvisionFailed
)


__all__ = [
    "AdbServerProvisionCommitted",
    "AdbServerProvisionTransactionResult",
]
