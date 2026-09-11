from __future__ import annotations

from typing import Hashable, Protocol, TypeVar


AccessT = TypeVar("AccessT", contravariant=True)
ResourceSetT = TypeVar("ResourceSetT")


class ResourceAcquisitionRequest(Protocol[ResourceSetT]):
    """Managed-owned view of one physical acquisition request.

    ``interrupted`` is independent from coordinator authority. It means the
    physical request has been asked to stop. While ``acquire`` is still running,
    late resources remain owned by the same request and may still be published.

    ``publish`` records a point-in-time ResourceSet snapshot. Callers must not
    mutate a published snapshot in-place; publish a new snapshot when the
    ResourceSet changes so Pool state changes remain synchronized.
    """

    @property
    def request_id(self) -> Hashable: ...

    @property
    def interrupted(self) -> bool: ...

    def publish(self, resources: ResourceSetT) -> None: ...


class AccessResourceLifecycle(Protocol[AccessT, ResourceSetT]):
    """Physical ResourceSet lifecycle driven directly by Access.

    Coordination concerns such as conflict detection, coexistence policy,
    request identity, authority revocation, and final cleanup ownership live in
    ``adb._managed``. Implementations should publish partial ResourceSet snapshots
    while blocking I/O is in progress so an interrupted request never loses
    ownership of resources that were already created.

    ``interrupt`` is best-effort physical I/O interruption. It is distinct from
    Managed authority revocation and may be a no-op when the backend cannot abort
    an in-flight operation. The acquire call must still eventually return or
    raise before final cleanup can run safely.
    """

    def acquire(
        self,
        access: AccessT,
        request: ResourceAcquisitionRequest[ResourceSetT],
    ) -> ResourceSetT: ...

    def interrupt(
        self,
        access: AccessT,
        request: ResourceAcquisitionRequest[ResourceSetT],
    ) -> None: ...

    def cleanup(self, resources: ResourceSetT) -> None: ...


__all__ = ["AccessResourceLifecycle", "ResourceAcquisitionRequest"]
