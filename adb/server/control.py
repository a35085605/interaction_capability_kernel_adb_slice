from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Condition
from typing import Protocol, runtime_checkable

from adb.server.model import AdbServerEndpoint
from adb.server.ownership import AdbOwnedServer, _ProcessAdbServerOwner


_MUTATION_LEASE_CONSTRUCTION_TOKEN = object()


class AdbServerMutationReservedError(RuntimeError):
    """Process ADB server mutation is reserved by an exclusive controller."""


class _AdbServerMutationLease:
    """Opaque authority for process ADB server mutations while control is reserved."""

    __slots__ = ()

    def __init__(self, *, _token: object) -> None:
        if _token is not _MUTATION_LEASE_CONSTRUCTION_TOKEN:
            raise TypeError("ADB server mutation leases are created by process control")

    @classmethod
    def _new(cls) -> "_AdbServerMutationLease":
        return cls(_token=_MUTATION_LEASE_CONSTRUCTION_TOKEN)


@runtime_checkable
class _AdbServerControl(Protocol):
    """Mutation-control port consumed by higher-level lifecycle policy."""

    def claim_mutation_authority(self, owner: AdbOwnedServer) -> _AdbServerMutationLease: ...

    def release_mutation_authority(self, lease: _AdbServerMutationLease) -> None: ...

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> AdbOwnedServer: ...

    def retire(
        self,
        owner: AdbOwnedServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool: ...

    def dispose_retired(
        self,
        owner: AdbOwnedServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None: ...

    @property
    def active_owner(self) -> AdbOwnedServer | None: ...


class _ProcessAdbServerControl:
    """Arbitrate mutation authority above the process-owned server lifetime primitive.

    The underlying owner only serializes native lifetime state. This layer decides whether
    process-wide mutations are currently available to ordinary callers or reserved by one
    durable controller. The reservation survives temporary absence of an active generation so
    recovery cannot be raced by unrelated acquire, invalidate, or close calls.

    Authority fencing is separate from operation serialization. Mutations carrying the same
    active lease may proceed concurrently, which preserves per-generation recovery policies that
    intentionally launch a fresh lifetime while an older retired lifetime is still closing.
    """

    def __init__(self, owner: _ProcessAdbServerOwner) -> None:
        if not isinstance(owner, _ProcessAdbServerOwner):
            raise TypeError("owner must be _ProcessAdbServerOwner")
        self._owner = owner
        self._condition = Condition()
        self._mutation_lease: _AdbServerMutationLease | None = None
        self._claim_pending = False
        self._release_pending = False
        self._unleased_mutations_in_flight = 0
        self._leased_mutations_in_flight = 0

    def claim_mutation_authority(self, owner: AdbOwnedServer) -> _AdbServerMutationLease:
        """Reserve process-wide mutations while ``owner`` is the active generation.

        The claim fences ordinary mutations that were already admitted before the claim began and
        prevents new ordinary mutations from entering until the claim either succeeds or fails.
        """

        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")
        with self._condition:
            if self._mutation_lease is not None or self._claim_pending:
                raise RuntimeError("ADB server mutation authority is already claimed")
            self._claim_pending = True
            try:
                while self._unleased_mutations_in_flight:
                    self._condition.wait()
                if self._owner.active_owner is not owner:
                    raise ValueError("server must be the process owner's active generation")
                lease = _AdbServerMutationLease._new()
                self._mutation_lease = lease
                return lease
            finally:
                self._claim_pending = False
                self._condition.notify_all()

    def release_mutation_authority(self, lease: _AdbServerMutationLease) -> None:
        """Release one exact mutation reservation without changing server ownership."""

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

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> AdbOwnedServer:
        """Return the active generation or, when authorized, launch one fresh generation.

        Returning an already-active owner is observational and remains available while mutation
        is reserved. Creating a generation from an absent state is a mutation and therefore
        requires the active lease when control is reserved.
        """

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        active = self._owner.active_owner
        if active is not None:
            if lease is not None:
                with self._condition:
                    self._require_active_lease_locked(lease)
            return active

        with self._mutation_scope(lease):
            return self._owner.acquire(endpoint)

    def retire(
        self,
        owner: AdbOwnedServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool:
        with self._mutation_scope(lease):
            return self._owner.retire(owner)

    def dispose_retired(
        self,
        owner: AdbOwnedServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None:
        with self._mutation_scope(lease):
            self._owner.dispose_retired(owner)

    def invalidate(
        self,
        owner: AdbOwnedServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool:
        with self._mutation_scope(lease):
            return self._owner.invalidate(owner)

    def close(
        self,
        owner: AdbOwnedServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None:
        with self._mutation_scope(lease):
            self._owner.close(owner)

    @property
    def active_owner(self) -> AdbOwnedServer | None:
        """Return the current public ownership projection without launching a generation."""

        return self._owner.active_owner

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
                        "ADB server mutation is reserved by an active controller"
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


_PROCESS_ADB_SERVER_OWNER = _ProcessAdbServerOwner()
_PROCESS_ADB_SERVER_CONTROL = _ProcessAdbServerControl(_PROCESS_ADB_SERVER_OWNER)


__all__ = ["AdbServerMutationReservedError"]
