from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Iterable, Protocol


@dataclass(slots=True, eq=False)
class ResourceEntry:
    """One physical resource retained by the shared pool."""

    identity: object
    resource: object
    claims: tuple[object, ...] = ()
    retired: bool = False


@dataclass(frozen=True, slots=True)
class ResourceBinding:
    """Managed-only record of which pool entries currently satisfy one Access."""

    entries: tuple[ResourceEntry, ...]


class ResourcePoolView(Protocol):
    """Read-only surface exposed to adapter resolution logic."""

    def snapshot(self) -> tuple[ResourceEntry, ...]: ...


class ResourcePool:
    """Shared registry of physical resources and active Managed bindings.

    The pool owns retention/accounting only. It does not know Access or Capability.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[int, ResourceEntry] = {}
        self._usage: dict[int, int] = {}

    def snapshot(self) -> tuple[ResourceEntry, ...]:
        with self._lock:
            return tuple(self._entries.values())

    def bind(self, entries: Iterable[ResourceEntry]) -> ResourceBinding:
        selected = tuple(entries)
        if not selected:
            raise ValueError("binding must contain at least one resource entry")
        if len({id(entry) for entry in selected}) != len(selected):
            raise ValueError("binding cannot contain duplicate entries")

        with self._lock:
            for entry in selected:
                self._require_retained(entry)
                if entry.retired:
                    raise RuntimeError("cannot bind a retired resource entry")
            for entry in selected:
                key = id(entry.identity)
                self._usage[key] = self._usage.get(key, 0) + 1

        return ResourceBinding(selected)

    def unbind(self, binding: ResourceBinding) -> tuple[ResourceEntry, ...]:
        """Drop one Managed demand and return zero-usage cleanup candidates."""

        if not isinstance(binding, ResourceBinding):
            raise TypeError("binding must be ResourceBinding")

        cleanup_candidates: list[ResourceEntry] = []
        with self._lock:
            for entry in binding.entries:
                self._require_retained(entry)
                key = id(entry.identity)
                usage = self._usage.get(key, 0)
                if usage <= 0:
                    raise RuntimeError("resource entry is not currently bound")
                next_usage = usage - 1
                self._usage[key] = next_usage
                if next_usage == 0:
                    entry.retired = True
                    cleanup_candidates.append(entry)
        return tuple(cleanup_candidates)

    def retire(self, entry: ResourceEntry) -> None:
        with self._lock:
            self._require_retained(entry)
            entry.retired = True

    def discard(self, entry: ResourceEntry) -> None:
        """Forget an entry only after physical cleanup has been confirmed."""

        with self._lock:
            self._require_retained(entry)
            key = id(entry.identity)
            if not entry.retired:
                raise RuntimeError("resource entry must be retired before discard")
            if self._usage.get(key, 0) != 0:
                raise RuntimeError("cannot discard a resource entry that is still bound")
            del self._entries[key]
            self._usage.pop(key, None)

    def usage(self, entry: ResourceEntry) -> int:
        with self._lock:
            self._require_retained(entry)
            return self._usage.get(id(entry.identity), 0)

    def _register(
        self,
        resource: object,
        *,
        claims: Iterable[object],
        identity: object,
        retired: bool,
    ) -> ResourceEntry:
        if resource is None:
            raise TypeError("resource cannot be None")
        if identity is None:
            raise TypeError("identity cannot be None")
        normalized_claims = tuple(claims)
        if any(claim is None for claim in normalized_claims):
            raise ValueError("resource claims cannot contain None")

        key = id(identity)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                raise RuntimeError("resource identity is already registered")
            entry = ResourceEntry(
                identity=identity,
                resource=resource,
                claims=normalized_claims,
                retired=retired,
            )
            self._entries[key] = entry
            self._usage[key] = 0
            return entry

    def _require_retained(self, entry: ResourceEntry) -> None:
        if not isinstance(entry, ResourceEntry):
            raise TypeError("entry must be ResourceEntry")
        retained = self._entries.get(id(entry.identity))
        if retained is not entry:
            raise RuntimeError("resource entry is not retained by this pool")


GLOBAL_RESOURCE_POOL = ResourcePool()


__all__ = [
    "GLOBAL_RESOURCE_POOL",
    "ResourceBinding",
    "ResourceEntry",
    "ResourcePool",
    "ResourcePoolView",
]
