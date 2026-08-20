from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.endpoint.model import AdbServerEndpoint


class AdbServerEndpointProvisioningError(RuntimeError):
    """Base error for ADB server endpoint provisioning failures."""


class AdbServerEndpointConflictError(AdbServerEndpointProvisioningError):
    """An endpoint is already reserved in this provisioning scope."""


class AdbServerEndpointExhaustedError(AdbServerEndpointProvisioningError):
    """The endpoint allocator could not produce another unreserved endpoint."""


@runtime_checkable
class AdbServerEndpointAllocator(Protocol):
    """Allocate one endpoint not present in the supplied reservation set."""

    def allocate(
        self,
        reserved_endpoints: frozenset[AdbServerEndpoint],
    ) -> AdbServerEndpoint: ...


class SequentialAdbServerEndpointAllocator:
    """Allocate registry-unique endpoints for one host from an increasing port range.

    The allocator does not probe operating-system socket availability. Provisioning
    owns only endpoint reservation; a caller-owned server id, if any, is associated
    with the returned endpoint by external composition.
    """

    def __init__(self, host: str = "localhost", first_port: int = 5037) -> None:
        first = AdbServerEndpoint(host=host, port=first_port)
        self.host = first.host
        self.first_port = first.port

    def allocate(
        self,
        reserved_endpoints: frozenset[AdbServerEndpoint],
    ) -> AdbServerEndpoint:
        if not isinstance(reserved_endpoints, frozenset):
            raise TypeError("reserved_endpoints must be a frozenset")
        for endpoint in reserved_endpoints:
            if not isinstance(endpoint, AdbServerEndpoint):
                raise TypeError("reserved_endpoints must contain AdbServerEndpoint values")

        for port in range(self.first_port, 65536):
            candidate = AdbServerEndpoint(self.host, port)
            if candidate not in reserved_endpoints:
                return candidate
        raise AdbServerEndpointExhaustedError(
            f"no unreserved ADB server endpoint remains for host {self.host!r} "
            f"starting at port {self.first_port}"
        )


@runtime_checkable
class AdbServerEndpointProvisioner(Protocol):
    """Reserve ADB server endpoints without caller identity semantics."""

    def provision(
        self,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint: ...


class InMemoryAdbServerEndpointProvisioner:
    """Reserve distinct ADB server endpoints for one process-local scope.

    Caller-owned logical server identities and their endpoint bindings deliberately
    remain outside the ADB domain. A caller resolves or creates that association, then
    passes the resulting ``AdbServerEndpoint`` into ADB queries, commands, and
    orchestration.
    """

    def __init__(self, allocator: AdbServerEndpointAllocator | None = None) -> None:
        allocator = allocator or SequentialAdbServerEndpointAllocator()
        if not callable(getattr(allocator, "allocate", None)):
            raise TypeError("allocator must provide allocate()")
        self._allocator = allocator
        self._reserved: set[AdbServerEndpoint] = set()
        self._lock = Lock()

    def provision(
        self,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        with self._lock:
            selected = endpoint
            if selected is None:
                selected = self._allocator.allocate(frozenset(self._reserved))
                if not isinstance(selected, AdbServerEndpoint):
                    raise TypeError("allocator.allocate() must return AdbServerEndpoint")

            if selected in self._reserved:
                raise AdbServerEndpointConflictError(
                    f"ADB server endpoint {selected.host}:{selected.port} is already reserved"
                )

            self._reserved.add(selected)
            return selected


__all__ = [
    "AdbServerEndpointAllocator",
    "AdbServerEndpointConflictError",
    "AdbServerEndpointExhaustedError",
    "AdbServerEndpointProvisioner",
    "AdbServerEndpointProvisioningError",
    "InMemoryAdbServerEndpointProvisioner",
    "SequentialAdbServerEndpointAllocator",
]
