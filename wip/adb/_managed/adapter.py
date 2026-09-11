from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from adb._capability.projection import CapabilityProjection
from adb._resource.lifecycle import ResourceLifecycle


AccessT = TypeVar("AccessT")
RequirementsT = TypeVar("RequirementsT")
ResourceSetT = TypeVar("ResourceSetT")
CapabilityT = TypeVar("CapabilityT")


class AccessModel(Protocol[AccessT, RequirementsT]):
    """Adapter semantics for deriving physical requirements from Access."""

    def requirements(self, access: AccessT) -> RequirementsT: ...


@dataclass(frozen=True, slots=True)
class Adapter(Generic[AccessT, RequirementsT, ResourceSetT, CapabilityT]):
    access_model: AccessModel[AccessT, RequirementsT]
    resource_lifecycle: ResourceLifecycle[RequirementsT, ResourceSetT]
    capability_projection: CapabilityProjection[AccessT, ResourceSetT, CapabilityT]


__all__ = ["AccessModel", "Adapter"]
