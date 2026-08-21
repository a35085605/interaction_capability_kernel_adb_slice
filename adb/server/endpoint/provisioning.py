from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.endpoint.model import AdbServerEndpoint


class AdbServerEndpointProvisioningError(RuntimeError):
    """Base error for ADB server endpoint allocation failures."""


class AdbServerEndpointExhaustedError(AdbServerEndpointProvisioningError):
    """The endpoint allocator could not produce another candidate endpoint."""


@runtime_checkable
class AdbServerEndpointAllocator(Protocol):
    """Allocate one endpoint not present in the supplied acquisition-local exclusion set."""

    def allocate(
        self,
        excluded_endpoints: frozenset[AdbServerEndpoint],
    ) -> AdbServerEndpoint: ...


class SequentialAdbServerEndpointAllocator:
    """Allocate candidate endpoints for one host from an increasing port range.

    Allocation is deliberately not a reservation and does not claim operating-system
    socket ownership. The process-owned server slot serializes creation attempts; the
    native ADB launcher remains the authority that decides whether a candidate can bind.
    """

    def __init__(self, host: str = "localhost", first_port: int = 5037) -> None:
        first = AdbServerEndpoint(host=host, port=first_port)
        self.host = first.host
        self.first_port = first.port

    def allocate(
        self,
        excluded_endpoints: frozenset[AdbServerEndpoint],
    ) -> AdbServerEndpoint:
        if not isinstance(excluded_endpoints, frozenset):
            raise TypeError("excluded_endpoints must be a frozenset")
        for endpoint in excluded_endpoints:
            if not isinstance(endpoint, AdbServerEndpoint):
                raise TypeError("excluded_endpoints must contain AdbServerEndpoint values")

        for port in range(self.first_port, 65536):
            candidate = AdbServerEndpoint(self.host, port)
            if candidate not in excluded_endpoints:
                return candidate
        raise AdbServerEndpointExhaustedError(
            f"no ADB server endpoint candidate remains for host {self.host!r} "
            f"starting at port {self.first_port}"
        )


__all__ = [
    "AdbServerEndpointAllocator",
    "AdbServerEndpointExhaustedError",
    "AdbServerEndpointProvisioningError",
    "SequentialAdbServerEndpointAllocator",
]
