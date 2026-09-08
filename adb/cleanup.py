from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable


LocalCleanupAttempt = Callable[[], object | None]


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
    resource: object
    local_cleanup: LocalCleanupAttempt | None
    conflict_key: object | None
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
    """Track cleanup debt and coordinate one local attempt with an outer handoff.

    Registration is lock-only bookkeeping and is safe to invoke from lifecycle transition callbacks.
    ``process_pending`` must run after the lifecycle lock is released. It attempts each registered
    local cleanup at most once; unresolved resources are offered to ``CleanupHandoff``. A normal
    ``accept`` return transfers processing responsibility but does not retire cleanup debt. Debt is
    removed only when local cleanup is confirmed or the corresponding ``CleanupCompletion`` reports
    success.

    The coordinator owns no worker, timer, retry policy, or external cleanup execution. A failed or
    unconfirmed handoff remains pending and may be offered again by a later ``process_pending`` call.
    Cleanup debt blocks a new acquisition only when its ``conflict_key`` matches that acquisition.
    """

    def __init__(self, handoff: CleanupHandoff) -> None:
        if not isinstance(handoff, CleanupHandoff):
            raise TypeError("handoff must satisfy CleanupHandoff")
        self._handoff = handoff
        self._lock = Lock()
        self._entries: dict[int, _CleanupEntry] = {}

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

    def has_conflict(self, conflict_key: object | None) -> bool:
        """Return whether incomplete cleanup debt exists for this conflict key.

        ``None`` means the backend could not identify an exclusivity domain for that resource;
        unknown cleanup debt remains tracked but does not globally block acquisition.
        """

        if conflict_key is None:
            return False
        with self._lock:
            return any(entry.conflict_key == conflict_key for entry in self._entries.values())

    def register(
        self,
        resource: object,
        local_cleanup: LocalCleanupAttempt,
        *,
        conflict_key: object | None = None,
    ) -> None:
        """Idempotently record cleanup debt without executing cleanup or handoff work."""

        if resource is None:
            raise TypeError("resource cannot be None")
        if not callable(local_cleanup):
            raise TypeError("local_cleanup must be callable")
        self._register(
            _CleanupEntry(
                resource=resource,
                local_cleanup=local_cleanup,
                conflict_key=conflict_key,
            )
        )

    def register_handoff(
        self,
        resource: object,
        *,
        conflict_key: object | None = None,
    ) -> None:
        """Record already-unresolved cleanup debt that should be handed off directly."""

        if resource is None:
            raise TypeError("resource cannot be None")
        self._register(
            _CleanupEntry(
                resource=resource,
                local_cleanup=None,
                conflict_key=conflict_key,
                handoff_resource=resource,
                local_attempted=True,
            )
        )

    def _register(self, entry: _CleanupEntry) -> None:
        with self._lock:
            key = id(entry.resource)
            if key in self._entries:
                return
            entry.completion = _CleanupCompletion(self, entry)
            self._entries[key] = entry

    def process_pending(self) -> None:
        """Advance currently pending cleanup tasks once, without scheduling background work."""

        with self._lock:
            entries = tuple(self._entries.values())
        for entry in entries:
            self._process(entry)

    def _process(self, entry: _CleanupEntry) -> None:
        with self._lock:
            if self._entries.get(id(entry.resource)) is not entry:
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
                    if self._entries.get(id(entry.resource)) is not entry:
                        return
                    entry.handoff_resource = handoff_resource
                    entry.local_cleanup = None

            if handoff_resource is None:
                with self._lock:
                    if self._entries.get(id(entry.resource)) is not entry:
                        return
                    handoff_resource = entry.handoff_resource
            if handoff_resource is None or completion is None:
                raise RuntimeError("cleanup coordinator state is inconsistent")

            try:
                self._handoff.accept(handoff_resource, completion)
            except BaseException as exc:
                with self._lock:
                    if self._entries.get(id(entry.resource)) is entry:
                        entry.handoff_error = str(exc).strip() or type(exc).__name__
                return

            with self._lock:
                if self._entries.get(id(entry.resource)) is entry:
                    entry.handoff_accepted = True
                    entry.handoff_error = None
        finally:
            with self._lock:
                if self._entries.get(id(entry.resource)) is entry:
                    entry.processing = False

    def _complete(self, entry: _CleanupEntry) -> None:
        """Idempotently retire one task only if this exact entry is still current."""

        with self._lock:
            key = id(entry.resource)
            if self._entries.get(key) is entry:
                del self._entries[key]


__all__ = [
    "CleanupCompletion",
    "CleanupCoordinator",
    "CleanupHandoff",
    "CleanupSnapshot",
    "LocalCleanupAttempt",
]
