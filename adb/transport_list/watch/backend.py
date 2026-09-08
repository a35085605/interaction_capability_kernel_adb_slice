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
    """Acquisition could not begin because acquisition or cleanup work is active."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireFailed:
    """Expected failure to establish a usable watch acquisition."""

    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireRevoked:
    """Evidence that the captured watch generation was revoked during acquisition."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


AdbTransportListWatchBackendAcquireResult: TypeAlias = (
    AdbTransportListWatchBackendAcquired
    | AdbTransportListWatchBackendAlreadyAcquired
    | AdbTransportListWatchBackendAcquireDeferred
    | AdbTransportListWatchBackendAcquireFailed
    | AdbTransportListWatchBackendAcquireRevoked
)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendPendingAcquireReleased:
    """Evidence that matching pending watch acquisition authority was released."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendReleased:
    """Evidence that a matching committed watch generation was logically released."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


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
    | AdbTransportListWatchBackendPendingAcquireReleased
    | AdbTransportListWatchBackendReleaseInactive
    | AdbTransportListWatchBackendReleaseMismatch
)


@runtime_checkable
class AdbTransportListWatchBackend(AdbTransportListWatchStateView, Protocol):
    """Sole authority for one runtime-scoped transport-list watch generation.

    ``read()`` returns the canonical atomic state snapshot. Generation fences stale lifecycle
    work and advances when matching pending or usable authority is logically released. Physical
    watch resources, cleanup ownership, and producer data-plane plumbing remain implementation
    details and are not part of lifecycle results.
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
    "AdbTransportListWatchBackendFactory",
    "AdbTransportListWatchBackendPendingAcquireReleased",
    "AdbTransportListWatchBackendReleased",
    "AdbTransportListWatchBackendReleaseInactive",
    "AdbTransportListWatchBackendReleaseMismatch",
    "AdbTransportListWatchBackendReleaseResult",
]
