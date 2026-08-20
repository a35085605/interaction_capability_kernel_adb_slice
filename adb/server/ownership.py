from __future__ import annotations

from enum import Enum
from threading import Condition

from adb.server.acquisition import (
    AdbServerAcquirer,
    AdbServerAcquisitionPolicy,
    AdbServerLease,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.server.status.model import AdbServerStatus


_OWNER_CONSTRUCTION_TOKEN = object()


class AdbServerConfigurationConflictError(RuntimeError):
    """A process-owned server is already active with incompatible configuration."""

    def __init__(
        self,
        active_endpoint: AdbServerEndpoint,
        requested_endpoint: AdbServerEndpoint,
    ) -> None:
        self.active_endpoint = active_endpoint
        self.requested_endpoint = requested_endpoint
        super().__init__(
            "process ADB server already uses "
            f"{active_endpoint.host}:{active_endpoint.port}; requested "
            f"{requested_endpoint.host}:{requested_endpoint.port}"
        )


class AdbServerOwnershipLostError(RuntimeError):
    """The process slot has no currently usable owned ADB server."""


class AdbOwnedServer:
    """One session-created managed ADB server owned by the process slot.

    ``owned`` is creation-derived: acquisition observed no listener, obtained positive
    creation evidence, and then verified a compatible ADB listener at the same endpoint.
    The object deliberately does not claim continuous native PID/process identity.

    The object is a lifetime capability, not a generation identifier. Once marked lost it
    becomes permanently inactive and is never revived; recovery creates a fresh
    :class:`AdbOwnedServer` instance instead.
    """

    __slots__ = ("_lease", "_logically_active")

    def __init__(self, lease: AdbServerLease, *, _token: object) -> None:
        if _token is not _OWNER_CONSTRUCTION_TOKEN:
            raise TypeError("AdbOwnedServer values are created by ProcessAdbServerSlot")
        if not isinstance(lease, AdbServerLease):
            raise TypeError("lease must be AdbServerLease")
        if not lease.active:
            raise ValueError("lease must be active")
        self._lease = lease
        self._logically_active = True

    @classmethod
    def _from_lease(cls, lease: AdbServerLease) -> "AdbOwnedServer":
        return cls(lease, _token=_OWNER_CONSTRUCTION_TOKEN)

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._lease.endpoint

    @property
    def active(self) -> bool:
        return self._logically_active and self._lease.active

    @property
    def server_status(self) -> AdbServerStatus:
        """Server status captured by successful acquisition verification."""

        return self._lease.server_status

    @property
    def attempts(self) -> tuple[object, ...]:
        """Candidate evidence retained by successful acquisition."""

        return self._lease.attempts

    def _mark_lost(self) -> bool:
        """Invalidate this lifetime and release only its process-local endpoint lease."""

        if not self._logically_active:
            return False
        self._logically_active = False
        if self._lease.active:
            self._lease.release()
        return True


class _ProcessAdbServerSlotStatus(str, Enum):
    EMPTY = "empty"
    CREATING = "creating"
    ACTIVE = "active"
    LOST = "lost"
    RECOVERING = "recovering"


class _ProcessAdbServerSlotState:
    def __init__(self) -> None:
        self.condition = Condition()
        self.status = _ProcessAdbServerSlotStatus.EMPTY
        self.owner: AdbOwnedServer | None = None
        self.requested_endpoint: AdbServerEndpoint | None = None
        self.acquirer: object | None = None


_PROCESS_ADB_SERVER_STATE = _ProcessAdbServerSlotState()


class ProcessAdbServerSlot:
    """Serialize managed ADB ownership into one process-level slot.

    Initial acquisition may search candidates. Recovery is pinned to the endpoint whose
    previous owner was invalidated. Existing listeners are never adopted: every new owner
    must come through the normal no-listener -> create -> verify acquisition path.

    Lost owners are removed from the active slot immediately. The slot keeps only the
    endpoint and acquisition capability needed to attempt fresh ownership later.
    """

    def __init__(
        self,
        acquirer: object | None = None,
        *,
        _state: _ProcessAdbServerSlotState | None = None,
    ) -> None:
        if acquirer is None:
            acquirer = AdbServerAcquirer()
        if not callable(getattr(acquirer, "acquire", None)):
            raise TypeError("acquirer must provide acquire()")
        if _state is not None and not isinstance(_state, _ProcessAdbServerSlotState):
            raise TypeError("_state must be _ProcessAdbServerSlotState or None")
        self._acquirer = acquirer
        self._state = _PROCESS_ADB_SERVER_STATE if _state is None else _state

    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbOwnedServer:
        if not isinstance(policy, AdbServerAcquisitionPolicy):
            raise TypeError("policy must be AdbServerAcquisitionPolicy")
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        state = self._state
        with state.condition:
            while state.status in {
                _ProcessAdbServerSlotStatus.CREATING,
                _ProcessAdbServerSlotStatus.RECOVERING,
            }:
                active_endpoint = state.requested_endpoint
                if (
                    endpoint is not None
                    and active_endpoint is not None
                    and endpoint != active_endpoint
                ):
                    raise AdbServerConfigurationConflictError(active_endpoint, endpoint)
                state.condition.wait()

            if state.status is _ProcessAdbServerSlotStatus.ACTIVE:
                assert state.owner is not None
                if not state.owner.active:
                    state.owner._mark_lost()
                    state.owner = None
                    state.status = _ProcessAdbServerSlotStatus.LOST
                    state.condition.notify_all()
                    raise AdbServerOwnershipLostError(
                        "process ADB server ownership is no longer active"
                    )
                self._require_compatible_endpoint(state.owner.endpoint, endpoint)
                return state.owner

            if state.status is _ProcessAdbServerSlotStatus.LOST:
                raise AdbServerOwnershipLostError(
                    "process ADB server ownership is lost; use recover()"
                )

            state.status = _ProcessAdbServerSlotStatus.CREATING
            state.requested_endpoint = endpoint
            state.acquirer = self._acquirer

        lease: object | None = None
        try:
            lease = self._acquirer.acquire(policy, endpoint=endpoint)
            owner = self._owner_from_lease(lease)
        except BaseException:
            if isinstance(lease, AdbServerLease) and lease.active:
                lease.release()
            with state.condition:
                state.status = _ProcessAdbServerSlotStatus.EMPTY
                state.owner = None
                state.requested_endpoint = None
                state.acquirer = None
                state.condition.notify_all()
            raise

        with state.condition:
            state.owner = owner
            state.requested_endpoint = owner.endpoint
            state.status = _ProcessAdbServerSlotStatus.ACTIVE
            state.condition.notify_all()
        return owner

    def mark_lost(self, owner: AdbOwnedServer) -> bool:
        """Remove the current owner from service after terminal liveness evidence.

        The old owner becomes permanently inactive. No endpoint observation can restore it.
        Recovery, if desired, must create a fresh owner at the remembered endpoint.
        """

        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")
        state = self._state
        with state.condition:
            if state.status is _ProcessAdbServerSlotStatus.ACTIVE:
                if state.owner is not owner:
                    raise AdbServerOwnershipLostError(
                        "ADB server owner is not the process slot's current owner"
                    )
                endpoint = owner.endpoint
                owner._mark_lost()
                state.owner = None
                state.requested_endpoint = endpoint
                state.status = _ProcessAdbServerSlotStatus.LOST
                state.condition.notify_all()
                return True
            if (
                state.status in {
                    _ProcessAdbServerSlotStatus.LOST,
                    _ProcessAdbServerSlotStatus.RECOVERING,
                }
                and not owner.active
                and state.requested_endpoint == owner.endpoint
            ):
                return False
            raise AdbServerOwnershipLostError(
                "ADB server owner is not attached to the process slot"
            )

    def recover(self, policy: AdbServerAcquisitionPolicy) -> AdbOwnedServer:
        """Create a fresh owner at the endpoint remembered by the LOST slot.

        Recovery never searches another endpoint and never adopts an existing listener.
        A failed acquisition leaves the slot LOST for a later retry.
        """

        if not isinstance(policy, AdbServerAcquisitionPolicy):
            raise TypeError("policy must be AdbServerAcquisitionPolicy")

        state = self._state
        with state.condition:
            while state.status is _ProcessAdbServerSlotStatus.RECOVERING:
                state.condition.wait()

            if state.status is _ProcessAdbServerSlotStatus.ACTIVE:
                assert state.owner is not None
                return state.owner
            if state.status is not _ProcessAdbServerSlotStatus.LOST:
                raise AdbServerOwnershipLostError(
                    "ADB server recovery requires a LOST process slot"
                )
            endpoint = state.requested_endpoint
            acquirer = state.acquirer
            if endpoint is None:
                raise RuntimeError("LOST ADB server slot has no recovery endpoint")
            if acquirer is None or not callable(getattr(acquirer, "acquire", None)):
                raise RuntimeError("LOST ADB server slot has no recovery acquirer")
            state.status = _ProcessAdbServerSlotStatus.RECOVERING

        lease: object | None = None
        try:
            lease = acquirer.acquire(policy, endpoint=endpoint)
            owner = self._owner_from_lease(lease)
        except BaseException:
            if isinstance(lease, AdbServerLease) and lease.active:
                lease.release()
            with state.condition:
                state.status = _ProcessAdbServerSlotStatus.LOST
                state.owner = None
                state.requested_endpoint = endpoint
                state.condition.notify_all()
            raise

        with state.condition:
            state.owner = owner
            state.requested_endpoint = owner.endpoint
            state.status = _ProcessAdbServerSlotStatus.ACTIVE
            state.condition.notify_all()
        return owner

    @property
    def active_owner(self) -> AdbOwnedServer | None:
        """Return the current owner without creating or recovering a server."""

        state = self._state
        with state.condition:
            if state.status is not _ProcessAdbServerSlotStatus.ACTIVE:
                return None
            assert state.owner is not None
            if not state.owner.active:
                endpoint = state.owner.endpoint
                state.owner._mark_lost()
                state.owner = None
                state.requested_endpoint = endpoint
                state.status = _ProcessAdbServerSlotStatus.LOST
                state.condition.notify_all()
                return None
            return state.owner

    @staticmethod
    def _owner_from_lease(lease: object) -> AdbOwnedServer:
        if not isinstance(lease, AdbServerLease):
            raise TypeError("acquirer.acquire() must return AdbServerLease")
        if not lease.active:
            raise ValueError("acquirer returned an inactive AdbServerLease")
        return AdbOwnedServer._from_lease(lease)

    @staticmethod
    def _require_compatible_endpoint(
        active_endpoint: AdbServerEndpoint,
        requested_endpoint: AdbServerEndpoint | None,
    ) -> None:
        if requested_endpoint is not None and requested_endpoint != active_endpoint:
            raise AdbServerConfigurationConflictError(active_endpoint, requested_endpoint)


_PROCESS_ADB_SERVER_SLOT = ProcessAdbServerSlot()


def acquire_process_adb_server(
    policy: AdbServerAcquisitionPolicy,
    *,
    endpoint: AdbServerEndpoint | None = None,
) -> AdbOwnedServer:
    """Acquire or return the process-level session-created managed ADB server."""

    return _PROCESS_ADB_SERVER_SLOT.acquire(policy, endpoint=endpoint)


__all__ = [
    "AdbOwnedServer",
    "AdbServerConfigurationConflictError",
    "AdbServerOwnershipLostError",
    "ProcessAdbServerSlot",
    "acquire_process_adb_server",
]
