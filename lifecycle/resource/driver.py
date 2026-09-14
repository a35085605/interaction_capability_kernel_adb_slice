from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

RequirementT = TypeVar("RequirementT", contravariant=True)
PhysicalResourceT = TypeVar("PhysicalResourceT")


type PhysicalResources[T] = tuple[T, ...]


@dataclass(frozen=True, slots=True)
class RequirementAcquireSucceeded(Generic[PhysicalResourceT]):
    """Report successful acquisition for one physical-resource requirement."""

    resources: PhysicalResources[PhysicalResourceT]


@dataclass(frozen=True, slots=True)
class RequirementAcquireFailed(Generic[PhysicalResourceT]):
    """Report terminal acquisition failure for one requirement.

    ``resources`` contains every physical resource created for the requirement before
    the failure. The caller retains those resources for a later cleanup attempt.
    """

    error: Exception
    resources: PhysicalResources[PhysicalResourceT]


@dataclass(frozen=True, slots=True)
class RequirementAcquireInterrupted(Generic[PhysicalResourceT]):
    """Report a control-flow interruption after physical resources were created.

    This result exists only to preserve cleanup ownership while a non-``Exception``
    ``BaseException`` such as ``KeyboardInterrupt`` or ``SystemExit`` propagates
    through the lifecycle. It is not a normal acquisition-failure result.
    """

    error: BaseException
    resources: PhysicalResources[PhysicalResourceT]


type RequirementAcquireResult[T] = (
    RequirementAcquireSucceeded[T]
    | RequirementAcquireFailed[T]
    | RequirementAcquireInterrupted[T]
)


class ResourceDriver(Protocol[RequirementT, PhysicalResourceT]):
    """Perform synchronous physical I/O for individual requirements.

    ``acquire`` handles one requirement and reports every resource created before its
    terminal result; it does not roll back resources on failure or interruption. A
    non-``Exception`` control-flow interruption that occurs after resource creation
    must be returned as ``RequirementAcquireInterrupted`` so lifecycle ownership can
    be recorded before the interruption is re-raised. ``cleanup`` may be retried with
    the same resources after raising and must tolerate members that were already
    cleaned up by an earlier attempt.
    """

    def acquire(
        self,
        requirement: RequirementT,
    ) -> RequirementAcquireResult[PhysicalResourceT]: ...

    def cleanup(self, resources: PhysicalResources[PhysicalResourceT]) -> None: ...


__all__ = [
    "RequirementAcquireFailed",
    "RequirementAcquireInterrupted",
    "RequirementAcquireResult",
    "RequirementAcquireSucceeded",
    "PhysicalResources",
    "ResourceDriver",
]
