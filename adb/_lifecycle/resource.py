from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from threading import Lock


ResourceClaimConflict = Callable[[object, object], bool]


@dataclass(slots=True, eq=False)
class ResourceEntry:
    """One resource registered in the process-wide resource pool."""

    identity: object
    resource: object
    claims: tuple[object, ...]
    retired: bool = False


@dataclass(slots=True)
class _ScopeState:
    entries: dict[int, ResourceEntry] = field(default_factory=dict)
    retired: bool = False
    sealed: bool = False


class ResourcePool:
    """Thread-safe registry that is the single source of truth for owned resources.

    Resources are registered as soon as they are obtained. Retiring a resource records that
    lifecycle authority no longer owns it; retirement does not perform physical cleanup. Retired
    resources remain in the pool (and retain their claims) until a future cleanup implementation
    explicitly discards them after disposal is confirmed.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_scope_id = 1
        self._scopes: dict[int, _ScopeState] = {}
        self._entries: dict[int, ResourceEntry] = {}
        self._entry_scope_ids: dict[int, int] = {}

    @staticmethod
    def _normalize_claims(claims: Iterable[object]) -> tuple[object, ...]:
        normalized = tuple(claims)
        if any(claim is None for claim in normalized):
            raise ValueError("resource claims cannot contain None")
        return normalized

    def open_scope(self) -> ResourceScope:
        with self._lock:
            scope_id = self._next_scope_id
            self._next_scope_id += 1
            self._scopes[scope_id] = _ScopeState()
        return ResourceScope(self, scope_id)

    def snapshot(self) -> tuple[ResourceEntry, ...]:
        """Return a point-in-time snapshot of every resource still retained by the pool."""

        with self._lock:
            return tuple(self._entries.values())

    def has_conflict(
        self,
        requested_claims: Iterable[object],
        conflicts: ResourceClaimConflict,
        *,
        exclude_scope: ResourceScope | None = None,
    ) -> bool:
        """Return whether any retained resource claim conflicts with a requested claim."""

        if not callable(conflicts):
            raise TypeError("conflicts must be callable")
        requested = self._normalize_claims(requested_claims)
        if not requested:
            return False
        exclude_scope_id = None
        if exclude_scope is not None:
            if not isinstance(exclude_scope, ResourceScope):
                raise TypeError("exclude_scope must be ResourceScope or None")
            if exclude_scope.pool is not self:
                raise ValueError("exclude_scope belongs to a different resource pool")
            exclude_scope_id = exclude_scope.scope_id

        with self._lock:
            existing = tuple(
                claim
                for key, entry in self._entries.items()
                if self._entry_scope_ids[key] != exclude_scope_id
                for claim in entry.claims
            )
        return any(
            conflicts(existing_claim, requested_claim)
            for existing_claim in existing
            for requested_claim in requested
        )

    def retire(self, resource: object, *, identity: object | None = None) -> None:
        """Mark one retained resource retired without performing cleanup."""

        stable_identity = resource if identity is None else identity
        if stable_identity is None:
            raise TypeError("identity cannot be None")
        with self._lock:
            entry = self._entries.get(id(stable_identity))
            if entry is None or entry.identity is not stable_identity:
                raise RuntimeError("resource is not registered in this pool")
            entry.retired = True

    def _state(self, scope_id: int) -> _ScopeState:
        state = self._scopes.get(scope_id)
        if state is None:
            raise RuntimeError("resource scope is not registered in this pool")
        return state

    def _register(
        self,
        scope_id: int,
        resource: object,
        *,
        claims: Iterable[object],
        identity: object,
    ) -> ResourceEntry:
        if resource is None:
            raise TypeError("resource cannot be None")
        if identity is None:
            raise TypeError("resource identity cannot be None")
        normalized_claims = self._normalize_claims(claims)

        with self._lock:
            state = self._state(scope_id)
            if state.sealed:
                raise RuntimeError("resource scope is sealed")
            key = id(identity)
            existing = self._entries.get(key)
            if existing is not None:
                if (
                    existing.identity is identity
                    and existing.resource is resource
                    and self._entry_scope_ids[key] == scope_id
                ):
                    return existing
                raise RuntimeError("resource identity is already registered in the resource pool")

            entry = ResourceEntry(
                identity=identity,
                resource=resource,
                claims=normalized_claims,
                retired=state.retired,
            )
            state.entries[key] = entry
            self._entries[key] = entry
            self._entry_scope_ids[key] = scope_id
            return entry

    def _replace_claims(
        self,
        scope_id: int,
        entry: ResourceEntry,
        claims: Iterable[object],
    ) -> None:
        if not isinstance(entry, ResourceEntry):
            raise TypeError("entry must be ResourceEntry")
        normalized_claims = self._normalize_claims(claims)
        with self._lock:
            state = self._state(scope_id)
            if state.sealed:
                raise RuntimeError("resource scope is sealed")
            if state.entries.get(id(entry.identity)) is not entry:
                raise RuntimeError("resource entry is not attached to this scope")
            entry.claims = normalized_claims

    def _retire(self, scope_id: int, entry: ResourceEntry) -> None:
        if not isinstance(entry, ResourceEntry):
            raise TypeError("entry must be ResourceEntry")
        with self._lock:
            state = self._state(scope_id)
            if state.entries.get(id(entry.identity)) is not entry:
                raise RuntimeError("resource entry is not attached to this scope")
            entry.retired = True

    def _retire_resource(self, scope_id: int, resource: object) -> None:
        if resource is None:
            raise TypeError("resource cannot be None")
        with self._lock:
            state = self._state(scope_id)
            entry = state.entries.get(id(resource))
            if entry is None or entry.identity is not resource:
                raise RuntimeError("resource is not registered in this scope")
            entry.retired = True

    def _retire_all(self, scope_id: int) -> None:
        with self._lock:
            state = self._state(scope_id)
            state.retired = True
            for entry in state.entries.values():
                entry.retired = True

    def _discard(self, scope_id: int, entry: ResourceEntry) -> None:
        """Forget one entry after cleanup or ownership transfer has been positively confirmed."""

        if not isinstance(entry, ResourceEntry):
            raise TypeError("entry must be ResourceEntry")
        with self._lock:
            state = self._state(scope_id)
            key = id(entry.identity)
            if state.entries.get(key) is not entry:
                raise RuntimeError("resource entry is not attached to this scope")
            del state.entries[key]
            if self._entries.get(key) is entry:
                del self._entries[key]
                del self._entry_scope_ids[key]

    def _snapshot(self, scope_id: int) -> tuple[ResourceEntry, ...]:
        with self._lock:
            return tuple(self._state(scope_id).entries.values())

    def _claims(self, scope_id: int) -> tuple[object, ...]:
        with self._lock:
            return tuple(
                claim
                for entry in self._state(scope_id).entries.values()
                for claim in entry.claims
            )

    def _seal(self, scope_id: int) -> None:
        with self._lock:
            self._state(scope_id).sealed = True

    def _is_retired(self, scope_id: int) -> bool:
        with self._lock:
            return self._state(scope_id).retired

    def _is_sealed(self, scope_id: int) -> bool:
        with self._lock:
            return self._state(scope_id).sealed


class ResourceScope:
    """Projection of one acquisition scope inside a shared ``ResourcePool``.

    A scope contains no independent ownership set. Every registration goes directly into the pool.
    ``retired`` and ``sealed`` are independent: a draining acquisition is retired but still open,
    so resources that arrive after revocation are registered immediately as retired.
    """

    __slots__ = ("_pool", "_scope_id")

    def __init__(self, pool: ResourcePool, scope_id: int) -> None:
        if not isinstance(pool, ResourcePool):
            raise TypeError("pool must be ResourcePool")
        self._pool = pool
        self._scope_id = scope_id

    @property
    def pool(self) -> ResourcePool:
        return self._pool

    @property
    def scope_id(self) -> int:
        return self._scope_id

    def register(
        self,
        resource: object,
        *,
        claims: Iterable[object] = (),
        identity: object | None = None,
    ) -> ResourceEntry:
        """Register a newly obtained resource directly in the shared pool."""

        return self._pool._register(
            self._scope_id,
            resource,
            claims=claims,
            identity=resource if identity is None else identity,
        )

    def replace_claims(self, entry: ResourceEntry, claims: Iterable[object]) -> None:
        self._pool._replace_claims(self._scope_id, entry, claims)

    def retire(self, entry: ResourceEntry) -> None:
        """Mark one resource retired without performing physical cleanup."""

        self._pool._retire(self._scope_id, entry)

    def retire_resource(self, resource: object) -> None:
        """Mark a default-identity resource in this scope retired."""

        self._pool._retire_resource(self._scope_id, resource)

    def retire_all(self) -> None:
        """Retire this scope and every resource currently or subsequently registered into it."""

        self._pool._retire_all(self._scope_id)

    def discard(self, entry: ResourceEntry) -> None:
        """Remove an entry after synchronous disposal or ownership transfer is confirmed."""

        self._pool._discard(self._scope_id, entry)

    def snapshot(self) -> tuple[ResourceEntry, ...]:
        return self._pool._snapshot(self._scope_id)

    def claims(self) -> tuple[object, ...]:
        return self._pool._claims(self._scope_id)

    def has_conflict(
        self,
        requested_claims: Iterable[object],
        conflicts: ResourceClaimConflict,
    ) -> bool:
        """Check this scope's claims against resources retained by all other scopes."""

        return self._pool.has_conflict(
            requested_claims,
            conflicts,
            exclude_scope=self,
        )

    def seal(self) -> None:
        self._pool._seal(self._scope_id)

    @property
    def retired(self) -> bool:
        return self._pool._is_retired(self._scope_id)

    @property
    def sealed(self) -> bool:
        return self._pool._is_sealed(self._scope_id)


GLOBAL_RESOURCE_POOL = ResourcePool()


__all__ = [
    "GLOBAL_RESOURCE_POOL",
    "ResourceClaimConflict",
    "ResourceEntry",
    "ResourcePool",
    "ResourceScope",
]
