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

    ``resources`` contains every physical resource that remains owned and still
    requires cleanup when ``acquire`` returns. Temporary resources that the driver
    has synchronously and conclusively cleaned up need not be reported.
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

    ``acquire`` handles one requirement and reports every resource whose ownership is
    transferred to the lifecycle. A driver may synchronously discard temporary
    resources before returning, but any resource whose cleanup cannot be confirmed
    must be reported in the terminal result. A non-``Exception`` control-flow
    interruption that leaves retained resources must be returned as
    ``RequirementAcquireInterrupted`` so lifecycle ownership can be recorded before
    the interruption is re-raised. ``cleanup`` may be retried with the same resources
    after raising and must tolerate members that were already cleaned up by an earlier
    attempt.
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
