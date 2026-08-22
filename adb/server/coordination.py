from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Condition
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServer, _AdbServerSequence
from adb.server.model import AdbServerEndpoint
from adb.server.ownership import _OwnedAdbServerLifetimeStore


_MUTATION_LEASE_CONSTRUCTION_TOKEN = object()


class AdbServerMutationReservedError(RuntimeError):
    """Process ADB server mutation is reserved by an exclusive coordinator client."""


class _AdbServerMutationLease:
    """Opaque authority for mutations inside the process ADB coordination domain."""

    __slots__ = ()

    def __init__(self, *, _token: object) -> None:
        if _token is not _MUTATION_LEASE_CONSTRUCTION_TOKEN:
            raise TypeError("ADB server mutation leases are created by process coordination")

    @classmethod
    def _new(cls) -> "_AdbServerMutationLease":
        return cls(_token=_MUTATION_LEASE_CONSTRUCTION_TOKEN)


@runtime_checkable
class _AdbServerCoordination(Protocol):
    """Process coordination port consumed by higher-level lifecycle policy."""

    def claim_mutation_authority(
        self,
        expected_current: AdbServer | None = None,
    ) -> _AdbServerMutationLease: ...

    def release_mutation_authority(self, lease: _AdbServerMutationLease) -> None: ...

    def acquire_server(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> AdbServer: ...

    def retire_server(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool: ...

    def dispose_retired(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None: ...

    @property
    def active_server(self) -> AdbServer | None: ...


class _ProcessAdbServerCoordinator:
    """Fence process-wide mutations independently from public server identity.

    The owned lifetime store retains exact native handles. This coordinator defines the singleton
    mutation domain, owns server epoch generation, and grants optional exclusive authority. A lease
    survives temporary absence of an active server so recovery cannot be raced by unrelated process
    callers.
    """

    def __init__(
        self,
        lifetimes: _OwnedAdbServerLifetimeStore,
        *,
        server_sequence: _AdbServerSequence | None = None,
    ) -> None:
        if not isinstance(lifetimes, _OwnedAdbServerLifetimeStore):
            raise TypeError("lifetimes must be _OwnedAdbServerLifetimeStore")
        if server_sequence is None:
            server_sequence = _AdbServerSequence()
        elif not isinstance(server_sequence, _AdbServerSequence):
            raise TypeError("server_sequence must be _AdbServerSequence")
        self._lifetimes = lifetimes
        self._servers = server_sequence
        self._condition = Condition()
        self._mutation_lease: _AdbServerMutationLease | None = None
        self._claim_pending = False
        self._release_pending = False
        self._unleased_mutations_in_flight = 0
        self._leased_mutations_in_flight = 0

    def claim_mutation_authority(
        self,
        expected_current: AdbServer | None = None,
    ) -> _AdbServerMutationLease:
        """Reserve mutations after fencing against the expected current server.

        ``expected_current`` gives claim CAS-like semantics: after already-admitted ordinary
        mutations drain, the claim succeeds only if that server is still current. Once granted,
        the lease may span current -> absent -> fresh server transitions during recovery.
        """

        if expected_current is not None and not isinstance(expected_current, AdbServer):
            raise TypeError("expected_current must be AdbServer or None")
        with self._condition:
            if self._mutation_lease is not None or self._claim_pending:
                raise RuntimeError("ADB server mutation authority is already claimed")
            self._claim_pending = True
            try:
                while self._unleased_mutations_in_flight:
                    self._condition.wait()
                if expected_current is not None:
                    active = self._lifetimes.active_server
                    if active != expected_current:
                        raise ValueError("expected ADB server is not the active server")
                lease = _AdbServerMutationLease._new()
                self._mutation_lease = lease
                return lease
            finally:
                self._claim_pending = False
                self._condition.notify_all()

    def release_mutation_authority(self, lease: _AdbServerMutationLease) -> None:
        self._require_lease_type(lease)
        with self._condition:
            if self._mutation_lease is not lease or self._release_pending:
                raise RuntimeError("ADB server mutation lease is not active")
            self._release_pending = True
            try:
                while self._leased_mutations_in_flight:
                    self._condition.wait()
                self._mutation_lease = None
            finally:
                self._release_pending = False
                self._condition.notify_all()

    def acquire_server(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> AdbServer:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        active = self._lifetimes.active_server
        if active is not None:
            if lease is not None:
                with self._condition:
                    self._require_active_lease_locked(lease)
            return active

        with self._mutation_scope(lease):
            return self._lifetimes.acquire(
                endpoint,
                server_factory=self._servers.next,
            )

    def retire_server(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool:
        self._require_server_type(server)
        with self._mutation_scope(lease):
            return self._lifetimes.retire(server)

    def dispose_retired(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None:
        self._require_server_type(server)
        with self._mutation_scope(lease):
            self._lifetimes.dispose_retired(server)

    def invalidate_server(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool:
        self._require_server_type(server)
        with self._mutation_scope(lease):
            return self._lifetimes.invalidate(server)

    def close_server(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None:
        self._require_server_type(server)
        with self._mutation_scope(lease):
            self._lifetimes.close(server)

    # Compatibility projections for callers of the former private control primitive.
    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> AdbServer:
        return self.acquire_server(endpoint, lease=lease)

    def retire(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool:
        return self.retire_server(server, lease=lease)

    def invalidate(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool:
        return self.invalidate_server(server, lease=lease)

    def close(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None:
        self.close_server(server, lease=lease)

    @property
    def active_server(self) -> AdbServer | None:
        return self._lifetimes.active_server

    @contextmanager
    def _mutation_scope(
        self,
        lease: _AdbServerMutationLease | None,
    ) -> Iterator[None]:
        leased = self._begin_mutation(lease)
        try:
            yield
        finally:
            self._end_mutation(leased=leased)

    def _begin_mutation(self, lease: _AdbServerMutationLease | None) -> bool:
        with self._condition:
            if lease is None:
                while self._claim_pending:
                    self._condition.wait()
                if self._mutation_lease is not None:
                    raise AdbServerMutationReservedError(
                        "ADB server mutation is reserved by an active coordinator client"
                    )
                self._unleased_mutations_in_flight += 1
                return False

            self._require_lease_type(lease)
            self._require_active_lease_locked(lease)
            self._leased_mutations_in_flight += 1
            return True

    def _end_mutation(self, *, leased: bool) -> None:
        with self._condition:
            if leased:
                self._leased_mutations_in_flight -= 1
            else:
                self._unleased_mutations_in_flight -= 1
            self._condition.notify_all()

    def _require_active_lease_locked(self, lease: _AdbServerMutationLease) -> None:
        self._require_lease_type(lease)
        if self._mutation_lease is not lease or self._release_pending:
            raise RuntimeError("ADB server mutation lease is not active")

    @staticmethod
    def _require_lease_type(lease: object) -> None:
        if not isinstance(lease, _AdbServerMutationLease):
            raise TypeError("lease must be _AdbServerMutationLease")

    @staticmethod
    def _require_server_type(server: object) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")


_PROCESS_ADB_SERVER_LIFETIMES = _OwnedAdbServerLifetimeStore()
_PROCESS_ADB_SERVER_COORDINATOR = _ProcessAdbServerCoordinator(_PROCESS_ADB_SERVER_LIFETIMES)

# Private compatibility aliases. Coordination, not control, owns these implementations.
_AdbServerControl = _AdbServerCoordination
_ProcessAdbServerControl = _ProcessAdbServerCoordinator
_PROCESS_ADB_SERVER_CONTROL = _PROCESS_ADB_SERVER_COORDINATOR


__all__ = ["AdbServerMutationReservedError"]
