from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerStateView


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerBackendCleanupHandoff:
    """Transfer unresolved physical cleanup ownership out of the backend.

    ``handle`` is no longer backend-owned. The receiver becomes responsible for either
    confirming physical cleanup or transferring ownership again to a more capable authority.
    """

    handle: object
    diagnostic: str

    def __post_init__(self) -> None:
        if self.handle is None:
            raise TypeError("handle cannot be None")
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


# Backwards-compatible name retained for callers that consumed the earlier release-only signal.
AdbServerBackendReleaseCleanupUnconfirmed = AdbServerBackendCleanupHandoff


class AdbServerBackendCleanupHandoffError(RuntimeError):
    """Primary exceptional outcome accompanied by unresolved cleanup ownership.

    The caller must accept ``cleanup_handoff`` even though ``primary_error`` remains the
    authoritative reason the lifecycle operation failed exceptionally.
    """

    def __init__(
        self,
        primary_error: BaseException,
        cleanup_handoff: AdbServerBackendCleanupHandoff,
    ) -> None:
        if not isinstance(primary_error, BaseException):
            raise TypeError("primary_error must be BaseException")
        if not isinstance(cleanup_handoff, AdbServerBackendCleanupHandoff):
            raise TypeError("cleanup_handoff must be AdbServerBackendCleanupHandoff")
        self.primary_error = primary_error
        self.cleanup_handoff = cleanup_handoff
        super().__init__(
            f"{primary_error}; unresolved ADB server cleanup ownership was handed off: "
            f"{cleanup_handoff.diagnostic}"
        )


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquired:
    """One runtime-scoped usable ADB server acquisition retained by the backend."""

    endpoint: AdbServerEndpoint
    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAlreadyAcquired:
    """Evidence that the backend already retains this usable server acquisition."""

    acquisition: AdbServerBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquired):
            raise TypeError("acquisition must be AdbServerBackendAcquired")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireDeferred:
    """Backend acquisition could not begin because another backend operation is active."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireFailed:
    """Backend acquisition failed to satisfy the request.

    ``cleanup_handoff`` is present only when acquisition created a physical resource whose
    cleanup could not be confirmed. Ownership of that resource has left the backend.
    """

    diagnostic: str
    cleanup_handoff: AdbServerBackendCleanupHandoff | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))
        if self.cleanup_handoff is not None and not isinstance(
            self.cleanup_handoff, AdbServerBackendCleanupHandoff
        ):
            raise TypeError(
                "cleanup_handoff must be AdbServerBackendCleanupHandoff or None"
            )


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireRevoked:
    """Evidence that the captured server generation was revoked during acquisition."""

    generation: AdbServerGeneration
    cleanup_handoff: AdbServerBackendCleanupHandoff | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if self.cleanup_handoff is not None and not isinstance(
            self.cleanup_handoff, AdbServerBackendCleanupHandoff
        ):
            raise TypeError(
                "cleanup_handoff must be AdbServerBackendCleanupHandoff or None"
            )


AdbServerBackendAcquireResult: TypeAlias = (
    AdbServerBackendAcquired
    | AdbServerBackendAlreadyAcquired
    | AdbServerBackendAcquireDeferred
    | AdbServerBackendAcquireFailed
    | AdbServerBackendAcquireRevoked
)


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleased:
    """Evidence that matching backend authority was released.

    ``generation`` is the generation that was revoked. ``acquisition`` is present only when
    that generation had committed a usable endpoint before release. ``cleanup_handoff`` is
    present only when normal physical cleanup could not be confirmed and ownership of the
    unresolved resource was transferred to the caller.
    """

    generation: AdbServerGeneration
    acquisition: AdbServerBackendAcquired | None = None
    cleanup_handoff: AdbServerBackendCleanupHandoff | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if self.acquisition is not None:
            if not isinstance(self.acquisition, AdbServerBackendAcquired):
                raise TypeError("acquisition must be AdbServerBackendAcquired or None")
            if self.acquisition.generation != self.generation:
                raise ValueError("acquisition generation must match released generation")
        if self.cleanup_handoff is not None and not isinstance(
            self.cleanup_handoff, AdbServerBackendCleanupHandoff
        ):
            raise TypeError(
                "cleanup_handoff must be AdbServerBackendCleanupHandoff or None"
            )


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseInactive:
    """Evidence that the matching current generation has no authority to release."""

    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseMismatch:
    """Evidence that release did not match the backend's current server generation."""

    current: AdbServerBackendAcquired | None
    current_generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(self.current, AdbServerBackendAcquired):
            raise TypeError("current must be AdbServerBackendAcquired or None")
        if not isinstance(self.current_generation, AdbServerGeneration):
            raise TypeError("current_generation must be AdbServerGeneration")
        if self.current is not None and self.current.generation != self.current_generation:
            raise ValueError("current_generation must match current acquisition generation")


AdbServerBackendReleaseResult: TypeAlias = (
    AdbServerBackendReleased
    | AdbServerBackendReleaseInactive
    | AdbServerBackendReleaseMismatch
)


@runtime_checkable
class AdbServerBackend(AdbServerStateView, Protocol):
    """Sole authority for one runtime-scoped ADB server generation.

    ``read()`` returns the canonical atomic state snapshot. Generation fences stale lifecycle
    work and advances when matching pending or usable authority is logically released. All views
    and ownership transitions are concurrency-safe and linearizable.

    Every physical resource obtained by the backend follows confirmed-or-handoff cleanup:
    ownership is retained until cleanup is confirmed, or transferred through a result/exception
    carrying ``AdbServerBackendCleanupHandoff``.
    """

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        """Acquire usable ADB server access within the current generation.

        A successful constrained acquisition must expose exactly ``endpoint_constraint``.
        """
        ...

    def release(self, expected: AdbServerGeneration) -> AdbServerBackendReleaseResult:
        """Release matching authority and advance the generation at logical revocation."""
        ...


class AdbServerBackendFactory(Protocol):
    """Construct one runtime-scoped ADB server backend."""

    def __call__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
    ) -> AdbServerBackend:
        """Construct a backend using the runtime-scoped server generation issuer."""
        ...


__all__ = [
    "AdbServerBackend",
    "AdbServerBackendAcquired",
    "AdbServerBackendAcquireDeferred",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireRevoked",
    "AdbServerBackendAlreadyAcquired",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendCleanupHandoff",
    "AdbServerBackendCleanupHandoffError",
    "AdbServerBackendFactory",
    "AdbServerBackendReleased",
    "AdbServerBackendReleaseCleanupUnconfirmed",
    "AdbServerBackendReleaseInactive",
    "AdbServerBackendReleaseMismatch",
    "AdbServerBackendReleaseResult",
]
