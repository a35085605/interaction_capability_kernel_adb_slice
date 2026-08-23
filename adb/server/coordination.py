from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Condition
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer
from adb.server.lifecycle.control.port import (
    AdbServerController,
    AdbServerStart,
    AdbServerStop,
)


_MUTATION_LEASE_CONSTRUCTION_TOKEN = object()


class AdbServerMutationReservedError(RuntimeError):
    """Process ADB server mutation is reserved by an exclusive coordinator client."""


class AdbServerUnavailableError(RuntimeError):
    """No usable ADB server is currently available."""


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
    """Internal contract for process-wide ADB server coordination."""

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
    """Coordinate process-wide ADB server identity and mutation authority."""

    def __init__(self, controller: AdbServerController) -> None:
        if not isinstance(controller, AdbServerController):
            raise TypeError("controller must satisfy AdbServerController")

        self._controller = controller
        self._condition = Condition()
        self._active_server: AdbServer | None = None
        self._retired_servers: set[AdbServer] = set()
        self._server_starting = False
        # A claimed lease remains valid across retire/acquire transitions until released.
        self._mutation_lease: _AdbServerMutationLease | None = None
        self._claim_pending = False
        self._release_pending = False
        self._unleased_mutations_in_flight = 0
        self._leased_mutations_in_flight = 0

    def claim_mutation_authority(
        self,
        expected_current: AdbServer | None = None,
    ) -> _AdbServerMutationLease:
        """Reserve mutation authority for the expected current server, if any."""

        if expected_current is not None and not isinstance(expected_current, AdbServer):
            raise TypeError("expected_current must be AdbServer or None")
        with self._condition:
            if self._mutation_lease is not None or self._claim_pending:
                raise RuntimeError("ADB server mutation authority is already claimed")
            self._claim_pending = True
            try:
                # Check expected_current only after previously admitted mutations drain.
                while self._unleased_mutations_in_flight:
                    self._condition.wait()
                if expected_current is not None and self._active_server != expected_current:
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

        with self._condition:
            active = self._active_server
            if active is not None:
                if lease is not None:
                    self._require_active_lease_locked(lease)
                return active

        with self._mutation_scope(lease):
            return self._acquire_absent_server(endpoint)

    def retire_server(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> bool:
        self._require_server_type(server)
        with self._mutation_scope(lease):
            with self._condition:
                if server in self._retired_servers:
                    return False
                if self._active_server != server:
                    return False
                self._active_server = None
                self._retired_servers.add(server)
                self._condition.notify_all()
                return True

    def dispose_retired(
        self,
        server: AdbServer,
        *,
        lease: _AdbServerMutationLease | None = None,
    ) -> None:
        self._require_server_type(server)
        with self._mutation_scope(lease):
            with self._condition:
                if server not in self._retired_servers:
                    raise RuntimeError("ADB server is not pending retired disposal")

            stopped = self._controller.stop(server)
            if not isinstance(stopped, AdbServerStop):
                raise TypeError("controller.stop() must return AdbServerStop")
            if stopped.server != server:
                raise ValueError("controller.stop() returned a different server identity")

            with self._condition:
                self._retired_servers.discard(server)
                self._condition.notify_all()

    @property
    def active_server(self) -> AdbServer | None:
        with self._condition:
            return self._active_server

    def _acquire_absent_server(self, endpoint: AdbServerEndpoint | None) -> AdbServer:
        with self._condition:
            while self._server_starting:
                self._condition.wait()
            if self._active_server is not None:
                return self._active_server
            self._server_starting = True

        try:
            started = self._controller.start(endpoint)
            if not isinstance(started, AdbServerStart):
                raise TypeError("controller.start() must return AdbServerStart")
            server = started.server
        except BaseException:
            with self._condition:
                self._server_starting = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._active_server = server
            self._server_starting = False
            self._condition.notify_all()
            return server

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


__all__ = ["AdbServerMutationReservedError", "AdbServerUnavailableError"]
