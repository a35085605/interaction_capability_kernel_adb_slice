from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from adb.transport_list.session_identity import (
    AdbTransportListSessionIdentity,
    AdbTransportListSessionIdentityIssuer,
)
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
    """One runtime-scoped usable transport-list watch retained by the backend."""

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

    @property
    def identity(self) -> AdbTransportListSessionIdentity:
        """Compatibility alias for the acquired producer session identity."""

        return self.session.session_identity

    @property
    def session_identity(self) -> AdbTransportListSessionIdentity:
        return self.session.session_identity


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAlreadyAcquired:
    """Evidence that the backend already retains a usable watch acquisition."""

    acquisition: AdbTransportListWatchBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbTransportListWatchBackendAcquired):
            raise TypeError("acquisition must be AdbTransportListWatchBackendAcquired")

    @property
    def session(self) -> AdbTransportListWatchSession:
        """Compatibility access to the retained session."""

        return self.acquisition.session

    @property
    def identity(self) -> AdbTransportListSessionIdentity:
        return self.acquisition.session_identity


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

    ``read()`` returns the canonical atomic lifecycle snapshot. ``generation`` fences
    stale lifecycle work and advances when matching pending or usable authority is
    logically released. The acquired session owns only producer resources; transport-list
    projection authority remains a separate domain.

    Session reads are single-consumer. Implementations may surface expected connection,
    service, or protocol failures while reading updates as ``AdbTransportListWatchError``.
    """

    def acquire(
        self,
        endpoint: TcpAddress,
        *,
        startup_timeout_seconds: float = 5.0,
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
    """Construct one runtime-scoped transport-list watch backend.

    ``generation_issuer`` remains optional for compatibility while runtime orchestration
    migrates; new code should provide the externally owned runtime-scoped issuer.
    """

    def __call__(
        self,
        identity_issuer: AdbTransportListSessionIdentityIssuer,
        *,
        generation_issuer: AdbTransportListWatchGenerationIssuer | None = None,
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
