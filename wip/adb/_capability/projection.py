from __future__ import annotations

from typing import Protocol, TypeVar


AccessT = TypeVar("AccessT", contravariant=True)
ResourceSetT = TypeVar("ResourceSetT", contravariant=True)
CapabilityT = TypeVar("CapabilityT", covariant=True)


class CapabilityProjection(Protocol[AccessT, ResourceSetT, CapabilityT]):
    """Pure, stateless projection from Access + ResourceSet to Capability."""

    def project(
        self,
        access: AccessT,
        resources: ResourceSetT,
    ) -> CapabilityT: ...


__all__ = ["CapabilityProjection"]
