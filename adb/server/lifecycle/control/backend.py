from __future__ import annotations

from enum import Enum, auto
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.control.errors import (
    AdbServerAcquireInProgressError,
    AdbServerAttachmentMismatchError,
    AdbServerNativeLifetimeBusyError,
    AdbServerNativeTerminationUnprovenError,
    AdbServerNoAttachmentError,
    AdbServerStopInProgressError,
)


class AdbServerBackendPhase(Enum):
    """Port-defined lifecycle phase for one backend-scoped server attachment."""

    IDLE = auto()
    ACQUIRING = auto()
    ACTIVE = auto()
    RELEASING = auto()
    INDETERMINATE = auto()


class AdbServerBackendLifecycle:
    """Port-owned lifecycle state machine shared by server backend adapters.

    The lifecycle owns request-admission and transition semantics, but deliberately does not own a
    synchronization primitive.  Adapters must invoke mutating methods while holding the same
    mutation boundary that protects their concrete attachment state so phase changes and native
    resource ownership remain atomic from competing callers' perspective.
    """

    __slots__ = ("_phase",)

    def __init__(self) -> None:
        self._phase = AdbServerBackendPhase.IDLE

    @property
    def phase(self) -> AdbServerBackendPhase:
        """Current backend attachment lifecycle phase."""

        return self._phase

    def begin_acquire(self) -> None:
        """Admit an acquire request and enter ``ACQUIRING``."""

        phase = self._phase
        self._require_determinate(phase)
        if phase is AdbServerBackendPhase.IDLE:
            self._phase = AdbServerBackendPhase.ACQUIRING
            return
        if phase is AdbServerBackendPhase.RELEASING:
            raise AdbServerStopInProgressError(
                "the previous ADB server backend attachment is still releasing"
            )
        if phase in (AdbServerBackendPhase.ACQUIRING, AdbServerBackendPhase.ACTIVE):
            raise AdbServerNativeLifetimeBusyError(
                "an ADB server backend attachment still occupies this backend slot"
            )
        raise RuntimeError(f"undefined ADB server backend acquire phase: {phase.name}")

    def complete_acquire(self) -> None:
        """Complete a successful acquire and enter ``ACTIVE``."""

        if self._phase is not AdbServerBackendPhase.ACQUIRING:
            raise RuntimeError(
                f"cannot complete backend acquire from phase {self._phase.name}"
            )
        self._phase = AdbServerBackendPhase.ACTIVE

    def abort_acquire(self, *, native_termination_proven: bool) -> None:
        """Abort a failed acquire after native-resource cleanup has been attempted."""

        if self._phase is not AdbServerBackendPhase.ACQUIRING:
            raise RuntimeError(f"cannot abort backend acquire from phase {self._phase.name}")
        if not isinstance(native_termination_proven, bool):
            raise TypeError("native_termination_proven must be bool")
        self._phase = (
            AdbServerBackendPhase.IDLE
            if native_termination_proven
            else AdbServerBackendPhase.INDETERMINATE
        )

    def begin_release(self) -> None:
        """Admit a release request and enter ``RELEASING``."""

        phase = self._phase
        self._require_determinate(phase)
        if phase is AdbServerBackendPhase.ACTIVE:
            self._phase = AdbServerBackendPhase.RELEASING
            return
        if phase is AdbServerBackendPhase.IDLE:
            raise AdbServerNoAttachmentError("no ADB server backend attachment is owned")
        if phase is AdbServerBackendPhase.ACQUIRING:
            raise AdbServerAcquireInProgressError(
                "the requested ADB server backend attachment is still acquiring"
            )
        if phase is AdbServerBackendPhase.RELEASING:
            raise AdbServerStopInProgressError(
                "the requested ADB server backend attachment is already releasing"
            )
        raise RuntimeError(f"undefined ADB server backend release phase: {phase.name}")

    def complete_release(self) -> None:
        """Complete a successful release and return to ``IDLE``."""

        if self._phase is not AdbServerBackendPhase.RELEASING:
            raise RuntimeError(
                f"cannot complete backend release from phase {self._phase.name}"
            )
        self._phase = AdbServerBackendPhase.IDLE

    def fail_release_unproven(self) -> None:
        """Poison the backend when release cannot prove native termination."""

        if self._phase is not AdbServerBackendPhase.RELEASING:
            raise RuntimeError(
                f"cannot fail backend release from phase {self._phase.name}"
            )
        self._phase = AdbServerBackendPhase.INDETERMINATE

    @staticmethod
    def _require_determinate(phase: AdbServerBackendPhase) -> None:
        if phase is AdbServerBackendPhase.INDETERMINATE:
            raise AdbServerNativeTerminationUnprovenError(
                "the previous ADB server native lifetime termination remains unproven"
            )


def require_backend_release_endpoint(
    owned: AdbServerEndpoint,
    requested: AdbServerEndpoint,
) -> None:
    """Reject release of an endpoint other than the exact backend-owned attachment."""

    if not isinstance(owned, AdbServerEndpoint):
        raise TypeError("owned must be AdbServerEndpoint")
    if not isinstance(requested, AdbServerEndpoint):
        raise TypeError("requested must be AdbServerEndpoint")
    if owned != requested:
        raise AdbServerAttachmentMismatchError(
            "requested endpoint does not identify the owned ADB server backend attachment"
        )


@runtime_checkable
class AdbServerBackend(Protocol):
    """Acquire and release one backend-scoped usable ADB server attachment.

    Implementations share the port-owned admission and transition semantics represented by
    :class:`AdbServerBackendLifecycle`.  Concrete resource ownership remains adapter-defined.
    Releasing an attachment relinquishes those backend resources; it does not imply that every
    backend must terminate an underlying ADB server process.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        ...

    def release(self, endpoint: AdbServerEndpoint) -> None:
        ...


__all__ = [
    "AdbServerBackend",
    "AdbServerBackendLifecycle",
    "AdbServerBackendPhase",
    "require_backend_release_endpoint",
]
