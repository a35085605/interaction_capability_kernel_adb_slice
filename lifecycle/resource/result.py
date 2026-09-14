from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from lifecycle.resource.driver import PhysicalResources


PhysicalResourceT = TypeVar("PhysicalResourceT")


@dataclass(frozen=True, slots=True)
class ResourceAcquireSucceeded(Generic[PhysicalResourceT]):
    """Report that all requirements were acquired successfully."""

    resources: PhysicalResources[PhysicalResourceT]


@dataclass(frozen=True, slots=True)
class ResourceAcquireFailed(Generic[PhysicalResourceT]):
    """Report acquisition failure while retaining all reported resources.

    The caller is responsible for passing ``resources`` to release when lifecycle
    cleanup is required. ``error`` may be a non-``Exception`` ``BaseException`` only
    as an ownership-preserving transport for a control-flow interruption; the lifecycle
    coordinator records the retained resources before re-raising that interruption.
    """

    error: BaseException
    resources: PhysicalResources[PhysicalResourceT]


ResourceAcquireResult: TypeAlias = (
    ResourceAcquireSucceeded[PhysicalResourceT]
    | ResourceAcquireFailed[PhysicalResourceT]
)


__all__ = [
    "ResourceAcquireFailed",
    "ResourceAcquireResult",
    "ResourceAcquireSucceeded",
]
