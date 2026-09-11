from __future__ import annotations

from typing import Protocol, TypeVar


AccessT = TypeVar("AccessT", contravariant=True)
ResolvedResourcesT = TypeVar("ResolvedResourcesT", contravariant=True)
CapabilityT = TypeVar("CapabilityT", covariant=True)


class CapabilityProjection(Protocol[AccessT, ResolvedResourcesT, CapabilityT]):
    """Pure, stateless projection from Access + resolved resources to Capability."""

    def project(
        self,
        access: AccessT,
        resources: ResolvedResourcesT,
    ) -> CapabilityT: ...


__all__ = ["CapabilityProjection"]
