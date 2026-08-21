from __future__ import annotations

from enum import Enum
from threading import Condition

from adb.server.acquisition import (
    AdbServerAcquirer,
    AdbServerAcquisition,
    AdbServerAcquisitionError,
    AdbServerAcquisitionPolicy,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.command import AdbServerStop, AdbServerStopper
from adb.server.status.model import AdbServerStatus
from native_attempt import NativeAttemptResult


_OWNER_CONSTRUCTION_TOKEN = object()


class AdbServerConfigurationConflictError(RuntimeError):
    """A process-owned server is already bound to a different endpoint lineage."""

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
    """One verified native ADB server lifetime created by this process.

    Ownership is creation-derived, not endpoint-derived: acquisition observed no listener,
    this process obtained positive native creation evidence, and then verified a compatible
    ADB listener at that endpoint. Once this lifetime is invalidated it never becomes owned
    again, even if a listener later exists at the same endpoint.
    """

    __slots__ = ("_acquisition", "_active")

    def __init__(self, acquisition: AdbServerAcquisition, *, _token: object) -> None:
        if _token is not _OWNER_CONSTRUCTION_TOKEN:
            raise TypeError("AdbOwnedServer values are created by ProcessAdbServerSlot")
        if not isinstance(acquisition, AdbServerAcquisition):
            raise TypeError("acquisition must be AdbServerAcquisition")
        self._acquisition = acquisition
        self._active = True

    @classmethod
    def _from_acquisition(cls, acquisition: AdbServerAcquisition) -> "AdbOwnedServer":
        return cls(acquisition, _token=_OWNER_CONSTRUCTION_TOKEN)

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._acquisition.endpoint

    @property
    def active(self) -> bool:
        return self._active

    @property
    def server_status(self) -> AdbServerStatus:
        """Server status captured by successful acquisition verification."""

        return self._acquisition.server_status

    @property
    def attempts(self) -> tuple[object, ...]:
        """Candidate evidence retained by successful acquisition."""

        return self._acquisition.attempts

    def _mark_lost(self) -> bool:
        """Permanently invalidate this owned lifetime."""

        if not self._active:
            return False
        self._active = False
        return True


class _ProcessAdbServerSlotStatus(str, Enum):
    EMPTY = "empty"
    CREATING = "creating"
    ACTIVE = "active"
    CLOSING = "closing"
    UNOWNED = "unowned"


class _ProcessAdbServerSlotState:
    def __init__(self) -> None:
        self.condition = Condition()
        self.status = _ProcessAdbServerSlotStatus.EMPTY
        self.owner: AdbOwnedServer | None = None
        self.requested_endpoint: AdbServerEndpoint | None = None
        self.acquirer: object | None = None


_PROCESS_ADB_SERVER_STATE = _ProcessAdbServerSlotState()


class ProcessAdbServerSlot:
    """Serialize process-owned ADB server lifetimes around one endpoint lineage.

    Initial acquisition may search untouched candidates. Once native creation may have
    occurred, the selected endpoint is pinned. Every later generation must be created again
    at that same endpoint; an existing listener, including one left by an older generation
    of this process, is treated as foreign and is never adopted.
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
                _ProcessAdbServerSlotStatus.CLOSING,
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
                    self._detach_owner_locked(state, state.owner.endpoint)
                    state.condition.notify_all()
                    raise AdbServerOwnershipLostError(
                        "process ADB server ownership is no longer active"
                    )
                self._require_compatible_endpoint(state.owner.endpoint, endpoint)
                return state.owner

            if state.status is _ProcessAdbServerSlotStatus.UNOWNED:
                pinned = state.requested_endpoint
                if endpoint is not None and pinned is not None and endpoint != pinned:
                    raise AdbServerConfigurationConflictError(pinned, endpoint)
                raise AdbServerOwnershipLostError(
                    "process ADB server endpoint is unowned; use recover()"
                )

            state.status = _ProcessAdbServerSlotStatus.CREATING
            state.requested_endpoint = endpoint
            state.acquirer = self._acquirer

        try:
            acquisition = self._acquirer.acquire(policy, endpoint=endpoint)
            owner = self._owner_from_acquisition(acquisition)
        except AdbServerAcquisitionError as exc:
            with state.condition:
                if exc.pinned_endpoint is not None:
                    state.status = _ProcessAdbServerSlotStatus.UNOWNED
                    state.requested_endpoint = exc.pinned_endpoint
                    state.acquirer = self._acquirer
                else:
                    self._reset_empty_locked(state)
                state.condition.notify_all()
            raise
        except BaseException:
            with state.condition:
                self._reset_empty_locked(state)
                state.condition.notify_all()
            raise

        with state.condition:
            state.owner = owner
            state.requested_endpoint = owner.endpoint
            state.status = _ProcessAdbServerSlotStatus.ACTIVE
            state.condition.notify_all()
        return owner

    def mark_lost(self, owner: AdbOwnedServer) -> bool:
        """Invalidate the current owner without attempting endpoint-based native teardown.

        The endpoint remains pinned. A future recovery must create a new native server at the
        same endpoint and therefore cannot adopt a listener left by this or any other process.
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
                self._detach_owner_locked(state, endpoint)
                state.condition.notify_all()
                return True
            if (
                state.status in {
                    _ProcessAdbServerSlotStatus.UNOWNED,
                    _ProcessAdbServerSlotStatus.CLOSING,
                    _ProcessAdbServerSlotStatus.CREATING,
                }
                and not owner.active
                and state.requested_endpoint == owner.endpoint
            ):
                return False
            raise AdbServerOwnershipLostError(
                "ADB server owner is not attached to the process slot"
            )

    def close(
        self,
        owner: AdbOwnedServer,
        stopper: AdbServerStopper,
    ) -> NativeAttemptResult:
        """Fence the current owned lifetime, issue its authorized native stop, and detach it.

        Native stop success is not required to end the Python ownership lifetime. The endpoint
        remains pinned afterwards; if a listener survived, the next recovery observes it as an
        existing foreign listener and refuses to adopt it.
        """

        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")
        if not callable(getattr(stopper, "stop", None)):
            raise TypeError("stopper must provide stop()")

        state = self._state
        with state.condition:
            if state.status is not _ProcessAdbServerSlotStatus.ACTIVE or state.owner is not owner:
                raise AdbServerOwnershipLostError(
                    "ADB server owner is not the process slot's current active owner"
                )
            endpoint = owner.endpoint
            owner._mark_lost()
            state.status = _ProcessAdbServerSlotStatus.CLOSING

        try:
            result = stopper.stop(AdbServerStop(endpoint))
            if not isinstance(result, NativeAttemptResult):
                raise TypeError("stopper.stop() must return NativeAttemptResult")
            return result
        finally:
            with state.condition:
                if state.owner is owner:
                    self._detach_owner_locked(state, endpoint)
                state.condition.notify_all()

    def recover(self, policy: AdbServerAcquisitionPolicy) -> AdbOwnedServer:
        """Create a fresh owner at the endpoint pinned by the prior server lifetime.

        Recovery never searches another endpoint and never adopts an existing listener. An
        older server from this process is indistinguishable from any other pre-existing
        listener once its owned lifetime has ended.
        """

        if not isinstance(policy, AdbServerAcquisitionPolicy):
            raise TypeError("policy must be AdbServerAcquisitionPolicy")

        state = self._state
        with state.condition:
            while state.status in {
                _ProcessAdbServerSlotStatus.CREATING,
                _ProcessAdbServerSlotStatus.CLOSING,
            }:
                state.condition.wait()

            if state.status is _ProcessAdbServerSlotStatus.ACTIVE:
                assert state.owner is not None
                return state.owner
            if state.status is not _ProcessAdbServerSlotStatus.UNOWNED:
                raise AdbServerOwnershipLostError(
                    "ADB server recovery requires an unowned pinned endpoint"
                )
            endpoint = state.requested_endpoint
            acquirer = state.acquirer
            if endpoint is None:
                raise RuntimeError("unowned ADB server slot has no pinned endpoint")
            if acquirer is None or not callable(getattr(acquirer, "acquire", None)):
                raise RuntimeError("unowned ADB server slot has no recovery acquirer")
            state.status = _ProcessAdbServerSlotStatus.CREATING

        try:
            acquisition = acquirer.acquire(policy, endpoint=endpoint)
            owner = self._owner_from_acquisition(acquisition)
        except BaseException:
            with state.condition:
                state.status = _ProcessAdbServerSlotStatus.UNOWNED
                state.owner = None
                state.requested_endpoint = endpoint
                state.acquirer = acquirer
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
                self._detach_owner_locked(state, endpoint)
                state.condition.notify_all()
                return None
            return state.owner

    @staticmethod
    def _owner_from_acquisition(acquisition: object) -> AdbOwnedServer:
        if not isinstance(acquisition, AdbServerAcquisition):
            raise TypeError("acquirer.acquire() must return AdbServerAcquisition")
        return AdbOwnedServer._from_acquisition(acquisition)

    @staticmethod
    def _detach_owner_locked(
        state: _ProcessAdbServerSlotState,
        endpoint: AdbServerEndpoint,
    ) -> None:
        state.owner = None
        state.requested_endpoint = endpoint
        state.status = _ProcessAdbServerSlotStatus.UNOWNED

    @staticmethod
    def _reset_empty_locked(state: _ProcessAdbServerSlotState) -> None:
        state.status = _ProcessAdbServerSlotStatus.EMPTY
        state.owner = None
        state.requested_endpoint = None
        state.acquirer = None

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


def close_process_adb_server(owner: AdbOwnedServer) -> NativeAttemptResult:
    """Close the current process-owned server using its creation-derived stop authority."""

    if not isinstance(owner, AdbOwnedServer):
        raise TypeError("owner must be AdbOwnedServer")
    from adb.server.lifecycle.adapters import SubprocessAdbServer

    return _PROCESS_ADB_SERVER_SLOT.close(owner, SubprocessAdbServer(owner.endpoint))


__all__ = [
    "AdbOwnedServer",
    "AdbServerConfigurationConflictError",
    "AdbServerOwnershipLostError",
    "ProcessAdbServerSlot",
    "acquire_process_adb_server",
    "close_process_adb_server",
]
