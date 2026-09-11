from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Hashable, TypeVar


RequirementsT = TypeVar("RequirementsT")


class ResourcePolicy(Enum):
    """Same-Access coexistence policy for one physical ResourceSet requirement."""

    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    PARALLEL = "parallel"


@dataclass(frozen=True, slots=True)
class ResourceRequirement(Generic[RequirementsT]):
    """Describe one ResourceSet acquisition before physical resources are touched.

    ``key`` identifies the physical resource contract for SHARED reuse, policy
    consistency, and cleanup ownership. Keys must include every material parameter
    that makes two ResourceSets unsafe to reuse. ``value`` is passed unchanged to
    ``ResourceLifecycle``.

    Policy semantics for the same Access are:

    * EXCLUSIVE: coexist with nothing else.
    * SHARED: reuse one active ResourceSet with the same key.
    * PARALLEL: allow independent ResourceSets, including with the same key.
    """

    key: Hashable
    value: RequirementsT
    policy: ResourcePolicy = ResourcePolicy.EXCLUSIVE

    def __post_init__(self) -> None:
        if self.key is None:
            raise TypeError("resource requirement key cannot be None")
        try:
            hash(self.key)
        except TypeError as exc:
            raise TypeError("resource requirement key must be hashable") from exc
        if not isinstance(self.policy, ResourcePolicy):
            raise TypeError("resource requirement policy must be ResourcePolicy")


__all__ = ["ResourcePolicy", "ResourceRequirement"]
