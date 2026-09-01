from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, overload, runtime_checkable

from adb.server.lifetime import AdbServerLifetime
from adb.server.lifecycle.transaction import AdbServerProvisionTransactionResult
from adb.server.state import AdbServerStateView


@dataclass(frozen=True, slots=True)
class AdbServerProvisionIntent:
    """Request one complete provision-and-commit transaction from the owning runtime."""


@dataclass(frozen=True, slots=True)
class AdbServerRetireIntent:
    """Request retirement and backend release of one exact current server lifetime."""

    server: AdbServerLifetime

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")


AdbServerLifecycleIntent: TypeAlias = AdbServerProvisionIntent | AdbServerRetireIntent
AdbServerLifecycleIntentResult: TypeAlias = AdbServerProvisionTransactionResult | bool


@runtime_checkable
class AdbServerLifecycleIntentDispatcher(Protocol):
    """Runtime-owned port used by supervision to observe and request server lifecycle work."""

    @property
    def server(self) -> AdbServerLifetime | None:
        """Return the runtime's authoritative current server lifetime."""
        ...

    @property
    def server_state(self) -> AdbServerStateView:
        """Return the runtime's authoritative server-state view."""
        ...

    def provision_server(self) -> AdbServerProvisionTransactionResult:
        """Provision against the server state authoritative at execution time."""
        ...

    def retire_server(self) -> bool:
        """Retire the server state authoritative at execution time."""
        ...

    @overload
    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerProvisionIntent,
    ) -> AdbServerProvisionTransactionResult: ...

    @overload
    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerRetireIntent,
    ) -> bool: ...

    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerLifecycleIntent,
    ) -> AdbServerLifecycleIntentResult: ...


__all__ = [
    "AdbServerLifecycleIntent",
    "AdbServerLifecycleIntentDispatcher",
    "AdbServerLifecycleIntentResult",
    "AdbServerProvisionIntent",
    "AdbServerRetireIntent",
]
