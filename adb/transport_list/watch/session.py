from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.transport_list.model import AdbTransportList


@runtime_checkable
class AdbTransportListWatchStream(Protocol):
    """Established low-level watch stream yielding complete transport lists."""

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class AdbTransportListWatchSession(AdbTransportListWatchStream, Protocol):
    """Server-bound watch session for one authoritative ADB server lifetime."""

    @property
    def server(self) -> AdbServerIdentity:
        ...


class _ServerBoundAdbTransportListWatchSession:
    """Bind one low-level watch stream to one runtime-scoped server identity."""

    __slots__ = ("_server", "_stream", "_initial")

    def __init__(
        self,
        server: AdbServerIdentity,
        stream: AdbTransportListWatchStream,
        initial: AdbTransportList,
    ) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(stream, AdbTransportListWatchStream):
            raise TypeError("stream must satisfy AdbTransportListWatchStream")
        if not isinstance(initial, AdbTransportList):
            raise TypeError("initial must be AdbTransportList")
        self._server = server
        self._stream = stream
        self._initial = initial

    @property
    def server(self) -> AdbServerIdentity:
        return self._server

    @property
    def initial(self) -> AdbTransportList:
        return self._initial

    def updates(self) -> Iterator[AdbTransportList]:
        yield from self._stream.updates()

    def close(self) -> None:
        self._stream.close()


def bind_transport_list_watch_session(
    server: AdbServerIdentity,
    stream: AdbTransportListWatchStream,
    initial: AdbTransportList,
) -> AdbTransportListWatchSession:
    """Bind an established raw stream and initial snapshot to one server lifetime."""

    return _ServerBoundAdbTransportListWatchSession(server, stream, initial)


__all__ = [
    "AdbTransportListWatchSession",
    "AdbTransportListWatchStream",
    "bind_transport_list_watch_session",
]
