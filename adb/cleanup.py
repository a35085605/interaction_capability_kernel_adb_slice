from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import Event, Lock, Thread
from typing import Protocol, runtime_checkable


CleanupAttempt = Callable[[], object | None]


@runtime_checkable
class CleanupDelegate(Protocol):
    """Fallback cleanup port for resources whose regular cleanup was not confirmed."""

    def cleanup(self, resource: object) -> bool:
        """Return ``True`` only after cleanup of ``resource`` is confirmed.

        ``False`` leaves the resource pending and the background cleanup flow will retry.
        Raising has the same retry semantics as returning ``False``.
        """
        ...


@dataclass(slots=True)
class _CleanupEntry:
    resource: object
    regular_cleanup: CleanupAttempt | None
    conflict_key: object | None
    delegate_resource: object | None = None


@dataclass(frozen=True, slots=True)
class CleanupSnapshot:
    pending_count: int
    worker_scheduled: bool
    start_error: str | None


class BackgroundCleanup:
    """Track retired resources until regular or delegated cleanup is confirmed.

    Each resource is attempted once with backend-provided regular cleanup. When that attempt
    cannot confirm cleanup, the unresolved resource is repeatedly passed to ``CleanupDelegate``
    until it returns ``True``. ``conflict_key`` is backend metadata used only to decide whether a
    new acquisition conflicts with a still-pending resource; unrelated cleanup debt does not block
    acquisition.

    ``register`` only records debt and is suitable for a lifecycle's locked handoff. Call
    ``start_pending`` after releasing that lock. ``submit`` combines those steps for callers
    outside lifecycle transitions. Worker startup failures never undo accepted debt; they are
    exposed by ``snapshot`` and retried by the next ``start_pending`` or submission, including
    duplicate submissions. Lifecycles also retry on their next acquire/release operation.
    """

    def __init__(
        self,
        delegate: CleanupDelegate,
        *,
        retry_interval_seconds: float = 0.05,
    ) -> None:
        if not isinstance(delegate, CleanupDelegate):
            raise TypeError("delegate must satisfy CleanupDelegate")
        if isinstance(retry_interval_seconds, bool) or not isinstance(
            retry_interval_seconds, (int, float)
        ):
            raise TypeError("retry_interval_seconds must be a number")
        retry_interval = float(retry_interval_seconds)
        if not isfinite(retry_interval) or retry_interval <= 0:
            raise ValueError("retry_interval_seconds must be finite and greater than zero")

        self._delegate = delegate
        self._retry_interval_seconds = retry_interval
        self._lock = Lock()
        self._wake = Event()
        self._entries: dict[int, _CleanupEntry] = {}
        self._worker: Thread | None = None
        self._start_error: str | None = None

    def snapshot(self) -> CleanupSnapshot:
        with self._lock:
            return CleanupSnapshot(len(self._entries), self._worker is not None, self._start_error)

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def has_conflict(self, conflict_key: object | None) -> bool:
        """Return whether cleanup debt exists for this acquisition conflict key.

        ``None`` means the backend could not identify an exclusivity domain for that resource;
        unknown cleanup debt is still tracked and retried but does not globally block acquisition.
        """

        if conflict_key is None:
            return False
        with self._lock:
            return any(entry.conflict_key == conflict_key for entry in self._entries.values())

    def submit(
        self,
        resource: object,
        regular_cleanup: CleanupAttempt,
        *,
        conflict_key: object | None = None,
    ) -> None:
        """Accept debt and attempt to start its worker, outside any lifecycle state lock."""

        self.register(resource, regular_cleanup, conflict_key=conflict_key)
        self.start_pending()

    def register(
        self,
        resource: object,
        regular_cleanup: CleanupAttempt,
        *,
        conflict_key: object | None = None,
    ) -> None:
        """Idempotently record debt without starting a thread or running resource cleanup."""

        if resource is None:
            raise TypeError("resource cannot be None")
        if not callable(regular_cleanup):
            raise TypeError("regular_cleanup must be callable")
        self._register(
            _CleanupEntry(
                resource=resource,
                regular_cleanup=regular_cleanup,
                conflict_key=conflict_key,
            )
        )

    def submit_delegated(
        self,
        resource: object,
        *,
        conflict_key: object | None = None,
    ) -> None:
        """Accept delegated debt and attempt to start its worker outside lifecycle locks."""

        self.register_delegated(resource, conflict_key=conflict_key)
        self.start_pending()

    def register_delegated(
        self,
        resource: object,
        *,
        conflict_key: object | None = None,
    ) -> None:
        """Record delegated debt without starting the worker."""

        if resource is None:
            raise TypeError("resource cannot be None")
        self._register(
            _CleanupEntry(
                resource=resource,
                regular_cleanup=None,
                conflict_key=conflict_key,
                delegate_resource=resource,
            )
        )

    def _register(self, entry: _CleanupEntry) -> None:
        with self._lock:
            key = id(entry.resource)
            if key in self._entries:
                return
            self._entries[key] = entry
            self._wake.set()

    def start_pending(self) -> bool:
        """Ensure a worker is scheduled; return False if startup failed.

        A failed start retains all debt and allows a later call to retry. This method does not
        wait for physical cleanup and must be called outside lifecycle state locks. If no later
        operations occur, the recorded failure remains available for a supervisor to inspect and
        retry; no additional retry thread is required when the system cannot start threads.
        """

        with self._lock:
            if not self._entries or self._worker is not None:
                return True
            self._start_error = None
            try:
                worker_to_start = Thread(
                    target=self._run,
                    name="adb-background-cleanup",
                    daemon=True,
                )
            except Exception as exc:
                self._start_error = str(exc)
                return False
            self._worker = worker_to_start

        try:
            worker_to_start.start()
        except BaseException as exc:
            with self._lock:
                # Do not retire a worker that actually started (e.g. an interrupted start()),
                # or overwrite a successor that was scheduled after this worker completed.
                if self._worker is worker_to_start and worker_to_start.ident is None:
                    self._worker = None
                    self._start_error = str(exc)
            if not isinstance(exc, Exception):
                raise
            return False
        return True

    def _run(self) -> None:
        while True:
            with self._lock:
                entries = tuple(self._entries.items())
                if not entries:
                    self._worker = None
                    self._wake.clear()
                    return
                self._wake.clear()

            for key, entry in entries:
                if self._attempt(entry):
                    with self._lock:
                        if self._entries.get(key) is entry:
                            del self._entries[key]

            with self._lock:
                if not self._entries:
                    continue
            self._wake.wait(self._retry_interval_seconds)

    def _attempt(self, entry: _CleanupEntry) -> bool:
        if entry.delegate_resource is None:
            regular_cleanup = entry.regular_cleanup
            if regular_cleanup is None:
                entry.delegate_resource = entry.resource
            else:
                try:
                    unresolved = regular_cleanup()
                except BaseException:
                    unresolved = entry.resource
                if unresolved is None:
                    return True
                entry.delegate_resource = unresolved
                entry.regular_cleanup = None

        try:
            return self._delegate.cleanup(entry.delegate_resource) is True
        except BaseException:
            return False


__all__ = ["BackgroundCleanup", "CleanupAttempt", "CleanupDelegate", "CleanupSnapshot"]
