from __future__ import annotations

from typing import Protocol, TypeVar


AccessT = TypeVar("AccessT", contravariant=True)
ResourceSetT = TypeVar("ResourceSetT")


class AccessResourceLifecycle(Protocol[AccessT, ResourceSetT]):
    """Physical ResourceSet lifecycle driven directly by Access.

    Coordination concerns such as conflict detection, coexistence policy,
    reservations, and revocation intentionally live in ``adb._managed``.
    """

    def acquire(self, access: AccessT) -> ResourceSetT: ...

    def cleanup(self, resources: ResourceSetT) -> None: ...


__all__ = ["AccessResourceLifecycle"]
