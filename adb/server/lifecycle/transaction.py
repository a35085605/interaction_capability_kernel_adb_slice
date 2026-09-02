from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)
from adb.server.state import AdbServerActivated


@dataclass(frozen=True, slots=True)
class AdbServerProvisionCommitted:
    """A provisioned endpoint committed as a fresh authoritative server identity.

    ``activation`` retains the canonical state-transition evidence when this result is produced by
    the runtime lifecycle facade. It remains optional so callers that only persist the committed
    identity do not need to manufacture state evidence.
    """

    server: AdbServerIdentity
    activation: AdbServerActivated | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if self.activation is not None:
            if not isinstance(self.activation, AdbServerActivated):
                raise TypeError("activation must be AdbServerActivated or None")
            if self.activation.server != self.server:
                raise ValueError("activation must describe the committed server identity")


AdbServerProvisionTransactionResult: TypeAlias = (
    AdbServerProvisionCommitted
    | AdbServerProvisionDeferred
    | AdbServerProvisionFailed
)


__all__ = [
    "AdbServerProvisionCommitted",
    "AdbServerProvisionTransactionResult",
]
