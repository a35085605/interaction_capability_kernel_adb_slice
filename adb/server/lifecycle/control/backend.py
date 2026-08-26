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


class AdbServerBackendRequest(Enum):
    """Lifecycle command accepted by :class:`AdbServerBackend`."""

    ACQUIRE = auto()
    RELEASE = auto()


def begin_backend_request(
    phase: AdbServerBackendPhase,
    request: AdbServerBackendRequest,
) -> AdbServerBackendPhase:
    """Apply the port-owned phase/request matrix and return the entered phase.

    This function is the authoritative lifecycle admission policy for every backend adapter.
    Adapters must call it while holding their mutation boundary instead of defining private
    phase-specific failures.  Rejected requests fail synchronously with control-domain errors.
    """

    if not isinstance(phase, AdbServerBackendPhase):
        raise TypeError("phase must be AdbServerBackendPhase")
    if not isinstance(request, AdbServerBackendRequest):
        raise TypeError("request must be AdbServerBackendRequest")

    if phase is AdbServerBackendPhase.INDETERMINATE:
        raise AdbServerNativeTerminationUnprovenError(
            "the previous ADB server native lifetime termination remains unproven"
        )

    if request is AdbServerBackendRequest.ACQUIRE:
        if phase is AdbServerBackendPhase.IDLE:
            return AdbServerBackendPhase.ACQUIRING
        if phase is AdbServerBackendPhase.RELEASING:
            raise AdbServerStopInProgressError(
                "the previous ADB server backend attachment is still releasing"
            )
        if phase in (AdbServerBackendPhase.ACQUIRING, AdbServerBackendPhase.ACTIVE):
            raise AdbServerNativeLifetimeBusyError(
                "an ADB server backend attachment still occupies this backend slot"
            )

    if request is AdbServerBackendRequest.RELEASE:
        if phase is AdbServerBackendPhase.ACTIVE:
            return AdbServerBackendPhase.RELEASING
        if phase is AdbServerBackendPhase.IDLE:
            raise AdbServerNoAttachmentError(
                "no ADB server backend attachment is owned"
            )
        if phase is AdbServerBackendPhase.ACQUIRING:
            raise AdbServerAcquireInProgressError(
                "the requested ADB server backend attachment is still acquiring"
            )
        if phase is AdbServerBackendPhase.RELEASING:
            raise AdbServerStopInProgressError(
                "the requested ADB server backend attachment is already releasing"
            )

    raise RuntimeError(
        f"undefined ADB server backend lifecycle request: phase={phase.name}, request={request.name}"
    )


def complete_backend_request(
    phase: AdbServerBackendPhase,
    request: AdbServerBackendRequest,
) -> AdbServerBackendPhase:
    """Return the port-defined phase after a successfully completed lifecycle command."""

    if request is AdbServerBackendRequest.ACQUIRE and phase is AdbServerBackendPhase.ACQUIRING:
        return AdbServerBackendPhase.ACTIVE
    if request is AdbServerBackendRequest.RELEASE and phase is AdbServerBackendPhase.RELEASING:
        return AdbServerBackendPhase.IDLE
    raise RuntimeError(
        f"invalid ADB server backend completion: phase={phase.name}, request={request.name}"
    )


def abort_backend_acquire(
    phase: AdbServerBackendPhase,
    *,
    native_termination_proven: bool,
) -> AdbServerBackendPhase:
    """Return the required phase after an acquire attempt fails and cleanup is attempted."""

    if phase is not AdbServerBackendPhase.ACQUIRING:
        raise RuntimeError(f"cannot abort backend acquire from phase {phase.name}")
    if not isinstance(native_termination_proven, bool):
        raise TypeError("native_termination_proven must be bool")
    return (
        AdbServerBackendPhase.IDLE
        if native_termination_proven
        else AdbServerBackendPhase.INDETERMINATE
    )


def fail_backend_release_unproven(
    phase: AdbServerBackendPhase,
) -> AdbServerBackendPhase:
    """Enter the terminal port state when release cannot prove native termination."""

    if phase is not AdbServerBackendPhase.RELEASING:
        raise RuntimeError(f"cannot fail backend release from phase {phase.name}")
    return AdbServerBackendPhase.INDETERMINATE


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

    All implementations share the lifecycle protocol defined by
    :class:`AdbServerBackendPhase`, :class:`AdbServerBackendRequest`, and the transition helpers in
    this module.  In particular, an inadmissible request must fail synchronously with the
    control-domain error prescribed by :func:`begin_backend_request`; adapters must not expose
    adapter-specific lifecycle failures to callers.

    Concrete resource ownership remains adapter-defined.  Releasing an attachment relinquishes
    those backend resources; it does not imply that every backend must terminate an underlying ADB
    server process.
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
    "AdbServerBackendPhase",
    "AdbServerBackendRequest",
    "abort_backend_acquire",
    "begin_backend_request",
    "complete_backend_request",
    "fail_backend_release_unproven",
    "require_backend_release_endpoint",
]
