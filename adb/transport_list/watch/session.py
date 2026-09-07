from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.transport_list.model import AdbTransportList


@runtime_checkable
class AdbTransportListWatchSession(Protocol):
    """Established watch resources lifetime-owned by the backend.

    Producers may consume ``initial`` and ``updates()`` while the acquisition remains
    authoritative, but receiving the session does not transfer physical lifetime ownership.
    The backend may call ``cancel()`` concurrently with a blocking update read after logical
    revocation. ``cancel()`` must therefore be thread-safe, idempotent, and non-blocking.
    ``close()`` performs idempotent final cleanup and is also used for rollback-only sessions
    that never became visible to a producer.
    """

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...

    def cancel(self) -> None:
        """Request retirement and interrupt any active blocking read."""
        ...

    def close(self) -> None:
        """Perform idempotent final physical cleanup."""
        ...


__all__ = ["AdbTransportListWatchSession"]
