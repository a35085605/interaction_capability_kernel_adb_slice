from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.session import AdbTransportListWatchSession
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

    ``session`` is exposed for producer execution, but that exposure does not transfer
    physical lifetime ownership. Matching backend release may cancel it concurrently after
    generation revocation.
    """

    endpoint: TcpAddress
    generation: AdbTransportListWatchGeneration
    session: AdbTransportListWatchSession

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if not isinstance(self.session, AdbTransportListWatchSession):
            raise TypeError("session must satisfy AdbTransportListWatchSession")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAlreadyAcquired:
    """Evidence that the backend already retains a usable watch acquisition."""

    acquisition: AdbTransportListWatchBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbTransportListWatchBackendAcquired):
            raise TypeError("acquisition must be AdbTransportListWatchBackendAcquired")

    @property
    def session(self) -> AdbTransportListWatchSession:
        """Compatibility access to the retained producer-use session."""

        return self.acquisition.session


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
class AdbTransportListWatchBackendReleased:
    """Evidence that matching watch authority was logically released.

    ``generation`` is the revoked generation. ``acquisition`` is present only when
    that generation had committed a usable watch before release. Physical cleanup may
    continue after the generation has advanced.
    """

    generation: AdbTransportListWatchGeneration
    acquisition: AdbTransportListWatchBackendAcquired | None = None

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

    ``generation`` is the only lifecycle and producer fence. Resource sessions carry no
    identity or authority of their own and remain lifetime-owned by the backend even while a
    producer consumes them. ``run_if_current()`` linearizes projection mutation against release
    so stale generations cannot commit after logical revocation.
    """

    def acquire(
        self,
        endpoint: TcpAddress,
        *,
        startup_timeout_seconds: float = 5.0,
    ) -> AdbTransportListWatchBackendAcquireResult:
        """Acquire one fully usable watch within the current generation."""
        ...

    def run_if_current(
        self,
        expected: AdbTransportListWatchGeneration,
        operation: Callable[[], None],
    ) -> bool:
        """Run ``operation`` while matching usable authority remains current."""
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
    "AdbTransportListWatchBackendReleased",
    "AdbTransportListWatchBackendReleaseInactive",
    "AdbTransportListWatchBackendReleaseMismatch",
    "AdbTransportListWatchBackendReleaseResult",
]
