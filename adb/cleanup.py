from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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


class BackgroundCleanup:
    """Track retired resources until regular or delegated cleanup is confirmed.

    Each resource is attempted once with backend-provided regular cleanup. When that attempt
    cannot confirm cleanup, the unresolved resource is repeatedly passed to ``CleanupDelegate``
    until it returns ``True``. ``conflict_key`` is backend metadata used only to decide whether a
    new acquisition conflicts with a still-pending resource; unrelated cleanup debt does not block
    acquisition.
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
        if retry_interval <= 0:
            raise ValueError("retry_interval_seconds must be greater than zero")

        self._delegate = delegate
        self._retry_interval_seconds = retry_interval
        self._lock = Lock()
        self._wake = Event()
        self._entries: dict[int, _CleanupEntry] = {}
        self._worker: Thread | None = None

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
        """Start one background cleanup flow with a regular-cleanup first stage."""

        if resource is None:
            raise TypeError("resource cannot be None")
        if not callable(regular_cleanup):
            raise TypeError("regular_cleanup must be callable")
        self._submit(
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
        """Track a resource whose regular cleanup was already attempted and unconfirmed."""

        if resource is None:
            raise TypeError("resource cannot be None")
        self._submit(
            _CleanupEntry(
                resource=resource,
                regular_cleanup=None,
                conflict_key=conflict_key,
                delegate_resource=resource,
            )
        )

    def _submit(self, entry: _CleanupEntry) -> None:
        worker_to_start: Thread | None = None
        with self._lock:
            key = id(entry.resource)
            if key in self._entries:
                return
            self._entries[key] = entry
            self._wake.set()
            if self._worker is None:
                worker_to_start = Thread(
                    target=self._run,
                    name="adb-background-cleanup",
                    daemon=True,
                )
                self._worker = worker_to_start

        if worker_to_start is not None:
            worker_to_start.start()

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


__all__ = ["BackgroundCleanup", "CleanupAttempt", "CleanupDelegate"]
