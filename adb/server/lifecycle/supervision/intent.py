from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, overload, runtime_checkable

from adb.server.identity import AdbServer
from adb.server.lifecycle.control.result import AdbServerProvisionResult
from adb.server.state import AdbServerStateView


@dataclass(frozen=True, slots=True)
class AdbServerProvisionIntent:
    """Request one complete provision-and-commit transaction from the owning runtime."""


@dataclass(frozen=True, slots=True)
class AdbServerRetireIntent:
    """Request retirement and backend release of one exact current server lifetime."""

    server: AdbServer

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")


AdbServerLifecycleIntent: TypeAlias = AdbServerProvisionIntent | AdbServerRetireIntent
AdbServerLifecycleIntentResult: TypeAlias = AdbServerProvisionResult | bool


@runtime_checkable
class AdbServerLifecycleIntentDispatcher(Protocol):
    """Runtime-owned port used by supervision to observe and request server lifecycle work."""

    @property
    def server(self) -> AdbServer | None:
        """Return the runtime's authoritative current server lifetime."""
        ...

    @property
    def server_state(self) -> AdbServerStateView:
        """Return the runtime's read-only authoritative server-state projection."""
        ...

    @overload
    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerProvisionIntent,
    ) -> AdbServerProvisionResult: ...

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
