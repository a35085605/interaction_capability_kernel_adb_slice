from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Hashable


class ResourcePolicy(Enum):
    """Same-Access coexistence policy for one managed resource identity."""

    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    PARALLEL = "parallel"


@dataclass(frozen=True, slots=True)
class ResourceRequirement:
    """Describe coordinator-level conflict and reuse semantics for one Access.

    ``key`` identifies the physical resource contract for SHARED reuse, policy
    consistency, and cleanup ownership. Keys must include every material
    parameter that makes two ResourceSets unsafe to reuse.

    Policy semantics for the same Access are:

    * EXCLUSIVE: coexist with nothing else.
    * SHARED: reuse one active ResourceSet with the same key.
    * PARALLEL: allow independent ResourceSets, including with the same key.
    """

    key: Hashable
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
