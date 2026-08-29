from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, overload, runtime_checkable

from adb.server.identity import AdbServer
from adb.server.lifecycle.control.result import AdbServerProvisionResult
from adb.server.state import AdbServerStateView


@dataclass(frozen=True, slots=True)
class AdbServerProvisionIntent:
    """Request one server provisioning attempt from the owning runtime."""


@dataclass(frozen=True, slots=True)
class AdbServerActivateIntent:
    """Request authoritative activation of one provisioned server lifetime."""

    server: AdbServer

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")


@dataclass(frozen=True, slots=True)
class AdbServerRetireIntent:
    """Request authoritative retirement of one exact current server lifetime."""

    server: AdbServer

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")


@dataclass(frozen=True, slots=True)
class AdbServerDisposeIntent:
    """Request backend attachment release for one retired or abandoned lifetime."""

    server: AdbServer

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")


AdbServerLifecycleIntent: TypeAlias = (
    AdbServerProvisionIntent
    | AdbServerActivateIntent
    | AdbServerRetireIntent
    | AdbServerDisposeIntent
)
AdbServerLifecycleIntentResult: TypeAlias = AdbServerProvisionResult | bool | None


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
        intent: AdbServerActivateIntent,
    ) -> bool: ...

    @overload
    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerRetireIntent,
    ) -> bool: ...

    @overload
    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerDisposeIntent,
    ) -> None: ...

    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerLifecycleIntent,
    ) -> AdbServerLifecycleIntentResult: ...


__all__ = [
    "AdbServerActivateIntent",
    "AdbServerDisposeIntent",
    "AdbServerLifecycleIntent",
    "AdbServerLifecycleIntentDispatcher",
    "AdbServerLifecycleIntentResult",
    "AdbServerProvisionIntent",
    "AdbServerRetireIntent",
]
