from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.transport_list.model import AdbTransportList


@runtime_checkable
class AdbTransportListWatchStream(Protocol):
    """Producer-facing capability for complete transport-list snapshots.

    The capability is a single-consumer data-plane view. It does not own or expose the
    underlying AOSP track-devices session. Releasing the lifecycle closes that physical
    session, which ends or interrupts subsequent update reads.
    """

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...


__all__ = ["AdbTransportListWatchStream"]
