from __future__ import annotations

from threading import Event, Lock


class ResourceAcquisition:
    """Cancellation context for one physical ResourceSet acquisition.

    Partial resources are intentionally a domain concern of ``ResourceLifecycle``.
    The shared pool only sees a ResourceSet after acquisition has completed.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._cancellation = Event()
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

    def revoke(self) -> None:
        """Revoke commit authority and request cancellation of physical acquisition."""

        with self._lock:
            if self._revoked:
                return
            self._revoked = True
            self._cancellation.set()

    def finish(self) -> None:
        """Record that the physical acquisition call has returned or failed."""

        with self._lock:
            self._finished = True


__all__ = ["ResourceAcquisition"]
