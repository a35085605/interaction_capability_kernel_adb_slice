from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from adb._capability.projection import CapabilityProjection
from adb._resource.lifecycle import ResourceLifecycle
from adb._resource.pool import ResourcePoolView
from adb._resource.result import MissingResources, Resolved


AccessT = TypeVar("AccessT")
RequirementsT = TypeVar("RequirementsT")
ResolvedResourcesT = TypeVar("ResolvedResourcesT")
CapabilityT = TypeVar("CapabilityT")


class AccessModel(Protocol[AccessT, RequirementsT, ResolvedResourcesT]):
    """Adapter semantics for Access -> requirements -> pool resolution."""

    def requirements(self, access: AccessT) -> RequirementsT: ...

    def resolve(
        self,
        access: AccessT,
        requirements: RequirementsT,
        available: ResourcePoolView,
    ) -> Resolved[ResolvedResourcesT] | MissingResources[RequirementsT]: ...


@dataclass(frozen=True, slots=True)
class Adapter(Generic[AccessT, RequirementsT, ResolvedResourcesT, CapabilityT]):
    access_model: AccessModel[AccessT, RequirementsT, ResolvedResourcesT]
    resource_lifecycle: ResourceLifecycle[RequirementsT]
    capability_projection: CapabilityProjection[
        AccessT,
        ResolvedResourcesT,
        CapabilityT,
    ]


__all__ = ["AccessModel", "Adapter"]
