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
_REFERENCE_CONSTRUCTION_TOKEN = object()


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
    """The process slot can no longer provide its original session-created owner."""


class AdbServerRef:
    """Borrow-only reference to the process-owned, session-created ADB server.

    The reference proves how the process obtained its managed server: the backing owner
    came from positive creation evidence in this process session. It deliberately does
    not claim that a later listener at the endpoint has the same native PID or process
    identity.

    References are created only by :class:`AdbOwnedServer`; callers cannot construct a
    managed reference from a bare endpoint.
    """

    __slots__ = ("_owner",)

    def __init__(self, owner: "AdbOwnedServer", *, _token: object) -> None:
        if _token is not _REFERENCE_CONSTRUCTION_TOKEN:
            raise TypeError("AdbServerRef values are created by AdbOwnedServer")
        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")
        self._owner = owner

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._owner.endpoint

    @property
    def active(self) -> bool:
        """Whether the backing process-local endpoint lease remains active."""

        return self._owner.active

    def __repr__(self) -> str:
        endpoint = self.endpoint
        return f"AdbServerRef({endpoint.host!r}, {endpoint.port})"


class AdbOwnedServer:
    """The process slot's one session-created managed ADB server.

    ``owned`` is intentionally limited to session-created provenance: acquisition
    obtained positive launcher creation evidence and then verified a compatible ADB
    listener. This object does not claim continuous native-process identity and exposes
    no public native termination operation.
    """

    __slots__ = ("_lease", "_reference")

    def __init__(self, lease: AdbServerLease, *, _token: object) -> None:
        if _token is not _OWNER_CONSTRUCTION_TOKEN:
            raise TypeError("AdbOwnedServer values are created by ProcessAdbServerSlot")
        if not isinstance(lease, AdbServerLease):
            raise TypeError("lease must be AdbServerLease")
        if not lease.active:
            raise ValueError("lease must be active")
        self._lease = lease
        self._reference = AdbServerRef(self, _token=_REFERENCE_CONSTRUCTION_TOKEN)

    @classmethod
    def _from_lease(cls, lease: AdbServerLease) -> "AdbOwnedServer":
        return cls(lease, _token=_OWNER_CONSTRUCTION_TOKEN)

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._lease.endpoint

    @property
    def active(self) -> bool:
        return self._lease.active

    @property
    def server_status(self) -> AdbServerStatus:
        """Server status captured by the successful acquisition verification."""

        return self._lease.server_status

    @property
    def attempts(self) -> tuple[object, ...]:
        """Candidate evidence retained by the successful acquisition."""

        return self._lease.attempts

    def borrow(self) -> AdbServerRef:
        """Return the stable borrow-only reference shared by managed consumers."""

        return self._reference


class _ProcessAdbServerSlotStatus(str, Enum):
    EMPTY = "empty"
    CREATING = "creating"
    ACTIVE = "active"
    LOST = "lost"


class _ProcessAdbServerSlotState:
    def __init__(self) -> None:
        self.condition = Condition()
        self.status = _ProcessAdbServerSlotStatus.EMPTY
        self.owner: AdbOwnedServer | None = None
        self.reference: AdbServerRef | None = None
        self.requested_endpoint: AdbServerEndpoint | None = None


_PROCESS_ADB_SERVER_STATE = _ProcessAdbServerSlotState()


class ProcessAdbServerSlot:
    """Serialize managed ADB server acquisition into one process-level slot.

    All ordinary instances share one module-level state. Concurrent callers therefore
    perform at most one acquisition, then receive the same :class:`AdbServerRef`.
    Acquisition episode policy is not persistent configuration; an explicit endpoint is.
    Once an owner has been established, requesting a different explicit endpoint raises
    :class:`AdbServerConfigurationConflictError`.

    The slot deliberately has no public ``close`` or reset operation. Without a native
    process identity/handle, releasing ownership and later treating an endpoint listener
    as the same owned server would be unsound. The session-created owner is therefore a
    process-lifetime service in this first ownership model.
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
    ) -> AdbServerRef:
        if not isinstance(policy, AdbServerAcquisitionPolicy):
            raise TypeError("policy must be AdbServerAcquisitionPolicy")
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        state = self._state
        with state.condition:
            while state.status is _ProcessAdbServerSlotStatus.CREATING:
                creating_endpoint = state.requested_endpoint
                if (
                    endpoint is not None
                    and creating_endpoint is not None
                    and endpoint != creating_endpoint
                ):
                    raise AdbServerConfigurationConflictError(
                        creating_endpoint,
                        endpoint,
                    )
                state.condition.wait()

            if state.status is _ProcessAdbServerSlotStatus.ACTIVE:
                assert state.owner is not None
                assert state.reference is not None
                if not state.owner.active:
                    state.status = _ProcessAdbServerSlotStatus.LOST
                    raise AdbServerOwnershipLostError(
                        "process ADB server endpoint reservation is no longer active"
                    )
                self._require_compatible_endpoint(state.owner.endpoint, endpoint)
                return state.reference

            if state.status is _ProcessAdbServerSlotStatus.LOST:
                raise AdbServerOwnershipLostError(
                    "process ADB server ownership was lost and cannot be reconstructed "
                    "from endpoint observations"
                )

            state.status = _ProcessAdbServerSlotStatus.CREATING
            state.requested_endpoint = endpoint

        lease: AdbServerLease | None = None
        try:
            lease = self._acquirer.acquire(policy, endpoint=endpoint)
            if not isinstance(lease, AdbServerLease):
                raise TypeError("acquirer.acquire() must return AdbServerLease")
            if not lease.active:
                raise ValueError("acquirer returned an inactive AdbServerLease")
            owner = AdbOwnedServer._from_lease(lease)
            reference = owner.borrow()
        except BaseException:
            if lease is not None and lease.active:
                lease.release()
            with state.condition:
                state.status = _ProcessAdbServerSlotStatus.EMPTY
                state.owner = None
                state.reference = None
                state.requested_endpoint = None
                state.condition.notify_all()
            raise

        with state.condition:
            state.owner = owner
            state.reference = reference
            state.requested_endpoint = owner.endpoint
            state.status = _ProcessAdbServerSlotStatus.ACTIVE
            state.condition.notify_all()
        return reference

    @property
    def active_reference(self) -> AdbServerRef | None:
        """Return the current borrow reference without creating a server."""

        state = self._state
        with state.condition:
            if state.status is not _ProcessAdbServerSlotStatus.ACTIVE:
                return None
            assert state.owner is not None
            assert state.reference is not None
            if not state.owner.active:
                state.status = _ProcessAdbServerSlotStatus.LOST
                return None
            return state.reference

    @staticmethod
    def _require_compatible_endpoint(
        active_endpoint: AdbServerEndpoint,
        requested_endpoint: AdbServerEndpoint | None,
    ) -> None:
        if requested_endpoint is not None and requested_endpoint != active_endpoint:
            raise AdbServerConfigurationConflictError(
                active_endpoint,
                requested_endpoint,
            )


_PROCESS_ADB_SERVER_SLOT = ProcessAdbServerSlot()


def acquire_process_adb_server(
    policy: AdbServerAcquisitionPolicy,
    *,
    endpoint: AdbServerEndpoint | None = None,
) -> AdbServerRef:
    """Acquire or borrow the process-level session-created managed ADB server."""

    return _PROCESS_ADB_SERVER_SLOT.acquire(policy, endpoint=endpoint)


__all__ = [
    "AdbOwnedServer",
    "AdbServerConfigurationConflictError",
    "AdbServerOwnershipLostError",
    "AdbServerRef",
    "ProcessAdbServerSlot",
    "acquire_process_adb_server",
]
