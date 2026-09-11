from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from adb._capability.projection import CapabilityProjection
from adb._managed.requirement import ResourceRequirement
from adb._resource.lifecycle import AccessResourceLifecycle


AccessT = TypeVar("AccessT")
ResourceT = TypeVar("ResourceT")
CapabilityT = TypeVar("CapabilityT")


class AccessModel(Protocol[AccessT]):
    """Coordinator policy for deriving conflict/reuse requirements from Access."""

    def requirements(self, access: AccessT) -> ResourceRequirement: ...


@dataclass(frozen=True, slots=True)
class Adapter(Generic[AccessT, ResourceT, CapabilityT]):
    access_model: AccessModel[AccessT]
    resource_lifecycle: AccessResourceLifecycle[AccessT, ResourceT]
    capability_projection: CapabilityProjection[AccessT, ResourceT, CapabilityT]


__all__ = ["AccessModel", "Adapter"]
