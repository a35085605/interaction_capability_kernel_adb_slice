from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable


LocalCleanupAttempt = Callable[[], object | None]
ResourceClaimConflict = Callable[[object, object], bool]


@runtime_checkable
class CleanupCompletion(Protocol):
    """Completion callback for one accepted cleanup handoff."""

    def succeed(self) -> None:
        """Confirm cleanup completion; duplicate notifications have no effect."""
        ...


@runtime_checkable
class CleanupHandoff(Protocol):
    """Port for transferring unresolved cleanup responsibility to an outer layer."""

    def accept(self, resource: object, completion: CleanupCompletion) -> None:
        """Accept responsibility for cleanup without implying completion.

        Normal return confirms only that the task was accepted. The implementation must not require
        cleanup to complete before returning. Repeated calls for the same ``completion`` must be
        safe because an unconfirmed acceptance may be retried.
        """
        ...


@dataclass(slots=True)
class _CleanupEntry:
    identity: object
    resource: object
    local_cleanup: LocalCleanupAttempt | None
    claims: tuple[object, ...]
    handoff_resource: object | None = None
    local_attempted: bool = False
    handoff_accepted: bool = False
    processing: bool = False
    handoff_error: str | None = None
    completion: _CleanupCompletion | None = None


class _CleanupCompletion:
    __slots__ = ("_owner", "_entry")

    def __init__(self, owner: CleanupCoordinator, entry: _CleanupEntry) -> None:
        self._owner = owner
        self._entry = entry

    def succeed(self) -> None:
        self._owner._complete(self._entry)


@dataclass(frozen=True, slots=True)
class CleanupSnapshot:
    pending_count: int
    handoff_accepted_count: int
    handoff_errors: tuple[str, ...]


class CleanupCoordinator:
    """Track resource cleanup debt independently from access information.

    Each entry has a stable ownership ``identity``, an actual cleanup resource, and zero or more
    resource claims. Local cleanup is attempted at most once; unresolved payloads may differ from
    the owned resource without changing identity. A successful handoff acceptance does not retire
    either debt or claims. Both remain live until local cleanup or ``CleanupCompletion`` confirms
    completion.

    Claim semantics are intentionally adapter-defined. The coordinator only retains claims and asks
    a supplied predicate whether an existing claim conflicts with a requested claim.
    """

    def __init__(self, handoff: CleanupHandoff) -> None:
        if not isinstance(handoff, CleanupHandoff):
            raise TypeError("handoff must satisfy CleanupHandoff")
        self._handoff = handoff
        self._lock = Lock()
        self._entries: dict[int, _CleanupEntry] = {}

    @staticmethod
    def _normalize_claims(claims: Iterable[object]) -> tuple[object, ...]:
        normalized = tuple(claims)
        if any(claim is None for claim in normalized):
            raise ValueError("resource claims cannot contain None")
        return normalized

    def snapshot(self) -> CleanupSnapshot:
        with self._lock:
            entries = tuple(self._entries.values())
            return CleanupSnapshot(
                pending_count=len(entries),
                handoff_accepted_count=sum(entry.handoff_accepted for entry in entries),
                handoff_errors=tuple(
                    entry.handoff_error
                    for entry in entries
                    if entry.handoff_error is not None
                ),
            )

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def has_conflict(
        self,
        requested_claims: Iterable[object],
        conflicts: ResourceClaimConflict,
    ) -> bool:
        """Return whether pending cleanup retains a claim conflicting with a request."""

        if not callable(conflicts):
            raise TypeError("conflicts must be callable")
        requested = self._normalize_claims(requested_claims)
        if not requested:
            return False
        with self._lock:
            existing = tuple(
                claim
                for entry in self._entries.values()
                for claim in entry.claims
            )
        return any(
            conflicts(existing_claim, requested_claim)
            for existing_claim in existing
            for requested_claim in requested
        )

    def register(
        self,
        resource: object,
        local_cleanup: LocalCleanupAttempt,
        *,
        identity: object | None = None,
        claims: Iterable[object] = (),
    ) -> None:
        """Idempotently record locally-cleanable ownership debt without doing cleanup work."""

        if resource is None:
            raise TypeError("resource cannot be None")
        if not callable(local_cleanup):
            raise TypeError("local_cleanup must be callable")
        stable_identity = resource if identity is None else identity
        if stable_identity is None:
            raise TypeError("identity cannot be None")
        self._register(
            _CleanupEntry(
                identity=stable_identity,
                resource=resource,
                local_cleanup=local_cleanup,
                claims=self._normalize_claims(claims),
            )
        )

    def register_handoff(
        self,
        resource: object,
        *,
        identity: object | None = None,
        claims: Iterable[object] = (),
    ) -> None:
        """Record already-unresolved ownership debt for direct handoff."""

        if resource is None:
            raise TypeError("resource cannot be None")
        stable_identity = resource if identity is None else identity
        if stable_identity is None:
            raise TypeError("identity cannot be None")
        self._register(
            _CleanupEntry(
                identity=stable_identity,
                resource=resource,
                local_cleanup=None,
                claims=self._normalize_claims(claims),
                handoff_resource=resource,
                local_attempted=True,
            )
        )

    def _register(self, entry: _CleanupEntry) -> None:
        with self._lock:
            key = id(entry.identity)
            existing = self._entries.get(key)
            if existing is not None:
                if existing.identity is not entry.identity:
                    raise RuntimeError("cleanup ownership identity collision")
                # Registration callbacks can be retried. Preserve the original cleanup state while
                # retaining any claims that became known before the retry.
                existing.claims = (*existing.claims, *entry.claims)
                return
            entry.completion = _CleanupCompletion(self, entry)
            self._entries[key] = entry

    def process_pending(self) -> None:
        """Advance currently pending cleanup tasks once, without scheduling background work."""

        with self._lock:
            entries = tuple(self._entries.values())
        for entry in entries:
            self._process(entry)

    def _is_current(self, entry: _CleanupEntry) -> bool:
        return self._entries.get(id(entry.identity)) is entry

    def _process(self, entry: _CleanupEntry) -> None:
        with self._lock:
            if not self._is_current(entry):
                return
            if entry.processing or entry.handoff_accepted:
                return
            entry.processing = True
            attempt_local = not entry.local_attempted
            if attempt_local:
                entry.local_attempted = True
            local_cleanup = entry.local_cleanup
            handoff_resource = entry.handoff_resource
            completion = entry.completion

        try:
            if attempt_local:
                assert local_cleanup is not None
                try:
                    handoff_resource = local_cleanup()
                except BaseException:
                    handoff_resource = entry.resource
                if handoff_resource is None:
                    self._complete(entry)
                    return
                with self._lock:
                    if not self._is_current(entry):
                        return
                    entry.handoff_resource = handoff_resource
                    entry.local_cleanup = None

            if handoff_resource is None:
                with self._lock:
                    if not self._is_current(entry):
                        return
                    handoff_resource = entry.handoff_resource
            if handoff_resource is None or completion is None:
                raise RuntimeError("cleanup coordinator state is inconsistent")

            try:
                self._handoff.accept(handoff_resource, completion)
            except BaseException as exc:
                with self._lock:
                    if self._is_current(entry):
                        entry.handoff_error = str(exc).strip() or type(exc).__name__
                return

            with self._lock:
                if self._is_current(entry):
                    entry.handoff_accepted = True
                    entry.handoff_error = None
        finally:
            with self._lock:
                if self._is_current(entry):
                    entry.processing = False

    def _complete(self, entry: _CleanupEntry) -> None:
        """Idempotently retire one ownership and its claims after confirmed cleanup."""

        with self._lock:
            key = id(entry.identity)
            if self._entries.get(key) is entry:
                del self._entries[key]


__all__ = [
    "CleanupCompletion",
    "CleanupCoordinator",
    "CleanupHandoff",
    "CleanupSnapshot",
    "LocalCleanupAttempt",
    "ResourceClaimConflict",
]
