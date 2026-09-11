from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Generic, TypeAlias, TypeVar

from adb._managed.pool import ResourceLease


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
ResourceSetT = TypeVar("ResourceSetT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(slots=True, eq=False)
class ManagedAttempt(Generic[GenerationT, AccessT]):
    """Coordinator-owned state for one in-flight Managed authority attempt.

    ``revoke`` removes commit authority and raises a coordinator-owned
    cancellation signal. ``AccessResourceLifecycle.acquire`` intentionally does
    not receive that signal; a physical acquire that cannot be interrupted is
    allowed to drain, after which the coordinator cleans up its ResourceSet.
    """

    generation: GenerationT
    access: AccessT
    _cancellation: Event = field(default_factory=Event, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _revoked: bool = field(default=False, init=False, repr=False)
    _finished: bool = field(default=False, init=False, repr=False)

    @property
    def cancellation(self) -> Event:
        return self._cancellation

    @property
    def revoked(self) -> bool:
        with self._lock:
            return self._revoked

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    def revoke(self) -> None:
        with self._lock:
            if self._revoked:
                return
            self._revoked = True
            self._cancellation.set()

    def finish(self) -> None:
        with self._lock:
            self._finished = True


@dataclass(frozen=True, slots=True)
class Idle(Generic[GenerationT]):
    generation: GenerationT


@dataclass(frozen=True, slots=True)
class Preparing(Generic[GenerationT, AccessT]):
    generation: GenerationT
    access: AccessT
    attempt: ManagedAttempt[GenerationT, AccessT]


@dataclass(frozen=True, slots=True)
class Current(Generic[GenerationT, AccessT, ResourceSetT, CapabilityT]):
    generation: GenerationT
    access: AccessT
    capability: CapabilityT
    resource_lease: ResourceLease[AccessT, ResourceSetT]


ManagedState: TypeAlias = (
    Idle[GenerationT]
    | Preparing[GenerationT, AccessT]
    | Current[GenerationT, AccessT, ResourceSetT, CapabilityT]
)


__all__ = ["Current", "Idle", "ManagedAttempt", "ManagedState", "Preparing"]
