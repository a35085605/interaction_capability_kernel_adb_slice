from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.transport_list.model import AdbTransportList


@runtime_checkable
class AdbTransportListWatchStream(Protocol):
    """Producer-facing stream of complete transport-list snapshots.

    The stream is a single-consumer data-plane capability only. Repeated state reads for one
    committed generation may return this same stream; callers must not consume ``updates()`` from
    multiple consumers. It does not own the physical watch resource and therefore exposes no
    cancellation or cleanup operations. Backend release may retire the underlying resource
    concurrently, ending ``updates()``.
    """

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...


__all__ = ["AdbTransportListWatchStream"]
