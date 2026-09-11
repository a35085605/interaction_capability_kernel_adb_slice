from __future__ import annotations

from collections.abc import Iterable
from threading import Event, Lock

from adb._resource.pool import ResourceEntry, ResourcePool


class ResourceAcquisition:
    """One physical-resource acquisition session.

    ``revoke()`` retires current registrations and makes every later registration
    immediately retired. ``finish()`` seals the session against future registration.
    """

    def __init__(self, pool: ResourcePool) -> None:
        if not isinstance(pool, ResourcePool):
            raise TypeError("pool must be ResourcePool")
        self._pool = pool
        self._lock = Lock()
        self._cancellation = Event()
        self._entries: list[ResourceEntry] = []
        self._revoked = False
        self._finished = False

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

    def register(
        self,
        resource: object,
        *,
        claims: Iterable[object] = (),
        identity: object | None = None,
    ) -> ResourceEntry:
        stable_identity = resource if identity is None else identity
        with self._lock:
            if self._finished:
                raise RuntimeError("resource acquisition is finished")
            for existing in self._entries:
                if existing.identity is stable_identity and existing.resource is resource:
                    return existing
            entry = self._pool._register(
                resource,
                claims=claims,
                identity=stable_identity,
                retired=self._revoked,
            )
            self._entries.append(entry)
            return entry

    def revoke(self) -> None:
        """Revoke this session; current and future registrations become retired."""

        with self._lock:
            if self._revoked:
                return
            self._revoked = True
            self._cancellation.set()
            entries = tuple(self._entries)
        for entry in entries:
            self._pool.retire(entry)

    def finish(self) -> None:
        """Seal the acquisition session against future registrations."""

        with self._lock:
            self._finished = True

    def snapshot(self) -> tuple[ResourceEntry, ...]:
        with self._lock:
            return tuple(self._entries)


__all__ = ["ResourceAcquisition"]
