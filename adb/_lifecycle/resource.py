from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import Lock


ResourceCleanupAttempt = Callable[[], object | None]


@dataclass(slots=True, eq=False)
class ResourceOwnership:
    """One owned resource and the claims retained until its cleanup is confirmed.

    ``identity`` is stable for the ownership lifetime and is deliberately separate from both the
    resource exposed to local cleanup and any unresolved handoff payload produced by that cleanup.
    """

    identity: object
    resource: object
    local_cleanup: ResourceCleanupAttempt | None
    claims: tuple[object, ...]
    handoff_only: bool = False


class ResourceScope:
    """Thread-safe ownership set attached to one lifecycle acquisition attempt.

    Adapters add ownership as soon as resource-producing operations return. A scope remains mutable
    while an acquisition is in flight, including after revocation while it is draining. It is sealed
    when that attempt commits or returns to the lifecycle state machine.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._ownerships: dict[int, ResourceOwnership] = {}
        self._sealed = False

    @staticmethod
    def _normalize_claims(claims: Iterable[object]) -> tuple[object, ...]:
        normalized = tuple(claims)
        if any(claim is None for claim in normalized):
            raise ValueError("resource claims cannot contain None")
        return normalized

    def adopt(
        self,
        resource: object,
        local_cleanup: ResourceCleanupAttempt,
        *,
        claims: Iterable[object] = (),
        identity: object | None = None,
    ) -> ResourceOwnership:
        """Record locally cleanable ownership and return its stable ownership record."""

        if resource is None:
            raise TypeError("resource cannot be None")
        if not callable(local_cleanup):
            raise TypeError("local_cleanup must be callable")
        return self._adopt(
            resource,
            local_cleanup,
            claims=claims,
            identity=resource if identity is None else identity,
            handoff_only=False,
        )

    def adopt_handoff(
        self,
        resource: object,
        *,
        claims: Iterable[object] = (),
        identity: object | None = None,
    ) -> ResourceOwnership:
        """Record already-unresolved ownership that must be handed off without a local attempt."""

        if resource is None:
            raise TypeError("resource cannot be None")
        return self._adopt(
            resource,
            None,
            claims=claims,
            identity=resource if identity is None else identity,
            handoff_only=True,
        )

    def _adopt(
        self,
        resource: object,
        local_cleanup: ResourceCleanupAttempt | None,
        *,
        claims: Iterable[object],
        identity: object,
        handoff_only: bool,
    ) -> ResourceOwnership:
        if identity is None:
            raise TypeError("resource identity cannot be None")
        normalized_claims = self._normalize_claims(claims)
        ownership = ResourceOwnership(
            identity=identity,
            resource=resource,
            local_cleanup=local_cleanup,
            claims=normalized_claims,
            handoff_only=handoff_only,
        )
        with self._lock:
            if self._sealed:
                raise RuntimeError("resource scope is sealed")
            key = id(identity)
            existing = self._ownerships.get(key)
            if existing is not None:
                if existing.identity is identity and existing.resource is resource:
                    return existing
                raise RuntimeError("resource ownership identity is already present in this scope")
            self._ownerships[key] = ownership
        return ownership

    def replace_claims(
        self,
        ownership: ResourceOwnership,
        claims: Iterable[object],
    ) -> None:
        """Replace claims while ownership is still attached to this open scope."""

        if not isinstance(ownership, ResourceOwnership):
            raise TypeError("ownership must be ResourceOwnership")
        normalized_claims = self._normalize_claims(claims)
        with self._lock:
            if self._sealed:
                raise RuntimeError("resource scope is sealed")
            current = self._ownerships.get(id(ownership.identity))
            if current is not ownership:
                raise RuntimeError("resource ownership is not attached to this scope")
            ownership.claims = normalized_claims

    def release(self, ownership: ResourceOwnership) -> None:
        """Forget ownership after adapter-local cleanup has been positively confirmed."""

        if not isinstance(ownership, ResourceOwnership):
            raise TypeError("ownership must be ResourceOwnership")
        with self._lock:
            if self._sealed:
                raise RuntimeError("resource scope is sealed")
            key = id(ownership.identity)
            if self._ownerships.get(key) is ownership:
                del self._ownerships[key]

    def snapshot(self) -> tuple[ResourceOwnership, ...]:
        with self._lock:
            return tuple(self._ownerships.values())

    def claims(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(
                claim
                for ownership in self._ownerships.values()
                for claim in ownership.claims
            )

    def seal(self) -> None:
        with self._lock:
            self._sealed = True

    @property
    def sealed(self) -> bool:
        with self._lock:
            return self._sealed


__all__ = [
    "ResourceCleanupAttempt",
    "ResourceOwnership",
    "ResourceScope",
]
