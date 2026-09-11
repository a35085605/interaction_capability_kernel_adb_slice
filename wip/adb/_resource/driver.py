from __future__ import annotations

from typing import Protocol, TypeVar


SpecT = TypeVar("SpecT", contravariant=True)
ResourceT = TypeVar("ResourceT")


type ResourceSet[T] = tuple[T, ...]


class AcquisitionContext(Protocol[ResourceT]):
    """Narrow physical-acquisition context owned by resource management.

    ``interrupted`` is a best-effort stop signal for the physical producer. It is
    independent from any upper-layer authority decision. A producer that cannot
    stop immediately may continue publishing immutable ResourceSet snapshots until
    its acquire call returns or raises.
    """

    @property
    def interrupted(self) -> bool: ...

    def publish(self, resources: ResourceSet[ResourceT]) -> None:
        """Publish the current immutable ResourceSet snapshot."""
        ...


class ResourceDriver(Protocol[SpecT, ResourceT]):
    """Physical resource I/O driven only by a lower-layer resource specification.

    Drivers do not know about managed Access, generations, authority, conflict
    policy, pool request identity, leases, or capability projection. Resource
    management owns those concerns and supplies only an acquisition context with
    interruption and snapshot publication.
    """

    def acquire(
        self,
        spec: SpecT,
        context: AcquisitionContext[ResourceT],
    ) -> ResourceSet[ResourceT]: ...

    def interrupt(
        self,
        spec: SpecT,
        context: AcquisitionContext[ResourceT],
    ) -> None: ...

    def cleanup(self, resources: ResourceSet[ResourceT]) -> None: ...


__all__ = ["AcquisitionContext", "ResourceDriver", "ResourceSet"]
