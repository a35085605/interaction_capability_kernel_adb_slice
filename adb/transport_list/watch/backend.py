from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import AdbTransportListWatchStateView


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendCleanupHandoff:
    """Transfer unresolved watch physical-cleanup ownership out of the backend.

    ``handle`` is no longer backend-owned. The receiver becomes responsible for either
    confirming physical cleanup or transferring ownership again to a more capable authority.
    """

    handle: object
    diagnostic: str

    def __post_init__(self) -> None:
        if self.handle is None:
            raise TypeError("handle cannot be None")
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


class AdbTransportListWatchBackendCleanupHandoffError(RuntimeError):
    """Primary exceptional outcome accompanied by unresolved watch cleanup ownership."""

    def __init__(
        self,
        primary_error: BaseException,
        cleanup_handoff: AdbTransportListWatchBackendCleanupHandoff,
    ) -> None:
        if not isinstance(primary_error, BaseException):
            raise TypeError("primary_error must be BaseException")
        if not isinstance(
            cleanup_handoff, AdbTransportListWatchBackendCleanupHandoff
        ):
            raise TypeError(
                "cleanup_handoff must be AdbTransportListWatchBackendCleanupHandoff"
            )
        self.primary_error = primary_error
        self.cleanup_handoff = cleanup_handoff
        super().__init__(
            f"{primary_error}; unresolved ADB watch cleanup ownership was handed off: "
            f"{cleanup_handoff.diagnostic}"
        )


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquired:
    """One runtime-scoped usable transport-list watch retained by the backend.

    This is lifecycle evidence only. The backend retains the physical watch resource; producer
    access to transport-list data is provided through a separate watch-stream capability.
    """

    endpoint: TcpAddress
    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAlreadyAcquired:
    """Evidence that the backend already retains a usable watch acquisition."""

    acquisition: AdbTransportListWatchBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbTransportListWatchBackendAcquired):
            raise TypeError("acquisition must be AdbTransportListWatchBackendAcquired")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireDeferred:
    """Acquisition could not begin because another backend operation is active."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireFailed:
    """Expected failure to establish a usable watch acquisition."""

    failure: AdbTransportListWatchFailure
    cleanup_handoff: AdbTransportListWatchBackendCleanupHandoff | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        if self.cleanup_handoff is not None and not isinstance(
            self.cleanup_handoff, AdbTransportListWatchBackendCleanupHandoff
        ):
            raise TypeError(
                "cleanup_handoff must be AdbTransportListWatchBackendCleanupHandoff or None"
            )


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireRevoked:
    """Evidence that the captured watch generation was revoked during acquisition."""

    generation: AdbTransportListWatchGeneration
    cleanup_handoff: AdbTransportListWatchBackendCleanupHandoff | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.cleanup_handoff is not None and not isinstance(
            self.cleanup_handoff, AdbTransportListWatchBackendCleanupHandoff
        ):
            raise TypeError(
                "cleanup_handoff must be AdbTransportListWatchBackendCleanupHandoff or None"
            )


AdbTransportListWatchBackendAcquireResult: TypeAlias = (
    AdbTransportListWatchBackendAcquired
    | AdbTransportListWatchBackendAlreadyAcquired
    | AdbTransportListWatchBackendAcquireDeferred
    | AdbTransportListWatchBackendAcquireFailed
    | AdbTransportListWatchBackendAcquireRevoked
)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendReleased:
    """Evidence that matching watch authority was logically released.

    ``generation`` is the revoked generation. ``acquisition`` is present only when
    that generation had committed a usable watch before release. ``cleanup_handoff`` is present
    only when normal physical retirement could not be confirmed and unresolved ownership was
    transferred to the caller.
    """

    generation: AdbTransportListWatchGeneration
    acquisition: AdbTransportListWatchBackendAcquired | None = None
    cleanup_handoff: AdbTransportListWatchBackendCleanupHandoff | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.acquisition is not None:
            if not isinstance(self.acquisition, AdbTransportListWatchBackendAcquired):
                raise TypeError(
                    "acquisition must be AdbTransportListWatchBackendAcquired or None"
                )
            if self.acquisition.generation != self.generation:
                raise ValueError("acquisition generation must match released generation")
        if self.cleanup_handoff is not None and not isinstance(
            self.cleanup_handoff, AdbTransportListWatchBackendCleanupHandoff
        ):
            raise TypeError(
                "cleanup_handoff must be AdbTransportListWatchBackendCleanupHandoff or None"
            )


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendReleaseInactive:
    """Evidence that the matching current generation has no watch authority to release."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendReleaseMismatch:
    """Evidence that release did not match the backend's current watch generation."""

    current: AdbTransportListWatchBackendAcquired | None
    current_generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(
            self.current, AdbTransportListWatchBackendAcquired
        ):
            raise TypeError("current must be AdbTransportListWatchBackendAcquired or None")
        if not isinstance(self.current_generation, AdbTransportListWatchGeneration):
            raise TypeError("current_generation must be AdbTransportListWatchGeneration")
        if self.current is not None and self.current.generation != self.current_generation:
            raise ValueError("current_generation must match current acquisition generation")


AdbTransportListWatchBackendReleaseResult: TypeAlias = (
    AdbTransportListWatchBackendReleased
    | AdbTransportListWatchBackendReleaseInactive
    | AdbTransportListWatchBackendReleaseMismatch
)


@runtime_checkable
class AdbTransportListWatchBackend(AdbTransportListWatchStateView, Protocol):
    """Sole authority for one runtime-scoped transport-list watch generation.

    ``read()`` returns the canonical atomic state snapshot. Generation fences stale lifecycle
    work and advances when matching pending or usable authority is logically released. Physical
    watch resources and producer data-plane plumbing remain backend implementation details.

    Every physical resource obtained by the backend follows confirmed-or-handoff cleanup:
    ownership is retained until cleanup is confirmed, or transferred through a result/exception
    carrying ``AdbTransportListWatchBackendCleanupHandoff``.
    """

    def acquire(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchBackendAcquireResult:
        """Acquire one fully usable watch within the current generation."""
        ...

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchBackendReleaseResult:
        """Release matching authority and advance generation at logical revocation."""
        ...


class AdbTransportListWatchBackendFactory(Protocol):
    """Construct one runtime-scoped transport-list watch backend."""

    def __call__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
    ) -> AdbTransportListWatchBackend:
        ...


__all__ = [
    "AdbTransportListWatchBackend",
    "AdbTransportListWatchBackendAcquired",
    "AdbTransportListWatchBackendAcquireDeferred",
    "AdbTransportListWatchBackendAcquireFailed",
    "AdbTransportListWatchBackendAcquireRevoked",
    "AdbTransportListWatchBackendAcquireResult",
    "AdbTransportListWatchBackendAlreadyAcquired",
    "AdbTransportListWatchBackendCleanupHandoff",
    "AdbTransportListWatchBackendCleanupHandoffError",
    "AdbTransportListWatchBackendFactory",
    "AdbTransportListWatchBackendReleased",
    "AdbTransportListWatchBackendReleaseInactive",
    "AdbTransportListWatchBackendReleaseMismatch",
    "AdbTransportListWatchBackendReleaseResult",
]
