from __future__ import annotations

from enum import Enum
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


@runtime_checkable
class AdbServerEndpointReservationProvider(Protocol):
    """Create process-local tentative reservations for endpoint acquisition."""

    def reserve(
        self,
        *,
        endpoint: AdbServerEndpoint | None = None,
        excluded_endpoints: frozenset[AdbServerEndpoint] = frozenset(),
    ) -> "AdbServerEndpointReservation": ...


class _ReservationState(Enum):
    TENTATIVE = "tentative"
    LEASED = "leased"


class AdbServerEndpointReservation:
    """One tentative process-local endpoint reservation.

    The endpoint stays reserved while the reservation is promoted, so promotion
    cannot open a process-local race window. A failed acquisition must release the
    reservation; a successful acquisition transfers responsibility to the returned
    :class:`AdbServerEndpointLease`.
    """

    __slots__ = ("endpoint", "_owner", "_token")

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        owner: "InMemoryAdbServerEndpointProvisioner",
        token: object,
    ) -> None:
        self.endpoint = endpoint
        self._owner = owner
        self._token = token

    @property
    def active(self) -> bool:
        return self._owner._has_reservation(
            self.endpoint,
            self._token,
            _ReservationState.TENTATIVE,
        )

    def promote(self) -> "AdbServerEndpointLease":
        """Atomically transfer this tentative reservation to a durable lease."""

        self._owner._promote(self.endpoint, self._token)
        return AdbServerEndpointLease(self.endpoint, self._owner, self._token)

    def release(self) -> None:
        """Release this reservation if it has not already been promoted or released."""

        self._owner._release(
            self.endpoint,
            self._token,
            expected_state=_ReservationState.TENTATIVE,
        )

    close = release

    def __enter__(self) -> "AdbServerEndpointReservation":
        if not self.active:
            raise RuntimeError("ADB server endpoint reservation is no longer active")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


class AdbServerEndpointLease:
    """Durable process-local reservation produced by atomic promotion."""

    __slots__ = ("endpoint", "_owner", "_token")

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        owner: "InMemoryAdbServerEndpointProvisioner",
        token: object,
    ) -> None:
        self.endpoint = endpoint
        self._owner = owner
        self._token = token

    @property
    def active(self) -> bool:
        return self._owner._has_reservation(
            self.endpoint,
            self._token,
            _ReservationState.LEASED,
        )

    def release(self) -> None:
        """Release this process-local lease without mutating the native server."""

        self._owner._release(
            self.endpoint,
            self._token,
            expected_state=_ReservationState.LEASED,
        )

    close = release

    def __enter__(self) -> "AdbServerEndpointLease":
        if not self.active:
            raise RuntimeError("ADB server endpoint lease is no longer active")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


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
        self._entries: dict[AdbServerEndpoint, tuple[object, _ReservationState]] = {}
        self._lock = Lock()

    def provision(
        self,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        """Permanently reserve an endpoint for the legacy provisioning API."""

        reservation = self.reserve(endpoint=endpoint)
        reservation.promote()
        return reservation.endpoint

    def reserve(
        self,
        *,
        endpoint: AdbServerEndpoint | None = None,
        excluded_endpoints: frozenset[AdbServerEndpoint] = frozenset(),
    ) -> AdbServerEndpointReservation:
        """Tentatively reserve one endpoint for an acquisition attempt.

        ``excluded_endpoints`` is acquisition-local history. It lets a caller
        release a failed reservation and still advance to the next candidate.
        """

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        if not isinstance(excluded_endpoints, frozenset):
            raise TypeError("excluded_endpoints must be a frozenset")
        for excluded in excluded_endpoints:
            if not isinstance(excluded, AdbServerEndpoint):
                raise TypeError(
                    "excluded_endpoints must contain AdbServerEndpoint values"
                )

        with self._lock:
            selected = endpoint
            if selected is None:
                unavailable = self._reserved | set(excluded_endpoints)
                selected = self._allocator.allocate(frozenset(unavailable))
                if not isinstance(selected, AdbServerEndpoint):
                    raise TypeError("allocator.allocate() must return AdbServerEndpoint")

            if selected in self._reserved or selected in excluded_endpoints:
                raise AdbServerEndpointConflictError(
                    f"ADB server endpoint {selected.host}:{selected.port} is already reserved"
                )

            token = object()
            self._reserved.add(selected)
            self._entries[selected] = (token, _ReservationState.TENTATIVE)
            return AdbServerEndpointReservation(selected, self, token)

    @property
    def reserved_endpoints(self) -> frozenset[AdbServerEndpoint]:
        """Return a point-in-time snapshot of process-local reservations."""

        with self._lock:
            return frozenset(self._reserved)

    def _has_reservation(
        self,
        endpoint: AdbServerEndpoint,
        token: object,
        expected_state: _ReservationState,
    ) -> bool:
        with self._lock:
            return self._entries.get(endpoint) == (token, expected_state)

    def _promote(self, endpoint: AdbServerEndpoint, token: object) -> None:
        with self._lock:
            current = self._entries.get(endpoint)
            if current != (token, _ReservationState.TENTATIVE):
                raise RuntimeError(
                    "ADB server endpoint reservation cannot be promoted after "
                    "release or prior promotion"
                )
            self._entries[endpoint] = (token, _ReservationState.LEASED)

    def _release(
        self,
        endpoint: AdbServerEndpoint,
        token: object,
        *,
        expected_state: _ReservationState,
    ) -> None:
        with self._lock:
            if self._entries.get(endpoint) != (token, expected_state):
                return
            del self._entries[endpoint]
            self._reserved.remove(endpoint)


__all__ = [
    "AdbServerEndpointAllocator",
    "AdbServerEndpointConflictError",
    "AdbServerEndpointExhaustedError",
    "AdbServerEndpointLease",
    "AdbServerEndpointProvisioner",
    "AdbServerEndpointProvisioningError",
    "AdbServerEndpointReservation",
    "AdbServerEndpointReservationProvider",
    "InMemoryAdbServerEndpointProvisioner",
    "SequentialAdbServerEndpointAllocator",
]
